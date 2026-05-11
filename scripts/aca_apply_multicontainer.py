#!/usr/bin/env python3
"""
Patch Azure Container App JSON export for: API + Celery worker + Celery beat (single replica).

Usage:
  az containerapp show -g CloudCost -n cloudcost-brank -o json \
    | python scripts/aca_apply_multicontainer.py [--image <full-image-ref>] > /tmp/patch.yaml
  az containerapp update -g CloudCost -n cloudcost-brank --yaml /tmp/patch.yaml

--image：可选；不传则沿用线上当前镜像（仅刷 command/probes/scale 配置）。
         CICD 里传入新构建的镜像 tag，让镜像与 command 在同一次 update 里原子刷新。

Requires: PyYAML (pip install -r scripts/requirements-aca.txt)
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import sys


def _strip_readonly(props: dict) -> None:
    for k in (
        "latestRevisionFqdn",
        "latestRevisionName",
        "latestReadyRevisionName",
        "runningStatus",
        "provisioningState",
        "outboundIpAddresses",
        "eventStreamEndpoint",
        "customDomainVerificationId",
    ):
        props.pop(k, None)
    ing = props.get("configuration", {}).get("ingress")
    if isinstance(ing, dict):
        ing.pop("fqdn", None)


def _build_containers(base_env: list, image: str) -> list[dict]:
    """Three containers: api (uvicorn), celery-worker, celery-beat."""
    def env_block():
        return copy.deepcopy(base_env)

    api = {
        "name": "api",
        "image": image,
        # 启动顺序：alembic upgrade head → uvicorn。
        # - alembic 幂等：schema 已是最新就是 no-op，几百毫秒返回；
        # - 只在 api 容器跑，celery worker/beat 不参与（避免三容器并发跑迁移竞锁）；
        # - 用 `python -m alembic` 而非裸 `alembic`：前者把 cwd 加进 sys.path，
        #   env.py 的 `from app.database import Base` 才能解析；alembic.ini 里
        #   也加了 prepend_sys_path = . 兜底，双保险；
        # - 用 exec 替换 shell 进程，让 SIGTERM 正确转到 uvicorn 而非 sh，
        #   优雅停机才不会被强杀。
        "command": [
            "/bin/sh",
            "-c",
            "python -m alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000",
        ],
        "env": env_block(),
        "probes": [
            {
                "type": "Liveness",
                "failureThreshold": 3,
                "periodSeconds": 10,
                "successThreshold": 1,
                "timeoutSeconds": 5,
                "tcpSocket": {"port": 8000},
            },
            {
                "type": "Readiness",
                "failureThreshold": 48,
                "periodSeconds": 5,
                "successThreshold": 1,
                "timeoutSeconds": 5,
                "tcpSocket": {"port": 8000},
            },
            {
                "type": "Startup",
                "failureThreshold": 240,
                "initialDelaySeconds": 1,
                "periodSeconds": 1,
                "successThreshold": 1,
                "timeoutSeconds": 3,
                "tcpSocket": {"port": 8000},
            },
        ],
        "resources": {"cpu": 1.0, "memory": "2Gi", "ephemeralStorage": "8Gi"},
    }

    worker = {
        "name": "celery-worker",
        "image": image,
        "command": ["celery"],
        "args": ["-A", "tasks.celery_app", "worker", "-l", "info", "-c", "2"],
        "env": env_block(),
        "resources": {"cpu": 2.0, "memory": "4Gi", "ephemeralStorage": "8Gi"},
    }

    beat = {
        "name": "celery-beat",
        "image": image,
        "command": ["celery"],
        "args": ["-A", "tasks.celery_app", "beat", "-l", "info"],
        "env": env_block(),
        "resources": {"cpu": 0.5, "memory": "1Gi", "ephemeralStorage": "4Gi"},
    }

    return [api, worker, beat]


def patch_resource(data: dict, image_override: str | None = None) -> dict:
    out = copy.deepcopy(data)
    out.pop("id", None)
    out.pop("systemData", None)

    props = out.get("properties")
    if not isinstance(props, dict):
        raise SystemExit("Invalid export: missing properties")

    _strip_readonly(props)

    tmpl = props.setdefault("template", {})
    containers = tmpl.get("containers")
    if not containers:
        raise SystemExit("Invalid export: no template.containers")

    first = containers[0]
    # 选镜像优先级：CLI --image > 线上当前镜像。CICD 必传 --image，把新构建好的
    # ACR tag 灌进来，保证 command/env 配置和镜像在一次 az update 里原子推到生产。
    image = image_override or first.get("image")
    if not image:
        raise SystemExit("No image provided (use --image) and current container has none")
    env = first.get("env") or []

    tmpl["containers"] = _build_containers(env, image)

    suffix = dt.datetime.now(dt.timezone.utc).strftime("mc-%Y%m%d%H%M")
    tmpl["revisionSuffix"] = suffix

    # min=max=1 keeps a single Beat; HTTP rule remains but cannot scale past maxReplicas
    tmpl["scale"] = {
        "minReplicas": 1,
        "maxReplicas": 1,
        "cooldownPeriod": 300,
        "pollingInterval": 30,
        "rules": [
            {
                "name": "http-scaler",
                "http": {"metadata": {"concurrentRequests": "100"}},
            }
        ],
    }

    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image",
        help="完整镜像引用，如 acr.io/cloudcost:20260511-abc1234；省略则沿用线上当前镜像",
    )
    args = parser.parse_args()

    try:
        import yaml
    except ImportError:
        print(
            "PyYAML is required: pip install -r scripts/requirements-aca.txt",
            file=sys.stderr,
        )
        raise SystemExit(1)

    data = json.load(sys.stdin)
    patched = patch_resource(data, image_override=args.image)
    # default_flow_style=False for readability; Azure accepts this
    yaml.safe_dump(
        patched,
        sys.stdout,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
        width=120,
    )


if __name__ == "__main__":
    main()
