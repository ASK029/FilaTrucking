"""Seed a LOCAL database with fake data for UI testing. Never run against production.
Usage (from FilaTrucking/):  python ../qa/seed_fake_data.py
Requires DATABASE_URL pointing at a throw-away DB (the QA scripts set sqlite)."""
import os
import sys
from datetime import date, timedelta
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "FilaTrucking"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "FilaTrucking.settings")
import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402

if "sqlite" not in settings.DATABASES["default"]["ENGINE"] and "qa" not in str(settings.DATABASES["default"].get("HOST", "")) \
        and os.environ.get("QA_ALLOW_NON_SQLITE") != "1":
    sys.exit("Refusing to seed a non-SQLite database (set QA_ALLOW_NON_SQLITE=1 only for the filaqa docker stack).")

from django.contrib.auth import get_user_model  # noqa: E402

from customers.models import Customer  # noqa: E402
from drivers.models import Driver  # noqa: E402
from shipments.models import (Expense, ExpenseCategory, IFTARate, Invoice, InvoiceLineItem, InvoiceStatus,  # noqa: E402
                              Shipment, ShipmentStatus, WhatsAppConfig, WhatsAppGroup, WhatsAppLog, WhatsAppMessage)
from vehicles.models import IFTAMileage, Maintenance, Vehicle  # noqa: E402

User = get_user_model()
if not User.objects.filter(username="qa_admin").exists():
    User.objects.create_superuser("qa_admin", "qa@example.com", "qa-pass-123")

today = date.today()
customers = [
    ("Acme Shipping Line", "ASL", "+13125550100", Decimal("400")),
    ("Very Long Customer Name Logistics International Holdings Company Limited (Midwest Division)", "VLCN", "+13125550101", Decimal("1250.50")),
    ("<script>window.__xss_customer=1</script> Corp", "XSS", "+13125550102", None),
    ("شركة النقل العربية", "ARB", "+13125550103", Decimal("300")),
    ("O'Brien & Sons \"Freight\"", "OBS", "+13125550104", Decimal("275")),
    ("Beta Freight", "BETA", "+13125550105", Decimal("510")),
]
cust = []
for n, a, p, r in customers:
    cust.append(Customer.objects.get_or_create(abbreviation=a, defaults=dict(
        name=n, phone_number=p, address="100 Dock Rd", city_state="Chicago, IL", email=f"{a.lower()}@example.com", default_rate=r))[0])

drivers = []
for i, n in enumerate(["Moe Hassan", "Taz Ahmad", "Mohab Ali", "A Very Long Driver Name That Might Overflow The Card Layout", "محمد", "Inactive Ivan"]):
    drivers.append(Driver.objects.get_or_create(license_number=f"LIC-{i:03d}", defaults=dict(
        name=n, phone_number=f"+1312555020{i}", status="inactive" if i == 5 else "active",
        license_expiry=today + timedelta(days=(-20 if i == 2 else 200 + i)) if i != 3 else None))[0])

vehicles = []
for i in range(6):
    vehicles.append(Vehicle.objects.get_or_create(chassis_number=f"1XKYD49X0LJ0000{i:02d}", defaults=dict(
        registration_number=f"T-{100 + i}", model="579", Manufacturer="Peterbilt" if i % 2 else None, year=2018 + i if i != 3 else None,
        driver=drivers[i] if i != 4 else None, current_odometer=100000 + i * 5000, average_mpg=Decimal("6.5"),
        gomotive_id=f"gm-{i}" if i < 3 else None))[0])

if not Shipment.objects.exists():
    statuses = [ShipmentStatus.PENDING_REVIEW, ShipmentStatus.CONFIRMED, ShipmentStatus.INVOICED]
    for i in range(45):
        Shipment.objects.create(
            customer=cust[i % len(cust)], driver=drivers[i % 5] if i % 4 else None, vehicle=vehicles[i % 6] if i % 3 else None,
            date=today - timedelta(days=i), booking=f"RICG{2390500 + i}", container=f"NYKU{5132858 + i}", seal=str(101039 + i),
            location="Chicago IL" if i % 5 else "</script><script>window.__xss_location=1</script>",
            amount=Decimal("400") + i if i % 7 else None, status=statuses[i % 3], is_flagged=(i % 9 == 0), notes="seed")
    # invoices
    for k, st in enumerate([InvoiceStatus.DRAFT, InvoiceStatus.SENT, InvoiceStatus.PAID, InvoiceStatus.PAID]):
        inv = Invoice.objects.create(customer=cust[k], invoice_date=today - timedelta(days=10 * k), status=st,
                                     paid_at=today - timedelta(days=3) if st == InvoiceStatus.PAID else None)
        for s in Shipment.objects.filter(customer=cust[k]).exclude(amount=None)[:3]:
            InvoiceLineItem.objects.create(invoice=inv, shipment=s, date_incurred=s.date, description=f"Shipment {s.container}",
                                           booking_no=s.booking, container_no=s.container, seal_no=s.seal, location="Chicago IL", amount=s.amount)
        inv.calculate_total()
    for i in range(30):
        Expense.objects.create(date=today - timedelta(days=i), category=list(ExpenseCategory.values)[i % 11], amount=Decimal("20") + i,
                               vehicle=vehicles[i % 6] if i % 2 else None, driver=drivers[i % 5] if i % 3 == 0 else None,
                               notes="Imported: FUEL" if i % 4 == 0 else "")
    for st in ("IL", "IN", "WI"):
        for q in (1, 2, 3, 4):
            IFTARate.objects.get_or_create(state_code=st, quarter=q, year=today.year, defaults=dict(rate=Decimal("0.4550")))
    for v in vehicles[:3]:
        for st in ("IL", "IN"):
            IFTAMileage.objects.get_or_create(vehicle=v, state_code=st, month=today.month, year=today.year,
                                              defaults=dict(miles=Decimal("1200.5"), gallons=Decimal("180.25")))
    Maintenance.objects.create(vehicle=vehicles[0], cost=Decimal("350"), service_provider="Joe's Truck Repair", type="Oil change",
                               description="Oil + filters", mileage_at_service=99000, next_service_mileage=100400)
    Maintenance.objects.create(vehicle=vehicles[1], cost=Decimal("0"), service_provider="GoMotive", type="Fault Code", description="P0420",
                               next_service_mileage=90000)

    cfg = WhatsAppConfig.get_instance()
    cfg.sidecar_status, cfg.auth_status = "disconnected", "authenticating"
    cfg.qr_code_data = "2@QA-FAKE-QR-PAYLOAD-NOT-A-REAL-PAIRING-CODE"
    cfg.last_error = "Disconnected: " + "x" * 400
    cfg.save()
    for i in range(3):
        WhatsAppGroup.objects.get_or_create(group_jid=f"12036{i}@g.us", defaults=dict(group_name=f"QA Dispatch Group {i}", participant_count=5 + i, is_active=i == 0))
    for i in range(30):
        WhatsAppLog.objects.create(level="error" if i % 3 == 0 else "info", message=f"seed log line {i}")
    WhatsAppMessage.objects.create(raw_text="hello", sender_phone="x", is_flagged=True, error_message="Unrecognized message format")
print("seeded:", Customer.objects.count(), "customers,", Shipment.objects.count(), "shipments,", Invoice.objects.count(), "invoices")
