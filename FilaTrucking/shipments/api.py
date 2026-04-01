import json
import re
import requests
from datetime import datetime
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_http_methods
from django.utils.dateparse import parse_date

from .models import (
    WhatsAppMessage, Shipment, ShipmentStatus,
    WhatsAppConfig, WhatsAppGroup, WhatsAppLog
)
from customers.models import Customer
from drivers.models import Driver
from vehicles.models import Vehicle

WHATSAPP_SECRET = getattr(settings, 'WHATSAPP_INGEST_SECRET', 'fila_secret_2026')
WHATSAPP_SIDECAR_URL = getattr(settings, 'WHATSAPP_SIDECAR_URL', 'http://localhost:3001')

@csrf_exempt
@require_POST
def ingest_whatsapp_message(request):
    auth_header = request.headers.get("Authorization", "")
    expected_header = f"Bearer {WHATSAPP_SECRET}"
    if not auth_header or auth_header != expected_header:
        return JsonResponse({"error": "Unauthorized"}, status=401)
    
    try:
        data = json.loads(request.body)
        raw_text = data.get("text", "")
        sender_phone = data.get("sender", "")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    if not raw_text:
        return JsonResponse({"error": "No text provided"}, status=400)
    
    msg_log = WhatsAppMessage.objects.create(
        raw_text=raw_text,
        sender_phone=sender_phone
    )
    
    # Parser rules
    lines = raw_text.strip().split('\n')
    parsed_data = {}
    for line in lines:
        if ':' in line:
            key, val = line.split(':', 1)
            parsed_data[key.strip().lower()] = val.strip()

    # Required keys mapping
    required_keys = ['date', 'booking', 'container', 'seal', 'customer', 'rate', 'driver', 'truck']
    missing_keys = [k for k in required_keys if k not in parsed_data or not parsed_data[k]]

    if missing_keys:
        msg_log.is_flagged = True
        msg_log.error_message = f"Missing required fields: {', '.join(missing_keys)}"
        msg_log.save()
        return JsonResponse({
            "status": "flagged", 
            "message": "Missing fields, flagged for manual review.",
            "message_id": msg_log.id
        })

    # Data transformation
    try:
        # Date parsing (expecting MM/DD/YY or YYYY-MM-DD or MM/DD/YYYY)
        # We will try a few formats
        date_str = parsed_data['date']
        parsed_date_obj = None
        for fmt in ('%m/%d/%y', '%m/%d/%Y', '%Y-%m-%d'):
            try:
                parsed_date_obj = datetime.strptime(date_str, fmt).date()
                break
            except ValueError:
                continue
        if not parsed_date_obj:
            raise ValueError(f"Invalid date format: {date_str}. Expected MM/DD/YY")

        # FK Lookups
        customer_name = parsed_data['customer']
        customer = Customer.objects.filter(abbreviation__iexact=customer_name).first()
        if not customer:
            customer = Customer.objects.filter(name__icontains=customer_name).first()
        if not customer:
            raise ValueError(f"Customer not found: {customer_name}")

        driver_name = parsed_data['driver']
        driver = Driver.objects.filter(name__icontains=driver_name).first()
        if not driver:
            raise ValueError(f"Driver not found: {driver_name}")

        truck_plate = parsed_data['truck']
        vehicle = Vehicle.objects.filter(registration_number__icontains=truck_plate).first()
        if not vehicle:
            vehicle = Vehicle.objects.filter(name__icontains=truck_plate).first()
        if not vehicle:
            raise ValueError(f"Vehicle not found: {truck_plate}")

        # Duplicate checking
        container_no = parsed_data['container']
        month_start = parsed_date_obj.replace(day=1)
        next_month = (month_start.month % 12) + 1
        year_next = month_start.year + (month_start.month // 12)
        month_end = month_start.replace(year=year_next, month=next_month)
        
        duplicates = Shipment.objects.filter(
            container__iexact=container_no,
            date__gte=month_start,
            date__lt=month_end
        )
        is_duplicate = duplicates.exists()

        rate_val = re.sub(r'[^\d.]', '', parsed_data['rate'])
        
        shipment = Shipment.objects.create(
            date=parsed_date_obj,
            booking=parsed_data['booking'],
            container=container_no,
            seal=parsed_data['seal'],
            location=parsed_data.get('location', ''), # Not strictly requested in example format but exists in model
            customer=customer,
            driver=driver,
            vehicle=vehicle,
            amount=rate_val if rate_val else 0,
            status=ShipmentStatus.PENDING_REVIEW,
            is_flagged=is_duplicate,
            notes=f"Auto-ingested from WhatsApp." + (" (Flagged: Duplicate container this billing period.)" if is_duplicate else "")
        )

        msg_log.is_processed = True
        msg_log.shipment = shipment
        msg_log.save()
        
        return JsonResponse({
            "status": "success",
            "message": "Shipment created successfully",
            "shipment_id": shipment.id,
            "flagged": is_duplicate
        })

    except Exception as e:
        msg_log.is_flagged = True
        msg_log.error_message = str(e)
        msg_log.save()
        return JsonResponse({
            "status": "flagged", 
            "message": f"Parse error: {str(e)}",
            "message_id": msg_log.id
        })


# ---------------------------------------------------------------------------
# WhatsApp Configuration API Endpoints
# ---------------------------------------------------------------------------

@csrf_exempt
@require_http_methods(["GET"])
def whatsapp_status(request):
    """Get current WhatsApp sidecar connection status."""
    config = WhatsAppConfig.get_instance()
    return JsonResponse({
        "sidecar_status": config.sidecar_status,
        "auth_status": config.auth_status,
        "last_connection_time": config.last_connection_time.isoformat() if config.last_connection_time else None,
        "last_error": config.last_error,
        "last_error_time": config.last_error_time.isoformat() if config.last_error_time else None,
    })


@csrf_exempt
@require_http_methods(["GET"])
def whatsapp_qr_code(request):
    """Get the latest QR code for WhatsApp authentication."""
    config = WhatsAppConfig.get_instance()
    return JsonResponse({
        "qr_code": config.qr_code_data,
        "auth_status": config.auth_status,
    })


@csrf_exempt
@require_http_methods(["GET"])
def whatsapp_logs(request):
    """Get recent WhatsApp logs with optional pagination."""
    limit = int(request.GET.get("limit", 100))
    offset = int(request.GET.get("offset", 0))
    
    logs = WhatsAppLog.objects.all()[offset:offset + limit]
    total = WhatsAppLog.objects.count()
    
    return JsonResponse({
        "logs": [
            {
                "level": log.level,
                "message": log.message,
                "created_at": log.created_at.isoformat(),
            }
            for log in logs
        ],
        "total": total,
        "offset": offset,
        "limit": limit,
    })


@csrf_exempt
@require_http_methods(["GET"])
def whatsapp_groups(request):
    """Get list of available WhatsApp groups."""
    groups = WhatsAppGroup.objects.all()
    return JsonResponse({
        "groups": [
            {
                "id": g.id,
                "group_jid": g.group_jid,
                "group_name": g.group_name,
                "is_active": g.is_active,
                "participant_count": g.participant_count,
                "last_synced_at": g.last_synced_at.isoformat(),
            }
            for g in groups
        ],
        "total": groups.count(),
    })


def check_auth(request):
    """Check if request has valid Bearer token OR is from authenticated user."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        expected_header = f"Bearer {WHATSAPP_SECRET}"
        return auth_header == expected_header
    # Allow authenticated Django users
    return request.user.is_authenticated


@csrf_exempt
@require_POST
def whatsapp_update_group_status(request, group_id):
    """Toggle a group's active status."""
    if not check_auth(request):
        return JsonResponse({"error": "Unauthorized"}, status=401)
    
    try:
        group = WhatsAppGroup.objects.get(id=group_id)
        data = json.loads(request.body)
        is_active = data.get("is_active", group.is_active)
        group.is_active = is_active
        group.save()
        return JsonResponse({
            "status": "success",
            "group_id": group.id,
            "is_active": group.is_active,
        })
    except WhatsAppGroup.DoesNotExist:
        return JsonResponse({"error": "Group not found"}, status=404)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)


@csrf_exempt
@require_POST
def whatsapp_sync_groups(request):
    """Endpoint for sidecar to POST group list to Django."""
    if not check_auth(request):
        return JsonResponse({"error": "Unauthorized"}, status=401)
    
    try:
        data = json.loads(request.body)
        groups_data = data.get("groups", [])
        
        # Update or create groups
        for group_data in groups_data:
            group_jid = group_data.get("jid")
            group_name = group_data.get("name", "Unknown Group")
            participant_count = group_data.get("participants", 0)
            
            if group_jid:
                group, created = WhatsAppGroup.objects.update_or_create(
                    group_jid=group_jid,
                    defaults={
                        "group_name": group_name,
                        "participant_count": participant_count,
                    }
                )
        
        return JsonResponse({
            "status": "success",
            "groups_synced": len(groups_data),
        })
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
@require_POST
def whatsapp_update_status(request):
    """Endpoint for sidecar to POST connection status updates."""
    if not check_auth(request):
        return JsonResponse({"error": "Unauthorized"}, status=401)
    
    try:
        data = json.loads(request.body)
        config = WhatsAppConfig.get_instance()
        
        sidecar_status = data.get("sidecar_status", config.sidecar_status)
        auth_status = data.get("auth_status", config.auth_status)
        qr_code = data.get("qr_code", config.qr_code_data)
        last_error = data.get("last_error", "")
        
        config.sidecar_status = sidecar_status
        config.auth_status = auth_status
        if qr_code:
            config.qr_code_data = qr_code
        if last_error:
            config.last_error = last_error
            from django.utils import timezone
            config.last_error_time = timezone.now()
        if sidecar_status == WhatsAppConfig.ConnectionStatus.CONNECTED:
            from django.utils import timezone
            config.last_connection_time = timezone.now()
        
        config.save()
        
        # Log the status update
        if last_error:
            WhatsAppLog.objects.create(
                level=WhatsAppLog.LogLevel.ERROR,
                message=f"Sidecar error: {last_error}"
            )
        else:
            WhatsAppLog.objects.create(
                level=WhatsAppLog.LogLevel.INFO,
                message=f"Status updated: {auth_status}"
            )
        
        return JsonResponse({
            "status": "success",
            "config_updated": True,
        })
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
@require_POST
def trigger_sync_groups(request):
    """Fetch groups from sidecar and sync to database."""
    if not check_auth(request):
        return JsonResponse({"error": "Unauthorized"}, status=401)
    
    try:
        import requests
        
        # Get groups from sidecar
        sidecar_url = f"{WHATSAPP_SIDECAR_URL}/api/groups"
        response = requests.get(sidecar_url, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            groups_list = data.get("groups", [])
            
            # Update groups in database
            for group_data in groups_list:
                WhatsAppGroup.objects.update_or_create(
                    group_jid=group_data.get("jid"),
                    defaults={
                        "group_name": group_data.get("name", "Unknown Group"),
                        "participant_count": group_data.get("participants", 0),
                        "is_active": True,
                    }
                )
            
            return JsonResponse({
                "status": "success",
                "groups_synced": len(groups_list),
            })
        else:
            return JsonResponse({
                "error": f"Sidecar returned {response.status_code}: {response.text}",
                "status": "error"
            }, status=500)
    except requests.exceptions.Timeout:
        return JsonResponse({"error": "Sidecar timeout", "status": "error"}, status=500)
    except requests.exceptions.ConnectionError:
        return JsonResponse({"error": "Sidecar connection error", "status": "error"}, status=500)
    except Exception as e:
        return JsonResponse({"error": str(e), "status": "error"}, status=500)


@csrf_exempt
@require_POST
def trigger_restart_connection(request):
    """Trigger the sidecar to restart the WhatsApp connection."""
    if not check_auth(request):
        return JsonResponse({"error": "Unauthorized"}, status=401)
    
    try:
        import requests
        
        sidecar_url = f"{WHATSAPP_SIDECAR_URL}/api/restart"
        headers = {"Authorization": f"Bearer {WHATSAPP_SECRET}"}
        
        response = requests.post(sidecar_url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            return JsonResponse({"status": "success"})
        else:
            return JsonResponse({
                "error": f"Sidecar returned {response.status_code}: {response.text}",
                "status": "error"
            }, status=500)
    except Exception as e:
        return JsonResponse({"error": str(e), "status": "error"}, status=500)


@csrf_exempt
@require_http_methods(["DELETE", "POST"])
def trigger_clear_auth(request):
    """Trigger the sidecar to clear authentication and show new QR code."""
    if not check_auth(request):
        return JsonResponse({"error": "Unauthorized"}, status=401)
    
    try:
        import requests
        
        sidecar_url = f"{WHATSAPP_SIDECAR_URL}/api/auth"
        headers = {"Authorization": f"Bearer {WHATSAPP_SECRET}"}
        
        response = requests.delete(sidecar_url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            return JsonResponse({"status": "success"})
        else:
            return JsonResponse({
                "error": f"Sidecar returned {response.status_code}: {response.text}",
                "status": "error"
            }, status=500)
    except Exception as e:
        return JsonResponse({"error": str(e), "status": "error"}, status=500)
