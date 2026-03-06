from django.db import models
from django.db.models import Sum
from decimal import Decimal

from customers.models import Customer
from drivers.models import Driver
from vehicles.models import Vehicle


# ---------------------------------------------------------------------------
# Shipment
# ---------------------------------------------------------------------------

class ShipmentStatus(models.TextChoices):
    PENDING_REVIEW = "pending_review", "Pending Review"
    CONFIRMED = "confirmed", "Confirmed"
    INVOICED = "invoiced", "Invoiced"


class Shipment(models.Model):
    driver = models.ForeignKey(Driver, on_delete=models.CASCADE, verbose_name="Driver")
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, verbose_name="Customer")
    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, verbose_name="Vehicle")

    date = models.DateField(verbose_name="Shipment Date")
    booking = models.CharField(max_length=50, verbose_name="Booking #")
    container = models.CharField(max_length=50, verbose_name="Container #")
    seal = models.CharField(max_length=50, verbose_name="Seal #")
    location = models.CharField(max_length=100, verbose_name="Location")
    amount = models.DecimalField(
        max_digits=12, decimal_places=2, verbose_name="Rate ($)"
    )
    status = models.CharField(
        max_length=20,
        choices=ShipmentStatus,
        default=ShipmentStatus.PENDING_REVIEW,
        verbose_name="Status",
    )
    notes = models.TextField(blank=True, verbose_name="Notes")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self) -> str:
        return f"Shipment #{self.pk} – {self.container} ({self.customer})"


# ---------------------------------------------------------------------------
# Invoice
# ---------------------------------------------------------------------------

class InvoiceStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SENT = "sent", "Sent"
    PAID = "paid", "Paid"


class Invoice(models.Model):
    customer = models.ForeignKey(
        Customer, on_delete=models.CASCADE, related_name="invoices"
    )
    invoice_date = models.DateField()
    status = models.CharField(
        max_length=10, choices=InvoiceStatus, default=InvoiceStatus.DRAFT
    )
    total_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0")
    )

    class Meta:
        ordering = ["-invoice_date", "-id"]

    def __str__(self) -> str:
        return f"Invoice #{self.pk} – {self.customer.name} – {self.invoice_date}"

    def calculate_total(self) -> Decimal:
        total = self.line_items.aggregate(s=Sum("amount"))["s"] or Decimal("0")
        self.total_amount = total
        self.save(update_fields=["total_amount"])
        return total


class InvoiceLineItem(models.Model):
    invoice = models.ForeignKey(
        Invoice, on_delete=models.CASCADE, related_name="line_items"
    )
    shipment = models.ForeignKey(
        Shipment, on_delete=models.SET_NULL, null=True, blank=True
    )
    date_incurred = models.DateField()
    description = models.CharField(max_length=255)
    container_no = models.CharField(max_length=50, null=True, blank=True)
    seal_no = models.CharField(max_length=50, null=True, blank=True)
    location = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ["date_incurred", "id"]

    def __str__(self) -> str:
        return f"{self.description} – {self.amount}"


# ---------------------------------------------------------------------------
# Expense
# ---------------------------------------------------------------------------

class ExpenseCategory(models.TextChoices):
    DRIVER_PAY = "driver_pay", "Driver Pay"
    REPAIRS = "repairs", "Repairs / Maintenance Parts"
    INSURANCE = "insurance", "Insurance"
    FUEL = "fuel", "Fuel"
    TOLLS = "tolls", "Tolls"
    OTHER = "other", "Other"


class Expense(models.Model):
    date = models.DateField(verbose_name="Date")
    category = models.CharField(
        max_length=20, choices=ExpenseCategory, default=ExpenseCategory.OTHER,
        verbose_name="Category",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Amount ($)")
    vehicle = models.ForeignKey(
        Vehicle, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Vehicle"
    )
    driver = models.ForeignKey(
        Driver, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Driver"
    )
    notes = models.TextField(blank=True, verbose_name="Notes")
    receipt = models.FileField(
        upload_to="expense_receipts/", null=True, blank=True, verbose_name="Receipt"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "Expense"
        verbose_name_plural = "Expenses"

    def __str__(self) -> str:
        return f"{self.get_category_display()} – ${self.amount} ({self.date})"


# ---------------------------------------------------------------------------
# IFTA Rate
# ---------------------------------------------------------------------------

class IFTARate(models.Model):
    """Stores the tax rate per state per quarter/year for IFTA calculations."""
    state_code = models.CharField(max_length=2, verbose_name="State Code")
    quarter = models.IntegerField(
        choices=[(1, "Q1"), (2, "Q2"), (3, "Q3"), (4, "Q4")],
        verbose_name="Quarter",
    )
    year = models.IntegerField(verbose_name="Year")
    rate = models.DecimalField(
        max_digits=8, decimal_places=4,
        verbose_name="Tax Rate ($/gallon)",
    )

    class Meta:
        ordering = ["-year", "-quarter", "state_code"]
        unique_together = [["state_code", "quarter", "year"]]
        verbose_name = "IFTA Rate"
        verbose_name_plural = "IFTA Rates"

    def __str__(self) -> str:
        return f"{self.state_code} Q{self.quarter}/{self.year} @ ${self.rate}"
