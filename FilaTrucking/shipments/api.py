import json
import re
from datetime import datetime
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.utils.dateparse import parse_date

from .models import WhatsAppMessage, Shipment, ShipmentStatus
from customers.models import Customer
from drivers.models import Driver
from vehicles.models import Vehicle

WHATSAPP_SECRET = getattr(settings, 'WHATSAPP_INGEST_SECRET', 'fila_secret_2026')

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
