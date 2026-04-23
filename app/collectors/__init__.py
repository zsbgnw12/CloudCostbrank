from app.collectors.base import BaseCollector
from app.collectors.gcp_collector import GCPCollector
from app.collectors.aws_collector import AWSCollector
from app.collectors.azure_collector import AzureCollector
from app.collectors.taiji_collector import TaijiCollector

_COLLECTORS = {
    "gcp": GCPCollector(),
    "aws": AWSCollector(),
    "azure": AzureCollector(),
    "taiji": TaijiCollector(),
}


def get_collector(provider: str) -> BaseCollector:
    collector = _COLLECTORS.get(provider)
    if not collector:
        raise ValueError(f"Unknown provider: {provider}")
    return collector
