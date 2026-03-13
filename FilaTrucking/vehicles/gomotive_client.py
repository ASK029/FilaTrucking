from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError:  # pragma: no cover - requests is expected to be installed
    requests = None  # type: ignore

logger = logging.getLogger(__name__)


@dataclass
class GoMotiveOdometer:
    vehicle_gomotive_id: str
    odometer: int


@dataclass
class GoMotiveMaintenanceAlert:
    vehicle_gomotive_id: str
    mileage_at_service: Optional[int]
    next_service_mileage: Optional[int]
    description: str
    service_type: str


class GoMotiveClient:
    """Thin wrapper around the GoMotive REST API.

    The exact API surface can be adjusted once real credentials and endpoints
    are available; this client focuses on the minimal operations we need:
    - Fetch latest odometer readings
    - Fetch maintenance alerts / upcoming service recommendations
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: int = 10,
    ) -> None:
        self.base_url = (base_url or os.environ.get("GOMOTIVE_BASE_URL") or "").rstrip("/")
        self.api_key = api_key or os.environ.get("GOMOTIVE_API_KEY") or ""
        self.timeout = timeout

        if not self.base_url or not self.api_key:
            logger.warning("GoMotiveClient initialized without base URL or API key.")

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        if requests is None:
            logger.error("requests library is not available; cannot call GoMotive API.")
            return None

        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            resp = requests.get(url, headers=self._headers(), params=params, timeout=self.timeout)  # type: ignore[arg-type]
            if resp.status_code != 200:
                logger.warning("GoMotive API %s returned %s", url, resp.status_code)
                return None
            return resp.json()
        except Exception:
            logger.exception("Error calling GoMotive API at %s", url)
            return None

    def fetch_odometer_reading(self, gomotive_id: str) -> Optional[GoMotiveOdometer]:
        """Fetch latest odometer reading for a single vehicle."""
        data = self._get(f"vehicles/{gomotive_id}/odometer")
        if not data:
            return None

        # Shape of response can be adapted later; assume {"odometer": <int>}
        try:
            value = int(data.get("odometer"))
        except (TypeError, ValueError):
            logger.warning("Invalid odometer value for GoMotive vehicle %s: %r", gomotive_id, data)
            return None

        return GoMotiveOdometer(vehicle_gomotive_id=gomotive_id, odometer=value)

    def fetch_maintenance_alerts(self, gomotive_id: str) -> List[GoMotiveMaintenanceAlert]:
        """Fetch maintenance alerts for a single vehicle."""
        data = self._get(f"vehicles/{gomotive_id}/maintenance-alerts") or {}
        items = data.get("alerts") or []

        alerts: List[GoMotiveMaintenanceAlert] = []
        for item in items:
            try:
                mileage_at_service = item.get("mileage_at_service")
                next_service_mileage = item.get("next_service_mileage")
                alerts.append(
                    GoMotiveMaintenanceAlert(
                        vehicle_gomotive_id=gomotive_id,
                        mileage_at_service=int(mileage_at_service) if mileage_at_service is not None else None,
                        next_service_mileage=int(next_service_mileage) if next_service_mileage is not None else None,
                        description=str(item.get("description") or ""),
                        service_type=str(item.get("service_type") or "Service"),
                    )
                )
            except (TypeError, ValueError):
                logger.warning("Skipping invalid maintenance alert for %s: %r", gomotive_id, item)
        return alerts


def get_client() -> GoMotiveClient:
    """Factory to create a client with environment configuration."""
    return GoMotiveClient()

