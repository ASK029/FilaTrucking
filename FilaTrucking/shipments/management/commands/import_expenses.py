import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand

from customers.models import Customer
from drivers.models import Driver
from shipments.models import (
    Expense,
    ExpenseCategory,
    Invoice,
    InvoiceStatus,
)


# CSV column → (ExpenseCategory, friendly label)
EXPENSE_COLUMNS = {
    "IRP": (ExpenseCategory.OTHER, "IRP"),
    "PARKING": (ExpenseCategory.OTHER, "Parking"),
    "ON SITE": (ExpenseCategory.OTHER, "On Site"),
    "TRUCK": (ExpenseCategory.OTHER, "Truck"),
    "CHECK CHARGE": (ExpenseCategory.OTHER, "Check Charge"),
    "INSURANS": (ExpenseCategory.INSURANCE, "Insurance"),
    "TOLL": (ExpenseCategory.TOLLS, "Toll"),
    "FUEL": (ExpenseCategory.FUEL, "Fuel"),
    "OTHER": (ExpenseCategory.OTHER, "Other"),
    "CHASSIS": (ExpenseCategory.OTHER, "Chassis"),
}


def _parse_money(raw: str) -> Decimal:
    """Strip $, commas, whitespace and return Decimal. Returns 0 on failure."""
    if not raw:
        return Decimal("0")
    cleaned = raw.replace("$", "").replace(",", "").strip()
    if not cleaned or cleaned == "-":
        return Decimal("0")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return Decimal("0")


class Command(BaseCommand):
    help = "Import monthly statement CSV (expenses + deposits) — e.g. 2025 STATMENTS - JAN.csv"

    def add_arguments(self, parser):
        parser.add_argument("csv_file", type=str, help="Path to the CSV file")
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Delete existing expenses/invoices for the imported month before importing",
        )

    def handle(self, *args, **options):
        csv_file_path = options["csv_file"]

        rows = []
        try:
            with open(csv_file_path, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    rows.append(row)
        except FileNotFoundError:
            self.stderr.write(self.style.ERROR(f'File "{csv_file_path}" not found'))
            return

        if not rows:
            self.stderr.write("CSV is empty.")
            return

        # ── Parse data rows (skip summary rows with empty DATE) ──
        data_rows = []
        for row in rows:
            date_str = (row.get("DATE") or "").strip()
            if not date_str:
                continue
            try:
                parsed_date = datetime.strptime(date_str, "%m-%d-%Y").date()
            except ValueError:
                try:
                    parsed_date = datetime.strptime(date_str, "%m/%d/%Y").date()
                except ValueError:
                    self.stderr.write(f"  ⚠ Could not parse date: {date_str}")
                    continue
            data_rows.append((parsed_date, row))

        if not data_rows:
            self.stderr.write("No valid data rows found.")
            return

        # Detect month/year from first row
        first_date = data_rows[0][0]
        import_month = first_date.month
        import_year = first_date.year
        self.stdout.write(
            self.style.NOTICE(
                f"Importing {len(data_rows)} rows for {first_date.strftime('%B %Y')}"
            )
        )

        # ── Optional: clear existing data for this month ──
        if options["clear"]:
            from calendar import monthrange

            _, last_day = monthrange(import_year, import_month)
            start = first_date.replace(day=1)
            end = first_date.replace(day=last_day)
            exp_del, _ = Expense.objects.filter(date__range=(start, end)).delete()
            inv_del, _ = Invoice.objects.filter(
                invoice_date__range=(start, end)
            ).delete()
            self.stdout.write(f"  Cleared {exp_del} expenses, {inv_del} invoices")

        expense_count = 0
        invoice_count = 0

        for parsed_date, row in data_rows:
            # ── Deposits → Invoice ──
            deposit_amt = _parse_money(row.get("DEPOSIT", ""))
            deposit_from = (row.get("DEPOSIT FROM") or "").strip()
            if deposit_amt > 0:
                customer = None
                if deposit_from and deposit_from != "-":
                    customer = Customer.objects.filter(
                        name__iexact=deposit_from
                    ).first()
                    if not customer:
                        customer = Customer.objects.create(
                            name=deposit_from.title(),
                            abbreviation=deposit_from[:20].upper(),
                            phone_number="+10000000000",
                            street="N/A",
                            email=f"{deposit_from.lower().replace(' ', '')}@import.local",
                        )
                        self.stdout.write(f"  + Created customer: {customer.name}")

                Invoice.objects.create(
                    customer=customer if customer else Customer.objects.first(),
                    invoice_date=parsed_date,
                    status=InvoiceStatus.PAID,
                    total_amount=deposit_amt,
                )
                invoice_count += 1

            # ── Expense columns ──
            for col_name, (category, label) in EXPENSE_COLUMNS.items():
                amt = _parse_money(row.get(col_name, ""))
                if amt > 0:
                    Expense.objects.create(
                        date=parsed_date,
                        category=category,
                        amount=amt,
                        notes=f"Imported: {label}",
                    )
                    expense_count += 1

            # ── Driver Pay ──
            pay_amt = _parse_money(row.get("PAY", ""))
            pay_to = (row.get("PAY TO") or "").strip()
            if pay_amt > 0:
                driver = None
                if pay_to and pay_to != "-":
                    driver = Driver.objects.filter(name__iexact=pay_to).first()
                    if not driver:
                        import uuid
                        driver = Driver.objects.create(
                            name=pay_to.title(),
                            phone_number="+10000000000",
                            license_number=f"IMP-{uuid.uuid4().hex[:8].upper()}",
                        )
                        self.stdout.write(f"  + Created driver: {driver.name}")

                Expense.objects.create(
                    date=parsed_date,
                    category=ExpenseCategory.DRIVER_PAY,
                    amount=pay_amt,
                    driver=driver,
                    notes=f"Imported: Pay → {pay_to or 'Unknown'}",
                )
                expense_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Done — {expense_count} expenses, {invoice_count} invoices created."
            )
        )
