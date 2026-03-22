from __future__ import annotations

from typing import List

from celery import shared_task
from django.db import transaction

from .gomotive_client import get_client
from .models import Maintenance, Vehicle


@shared_task
def sync_gomotive_data() -> int:
    """Nightly sync of odometer readings and maintenance alerts from GoMotive.

    Returns the number of vehicles successfully updated.
    """
    client = get_client()
    vehicles: List[Vehicle] = list(
        Vehicle.objects.filter(gomotive_id__isnull=False).exclude(gomotive_id="")
    )

    updated_count = 0

    for vehicle in vehicles:
        gomotive_id = vehicle.gomotive_id
        if not gomotive_id:
            continue

        odometer = client.fetch_odometer_reading(gomotive_id)
        alerts = client.fetch_maintenance_alerts(gomotive_id)

        with transaction.atomic():
            if odometer is not None:
                vehicle.current_odometer = odometer.odometer
                vehicle.save(update_fields=["current_odometer"])

            for alert in alerts:
                if alert.alert_id:
                    Maintenance.objects.update_or_create(
                        gomotive_alert_id=alert.alert_id,
                        defaults={
                            'vehicle': vehicle,
                            'cost': 0,
                            'service_provider': "GoMotive",
                            'type': alert.service_type,
                            'description': alert.description,
                            'mileage_at_service': alert.mileage_at_service or (vehicle.current_odometer or 0),
                            'next_service_mileage': alert.next_service_mileage,
                        }
                    )
                else:
                    # Fallback for old/empty IDs or if API doesn't provide one
                    Maintenance.objects.create(
                        vehicle=vehicle,
                        cost=0,
                        service_provider="GoMotive",
                        type=alert.service_type,
                        description=alert.description,
                        mileage_at_service=alert.mileage_at_service or (vehicle.current_odometer or 0),
                        next_service_mileage=alert.next_service_mileage,
                    )

        if odometer is not None or alerts:
            updated_count += 1

    return updated_count

