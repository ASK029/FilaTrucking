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
    alert_id: str
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
        self.base_url = (base_url or os.environ.get("GOMOTIVE_BASE_URL") or "https://api.gomotive.com/v1").rstrip("/")
        self.api_key = api_key or os.environ.get("GOMOTIVE_API_KEY") or ""
        self.timeout = timeout

        if not self.api_key:
            logger.warning("GoMotiveClient initialized without API key. Falling back to dynamic settings if available.")

    def _headers(self) -> Dict[str, str]:
        return {
            "x-api-key": f"{self.api_key}",
            "Accept": "application/json",
        }

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Optional[Any]:
        if requests is None:
            logger.error("requests library is not available; cannot call GoMotive API.")
            return None

        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            resp = requests.get(url, headers=self._headers(), params=params, timeout=self.timeout)  # type: ignore[arg-type]
            if resp.status_code == 401:
                logger.error("GoMotive API: 401 Unauthorized. Check your API key.")
                return None
            if resp.status_code != 200:
                logger.warning("GoMotive API %s returned %s: %s", url, resp.status_code, resp.text)
                return None
            return resp.json()
        except Exception:
            logger.exception("Error calling GoMotive API at %s", url)
            return None

    def test_connection(self) -> bool:
        """Simple check to see if the API key is valid by fetching the vehicles list."""
        # The Motive API vehicles endpoint is a good place to test connectivity
        resp = self._get("vehicles")
        return resp is not None

    def fetch_vehicles(self) -> List[Dict[str, Any]]:
        """Fetch list of all vehicles from Motive."""
        data = self._get("vehicles")
        if not data or not isinstance(data, dict):
            return []
        return data.get("vehicles") or []

    def fetch_vehicle(self, gomotive_id: str) -> Optional[Dict[str, Any]]:
        """Fetch details for a single vehicle."""
        return self._get(f"vehicles/{gomotive_id}")

    def fetch_odometer_reading(self, gomotive_id: str) -> Optional[GoMotiveOdometer]:
        """Fetch latest odometer reading for a single vehicle.
        
        Searches the /vehicle_locations or /vehicles payload.
        """
        data = self._get("vehicle_locations")
        if not data or not isinstance(data, dict):
            return None

        vehicles_array = data.get("vehicles", [])
        for item in vehicles_array:
            vehicle_data = item.get("vehicle", {})
            if str(vehicle_data.get("id")) == str(gomotive_id):
                loc = vehicle_data.get("current_location")
                if loc and isinstance(loc, dict):
                    value = loc.get("odometer")
                    try:
                        if value is not None:
                            return GoMotiveOdometer(vehicle_gomotive_id=gomotive_id, odometer=int(float(value)))
                    except (TypeError, ValueError):
                        logger.warning("Invalid odometer value for GoMotive vehicle %s: %r", gomotive_id, loc)
                return None
        return None

    def fetch_maintenance_alerts(self, gomotive_id: str) -> List[GoMotiveMaintenanceAlert]:
        """Fetch maintenance alerts/fault codes for a single vehicle."""
        data = self._get("fault_codes", params={"vehicle_id": gomotive_id}) or {}
        items = data.get("fault_codes") or []

        alerts: List[GoMotiveMaintenanceAlert] = []
        for item_wrapper in items:
            try:
                fc = item_wrapper.get("fault_code", {})
                if not fc:
                    continue
                v_data = fc.get("vehicle", {})
                if str(v_data.get("id")) != str(gomotive_id):
                    continue
                    
                label = fc.get("code_label") or fc.get("code") or "Unknown Fault"
                desc = fc.get("fmi_description") or "Check engine fault code."
                alerts.append(
                    GoMotiveMaintenanceAlert(
                        alert_id=str(fc.get("id") or ""),
                        vehicle_gomotive_id=gomotive_id,
                        mileage_at_service=None,
                        next_service_mileage=None,
                        description=f"{label}: {desc}",
                        service_type="Fault Code",
                    )
                )
            except (TypeError, ValueError):
                logger.warning("Skipping invalid maintenance alert for %s: %r", gomotive_id, item_wrapper)
        return alerts


def get_client() -> GoMotiveClient:
    """Factory to create a client with environment or database configuration."""
    try:
        from shipments.models import SystemSettings
        settings = SystemSettings.get_instance()
        return GoMotiveClient(
            base_url=settings.motive_base_url,
            api_key=settings.get_motive_api_key()
        )
    except Exception:
        return GoMotiveClient()

