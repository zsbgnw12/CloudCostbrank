"""Base collector abstract class."""

from abc import ABC, abstractmethod


class BaseCollector(ABC):

    @abstractmethod
    def collect_billing(
        self,
        secret_data: dict,
        config: dict,
        start_date: str,  # YYYY-MM-DD
        end_date: str,    # YYYY-MM-DD
    ) -> list[dict]:
        """
        Return a list of normalized billing rows:
        {
            "date": "2026-03-15",
            "provider": "aws",
            "project_id": "123456789012",
            "project_name": "my-project",
            "product": "AmazonEC2",
            "usage_type": "USW2-BoxUsage:m5.large",
            "region": "us-west-2",
            "cost": 12.345678,
            "usage_quantity": 24.0,
            "usage_unit": "Hrs",
            "currency": "USD",
            "tags": {},
            "additional_info": {}
        }
        """
        pass

    @abstractmethod
    def collect_resources(
        self,
        secret_data: dict,
        config: dict,
    ) -> list[dict]:
        """Return resource inventory list (optional)."""
        pass
