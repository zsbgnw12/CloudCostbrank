#!/usr/bin/env bash
# Local dev setup — install deps and bring DB schema up to head.
# Phase 1 removed `Base.metadata.create_all` from app startup, so alembic
# is now the only path to creating/updating schema.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

pip install -r requirements.txt
alembic upgrade head
