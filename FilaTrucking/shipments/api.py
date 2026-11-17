import hashlib
import hmac
import json
import logging
import re
import requests
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal, InvalidOperation
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_http_methods
from django.db import IntegrityError
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from .models import (
    WhatsAppMessage, Shipment, ShipmentStatus,
    WhatsAppConfig, WhatsAppGroup, WhatsAppLog
)
from customers.models import Customer
from .message_parser import KEY_FIELDS, parse_message
from drivers.models import Driver
from vehicles.models import Vehicle

logger = logging.getLogger(__name__)
WHATSAPP_SECRET = settings.WHATSAPP_INGEST_SECRET  # no default: see settings.py (QA-004)
WHATSAPP_SIDECAR_URL = getattr(settings, 'WHATSAPP_SIDECAR_URL', 'http://localhost:3001')


def has_valid_bearer(request):
    """True when the request carries 'Authorization: Bearer <shared secret>' (constant-time compare)."""
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme != "Bearer" or not token:
        return False
    return hmac.compare_digest(token.encode(), WHATSAPP_SECRET.encode())


def _sidecar_headers():
    return {"Authorization": f"Bearer {WHATSAPP_SECRET}"}

FORMAT_HELP = """📋 Please send the shipment like this, one value per line:

Klaipeda  (optional note, words only)
95938662  (booking)
HAMU3898114  (container)
179458  (seal)
NAPZ225644  (optional chassis)

Required: BOOKING CONTAINER SEAL
Optional on extra lines: CUSTOMER: name or abbreviation, RATE: amount, DRIVER: driver name, TRUCK: truck number, DATE: MM/DD/YYYY"""


def missing_fields_reply(missing):
    return f"⚠️ Missing: {', '.join(missing)}\n\n{FORMAT_HELP}"


def jid_to_e164(jid):
    """'13125550101@s.whatsapp.net' (or '...:12@s.whatsapp.net', '+1 312 555 0101') -> '+13125550101'.

    Returns None for ids that are not phone numbers (e.g. '123456@lid' privacy ids) or look implausible,
    so they never match a driver by accident (QA-017).
    """
    if not jid:
        return None
    user, _, domain = str(jid).partition("@")
    if domain and domain not in ("s.whatsapp.net", "c.us"):
        return None
    digits = re.sub(r"\D", "", user.split(":")[0])
    return "+" + digits if 7 <= len(digits) <= 15 else None


def parse_rate(text):
    """'$1,500.50' -> Decimal('1500.50'); anything unusable (empty, '1.2.3', absurdly large, 0) -> None."""
    cleaned = re.sub(r"[^\d.]", "", text or "")
    try:
        value = Decimal(cleaned) if cleaned else None
    except InvalidOperation:
        return None
    return value if value and value < Decimal("10000000") else None


def find_driver(name, sender):
    """Driver named in the message (exact, else a unique partial match), else the driver whose phone is the sender's."""
    if name:
        exact = Driver.objects.filter(name__iexact=name).first()
        if exact:
            return exact
        partial = list(Driver.objects.filter(name__icontains=name)[:2])
        if len(partial) == 1:  # "Moe" must not silently pick one of two Moes
            return partial[0]
    phone = jid_to_e164(sender)
    if phone:
        return Driver.objects.filter(phone_number=phone).first()
    return None


MAX_MESSAGE_CHARS = 4000


def _dedupe_key(message_id, sender, timestamp, text):
    if message_id:
        return ("id:" + message_id)[:80]
    if timestamp:
        return "h:" + hashlib.sha256(f"{sender}|{timestamp}|{text}".encode()).hexdigest()
    return None


def _duplicate_response(earlier):
    return JsonResponse({
        "status": "duplicate",
        "message": "Message already processed",
        "message_id": earlier.id,
        "shipment_id": earlier.shipment_id,
    })  # deliberately no reply_message: the group was already answered the first time


def _message_local_date(timestamp):
    """Business-local date of the WhatsApp message (a 20:30 Chicago message is the 5th, not the 6th UTC)."""
    parsed = parse_datetime(timestamp) if timestamp else None
    if parsed is None:
        return timezone.localdate()
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, dt_timezone.utc)
    return timezone.localtime(parsed).date()


SHIPMENT_KEYS = set(KEY_FIELDS)  # recognised "KEY:" names (kept for importers of the old constant)

UNASSIGNED_CUSTOMER = "Unassigned"


def looks_like_shipment_message(text):
    """Is this plausibly an attempt to report a shipment, as opposed to chat in the dispatch group? (QA-016, QA-051)

    The rules live in message_parser.parse_message: a complete shipment, or a clear but incomplete attempt, is True;
    'ok thanks boss', questions, links and lone numbers are False.
    """
    return parse_message(text).kind != "ignore"


def get_unassigned_customer():
    """Placeholder customer for a message that names none: the shipment is created, flagged, and dispatch sets the
    real customer when confirming it."""
    customer = Customer.objects.filter(abbreviation__iexact=UNASSIGNED_CUSTOMER).first()
    if customer is None:
        customer = Customer.objects.create(
            name=UNASSIGNED_CUSTOMER,
            abbreviation=UNASSIGNED_CUSTOMER.upper(),
            phone_number="+10000000000",
            address="N/A",
            email="unassigned@import.local",
            notes="Placeholder for WhatsApp shipments that named no customer. Move them to the real customer.",
        )
    return customer


@csrf_exempt
@require_POST
def ingest_whatsapp_message(request):
    if not has_valid_bearer(request):
        return JsonResponse({"error": "Unauthorized"}, status=401)
    
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({"error": "Expected a JSON object"}, status=400)

    raw_text = data.get("text", "")
    sender_phone = data.get("sender", "")
    message_timestamp = data.get("timestamp")
    message_id = data.get("message_id")
    if not isinstance(raw_text, str) or not isinstance(sender_phone, str)             or not (message_timestamp is None or isinstance(message_timestamp, str))             or not (message_id is None or isinstance(message_id, str)):
        return JsonResponse({"error": "text, sender, timestamp and message_id must be strings"}, status=400)

    if not raw_text.strip():
        return JsonResponse({"error": "No text provided"}, status=400)
    if len(raw_text) > MAX_MESSAGE_CHARS:
        return JsonResponse({"error": "Text too long"}, status=400)
    sender_phone = sender_phone[:50]

    parsed = parse_message(raw_text)
    if parsed.kind == "ignore":
        # Chit-chat in the group: no error row (it would keep personal chat forever), and no reply (the sidecar
        # only answers when Django sends a reply_message) (QA-016).
        return JsonResponse({"status": "ignored", "message": "Not a shipment message"})

    # Idempotency: the same WhatsApp message must not create a second shipment (QA-018)
    dedupe_key = _dedupe_key(message_id, sender_phone, message_timestamp, raw_text)
    if dedupe_key:
        earlier = WhatsAppMessage.objects.filter(dedupe_key=dedupe_key).first()
        if earlier:
            return _duplicate_response(earlier)
    try:
        msg_log = WhatsAppMessage.objects.create(raw_text=raw_text, sender_phone=sender_phone, dedupe_key=dedupe_key)
    except IntegrityError:  # a concurrent worker stored the same message a moment ago
        earlier = WhatsAppMessage.objects.filter(dedupe_key=dedupe_key).first()
        if earlier:
            return _duplicate_response(earlier)
        raise

    fallback_date = _message_local_date(message_timestamp)

    parsed_data = dict(parsed.fields)

    if parsed.kind == "incomplete":
        msg_log.is_flagged = True
        msg_log.error_message = f"Missing required fields: {', '.join(parsed.missing)}"
        msg_log.save()
        return JsonResponse({
            "status": "flagged",
            "message": "Missing fields, flagged for manual review.",
            "message_id": msg_log.id,
            "reply_message": missing_fields_reply(parsed.missing),
        })

    # Data transformation
    try:
        # Date parsing (expecting MM/DD/YY or YYYY-MM-DD or MM/DD/YYYY)
        # Use fallback_date if no date provided in message
        parsed_date_obj = None
        if parsed_data.get('date'):
            date_str = parsed_data['date']
            for fmt in ('%m/%d/%y', '%m/%d/%Y', '%Y-%m-%d'):
                try:
                    parsed_date_obj = datetime.strptime(date_str, fmt).date()
                    break
                except ValueError:
                    continue
            if not parsed_date_obj:
                raise ValueError(f"Invalid date format: {date_str}. Expected MM/DD/YY")
        else:
            # Use fallback date (message timestamp or current date)
            parsed_date_obj = fallback_date

        # FK Lookups - driver/vehicle optional. A customer that is NAMED but unknown stops the shipment (a typo must
        # not silently land somewhere else); no customer at all creates it under "Unassigned", flagged (QA-051).
        flag_reasons = []
        customer = None
        customer_name = parsed_data.get('customer')
        if customer_name:
            customer = Customer.objects.filter(abbreviation__iexact=customer_name).first()
            if not customer:
                customer = Customer.objects.filter(name__icontains=customer_name).first()
            if not customer:
                msg_log.is_flagged = True
                msg_log.error_message = f"Customer not found: {customer_name}"
                msg_log.save()
                return JsonResponse({
                    "status": "flagged",
                    "message": "Customer not found",
                    "message_id": msg_log.id,
                    "reply_message": (
                        f"⚠️ Customer not found: {customer_name}\n\n"
                        "Check the customer name or abbreviation and send the message again, "
                        "or leave the customer out and dispatch will set it.\n\n" + FORMAT_HELP
                    ),
                })
        else:
            customer = get_unassigned_customer()
            flag_reasons.append("No customer in the message: set the customer.")

        driver = find_driver(parsed_data.get('driver'), sender_phone)
        
        # Try to match vehicle from driver if not provided
        vehicle = None
        if parsed_data.get('truck'):
            truck_plate = parsed_data['truck']
            vehicle = Vehicle.objects.filter(registration_number__icontains=truck_plate).first()
            if not vehicle:
                vehicle = Vehicle.objects.filter(chassis_number__icontains=truck_plate).first()
        elif driver:
            # Try to get driver's default vehicle
            vehicle = driver.vehicle if hasattr(driver, 'vehicle') else None

        # Duplicate checking
        container_no = parsed_data['container']
        month_start = parsed_date_obj.replace(day=1)
        next_month = (month_start.month % 12) + 1
        year_next = month_start.year + (month_start.month // 12)
        month_end = month_start.replace(year=year_next, month=next_month)
        
        # Longer values would be a database error on MySQL (strict mode): refuse them with a clear message
        for field_name, limit in (("booking", 50), ("container", 50), ("seal", 50), ("chassis", 50), ("location", 100)):
            if len(parsed_data.get(field_name, "")) > limit:
                raise ValueError(f"{field_name.capitalize()} is too long (maximum {limit} characters)")

        duplicates = Shipment.objects.filter(
            container__iexact=container_no,
            date__gte=month_start,
            date__lt=month_end
        )
        is_duplicate = duplicates.exists()

        # No rate in the message: fall back to the customer's default rate (PRD); if there is none either, leave the
        # amount empty and flag the shipment instead of inventing $0 (QA-017).
        amount = parse_rate(parsed_data.get('rate')) or customer.default_rate
        missing_rate = amount is None
        
        if is_duplicate:
            flag_reasons.append("Duplicate container this billing period.")
        if missing_rate:
            flag_reasons.append("No rate in the message and the customer has no default rate: set the amount.")

        # notes = what a person wrote (the description line): it becomes the invoice description. The automatic
        # remarks live in flag_reason. Location stays empty unless given: it then defaults to the customer's
        # abbreviation (Shipment.effective_location), also after the customer is changed.
        shipment = Shipment.objects.create(
            date=parsed_date_obj,
            booking=parsed_data['booking'],
            container=container_no,
            seal=parsed_data['seal'],
            chassis=parsed_data.get('chassis', ''),
            location=parsed_data.get('location', ''),
            customer=customer,
            driver=driver,
            vehicle=vehicle,
            amount=amount,
            status=ShipmentStatus.PENDING_REVIEW,
            is_flagged=bool(flag_reasons),
            flag_reason=" ".join(flag_reasons),
            notes=parsed_data.get('note', ''),
        )

        msg_log.is_processed = True
        msg_log.shipment = shipment
        msg_log.save()

        amount_text = f"${shipment.amount}" if shipment.amount is not None else "not set (admin will add the rate)"
        success_message = "✅ Shipment recorded!\n" + "\n".join(filter(None, [
            f"Note: {shipment.notes}" if shipment.notes else "",
            f"Booking: {shipment.booking}",
            f"Container: {shipment.container}",
            f"Seal: {shipment.seal}",
            f"Chassis: {shipment.chassis}" if shipment.chassis else "",
            f"Customer: {shipment.customer.name}",
            f"Date: {shipment.date.strftime('%m/%d/%Y')}",
            f"Amount: {amount_text}",
            "⚠️ No customer in the message: dispatch will set it." if customer_name is None else "",
            "Status: Pending Review",
        ]))

        return JsonResponse({
            "status": "success",
            "message": "Shipment created successfully",
            "shipment_id": shipment.id,
            "flagged": shipment.is_flagged,
            "reply_message": success_message
        })

    except ValueError as e:
        # our own validation messages (bad date, value too long, ...): safe to show to the driver
        msg_log.is_flagged = True
        msg_log.error_message = str(e)
        msg_log.save()

        error_reply = f"""⚠️ Error: {str(e)}

Please check your message format and try again.
Contact dispatch if issue persists."""

        return JsonResponse({
            "status": "flagged",
            "message": f"Parse error: {str(e)}",
            "message_id": msg_log.id,
            "reply_message": error_reply
        })

    except Exception:
        # anything else is OUR bug: log it, keep internals (SQL, paths) out of the WhatsApp group
        logger.exception("WhatsApp ingest failed for stored message %s", msg_log.id)
        msg_log.is_flagged = True
        msg_log.error_message = "Internal error while processing this message"
        msg_log.save()
        return JsonResponse({
            "status": "flagged",
            "message": "Internal error",
            "message_id": msg_log.id,
            "reply_message": "⚠️ Something went wrong while saving your message. Dispatch will review it manually.",
        })


# ---------------------------------------------------------------------------
# WhatsApp Configuration API Endpoints
# ---------------------------------------------------------------------------

@csrf_exempt
@require_http_methods(["GET"])
def whatsapp_status(request):
    """Get current WhatsApp sidecar connection status."""
    if not check_auth(request):
        return JsonResponse(UNAUTHORIZED, status=401)
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
    if not check_auth(request):
        return JsonResponse(UNAUTHORIZED, status=401)
    config = WhatsAppConfig.get_instance()
    return JsonResponse({
        "qr_code": config.qr_code_data,
        "auth_status": config.auth_status,
    })


@csrf_exempt
@require_http_methods(["GET"])
def whatsapp_logs(request):
    """Get recent WhatsApp logs with optional pagination."""
    if not check_auth(request):
        return JsonResponse(UNAUTHORIZED, status=401)
    try:
        limit = min(max(int(request.GET.get("limit", 100)), 1), 500)  # at most 500 rows per call
        offset = max(int(request.GET.get("offset", 0)), 0)
    except ValueError:
        return JsonResponse({"error": "limit and offset must be whole numbers"}, status=400)
    
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
    if not check_auth(request):
        return JsonResponse(UNAUTHORIZED, status=401)
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


def _csrf_ok(request):
    """Enforce Django's CSRF check by hand. These views are @csrf_exempt so the sidecar (no cookies) can call
    them with a bearer token; a browser session must still prove it is not a cross-site request (QA-003)."""
    if request.method in ("GET", "HEAD", "OPTIONS", "TRACE"):
        return True
    from django.middleware.csrf import CsrfViewMiddleware
    check = CsrfViewMiddleware(lambda req: None)
    check.process_request(request)
    return check.process_view(request, None, (), {}) is None


def check_auth(request):
    """True for the sidecar (valid Bearer secret) or for a logged-in browser session (+ CSRF on writes)."""
    if request.headers.get("Authorization", "").startswith("Bearer "):
        return has_valid_bearer(request)
    return request.user.is_authenticated and _csrf_ok(request)


UNAUTHORIZED = {"error": "Unauthorized"}


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
        
        previous = (config.sidecar_status, config.auth_status, config.last_error)
        sidecar_status = data.get("sidecar_status", config.sidecar_status)
        auth_status = data.get("auth_status", config.auth_status)
        last_error = data.get("last_error") or ""
        connected = sidecar_status == WhatsAppConfig.ConnectionStatus.CONNECTED

        config.sidecar_status = sidecar_status
        config.auth_status = auth_status
        # The QR is only valid while pairing: an explicit null from the sidecar, or being connected, clears it
        # (it used to stay on the settings page forever) (QA-019).
        if connected:
            config.qr_code_data = ""
        elif "qr_code" in data:
            config.qr_code_data = data.get("qr_code") or ""
        if last_error:
            config.last_error = last_error
            config.last_error_time = timezone.now()
        elif connected:
            config.last_error = ""  # a successful (re)connection ends the incident shown on the page
        if connected:
            config.last_connection_time = timezone.now()

        config.save()
        
        # Log CHANGES only (QA-045): every ping used to add a row (about 1,200 an hour during a reconnect loop; the
        # production table had 43,000 rows in 5 days). A repeated identical error is not news.
        if last_error and last_error != previous[2]:
            WhatsAppLog.objects.create(
                level=WhatsAppLog.LogLevel.ERROR,
                message=f"Sidecar error: {last_error}"[:2000]
            )
        elif not last_error and (sidecar_status, auth_status) != previous[:2]:
            WhatsAppLog.objects.create(
                level=WhatsAppLog.LogLevel.INFO,
                message=f"Status: {sidecar_status} / {auth_status}"
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
        response = requests.get(sidecar_url, headers=_sidecar_headers(), timeout=10)
        
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
                        # is_active is NOT touched: "Sync" used to switch every group back on, so the bot
                        # started listening in groups the admin had deliberately disabled (QA-019)
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
        headers = _sidecar_headers()
        
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
        headers = _sidecar_headers()
        
        response = requests.delete(sidecar_url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            # Groups (and which of them are enabled) are kept: re-pairing the same account brings the same groups
            # back, and deleting them silently dropped the admin's selection (QA-019).
            return JsonResponse({"status": "success"})
        else:
            return JsonResponse({
                "error": f"Sidecar returned {response.status_code}: {response.text}",
                "status": "error"
            }, status=500)
    except Exception as e:
        return JsonResponse({"error": str(e), "status": "error"}, status=500)
