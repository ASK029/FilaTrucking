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
    is_flagged = models.BooleanField(default=False, verbose_name="Flagged (Issue)")
    notes = models.TextField(blank=True, verbose_name="Notes")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self) -> str:
        return f"Shipment #{self.pk} – {self.container} ({self.customer})"


# ---------------------------------------------------------------------------
# WhatsApp Ingestion Log
# ---------------------------------------------------------------------------

class WhatsAppMessage(models.Model):
    raw_text = models.TextField(verbose_name="Raw Message Text")
    sender_phone = models.CharField(max_length=50, blank=True, verbose_name="Sender Phone")
    received_at = models.DateTimeField(auto_now_add=True)
    is_processed = models.BooleanField(default=False)
    is_flagged = models.BooleanField(default=False)
    error_message = models.TextField(blank=True)
    shipment = models.ForeignKey(
        Shipment, null=True, blank=True, on_delete=models.SET_NULL, related_name="whatsapp_messages"
    )

    class Meta:
        ordering = ["-received_at"]

    def __str__(self) -> str:
        return f"Message from {self.sender_phone} at {self.received_at}"


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


# ---------------------------------------------------------------------------
# WhatsApp Configuration & Groups
# ---------------------------------------------------------------------------

class WhatsAppConfig(models.Model):
    """Singleton model to store WhatsApp sidecar connection status and QR code."""
    
    class ConnectionStatus(models.TextChoices):
        DISCONNECTED = "disconnected", "Disconnected"
        CONNECTING = "connecting", "Connecting"
        CONNECTED = "connected", "Connected"
        ERROR = "error", "Error"
    
    class AuthStatus(models.TextChoices):
        NOT_AUTHENTICATED = "not_authenticated", "Not Authenticated"
        AUTHENTICATING = "authenticating", "Authenticating (Scan QR)"
        AUTHENTICATED = "authenticated", "Authenticated"
    
    sidecar_status = models.CharField(
        max_length=20,
        choices=ConnectionStatus,
        default=ConnectionStatus.DISCONNECTED,
        verbose_name="Sidecar Status",
    )
    auth_status = models.CharField(
        max_length=20,
        choices=AuthStatus,
        default=AuthStatus.NOT_AUTHENTICATED,
        verbose_name="Auth Status",
    )
    qr_code_data = models.TextField(
        blank=True,
        verbose_name="QR Code Data",
        help_text="Latest QR code in ASCII format",
    )
    last_connection_time = models.DateTimeField(
        null=True, blank=True, verbose_name="Last Connected At"
    )
    last_error = models.TextField(blank=True, verbose_name="Last Error")
    last_error_time = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "WhatsApp Configuration"
        verbose_name_plural = "WhatsApp Configuration"
    
    def __str__(self) -> str:
        return f"WhatsApp Config – Status: {self.get_sidecar_status_display()}"
    
    @classmethod
    def get_instance(cls):
        """Get or create the singleton instance."""
        instance, _ = cls.objects.get_or_create(pk=1)
        return instance


class WhatsAppGroup(models.Model):
    """Store WhatsApp groups that the sidecar can listen to."""
    
    group_jid = models.CharField(
        max_length=100, unique=True, verbose_name="Group JID",
        help_text="WhatsApp group identifier (e.g., 123456789-1234567890@g.us)"
    )
    group_name = models.CharField(max_length=255, verbose_name="Group Name")
    is_active = models.BooleanField(
        default=True,
        verbose_name="Active (Listen to this group)",
        help_text="If unchecked, messages from this group will be ignored"
    )
    participant_count = models.IntegerField(default=0, verbose_name="Participant Count")
    last_synced_at = models.DateTimeField(
        auto_now=True, verbose_name="Last Synced"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ["-last_synced_at", "group_name"]
        verbose_name = "WhatsApp Group"
        verbose_name_plural = "WhatsApp Groups"
    
    def __str__(self) -> str:
        status = "✓ Active" if self.is_active else "✗ Inactive"
        return f"{self.group_name} ({status})"


class WhatsAppLog(models.Model):
    """Store sidecar logs for debugging and monitoring."""
    
    class LogLevel(models.TextChoices):
        DEBUG = "debug", "Debug"
        INFO = "info", "Info"
        WARNING = "warning", "Warning"
        ERROR = "error", "Error"
    
    level = models.CharField(
        max_length=10,
        choices=LogLevel,
        default=LogLevel.INFO,
        verbose_name="Log Level",
    )
    message = models.TextField(verbose_name="Message")
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ["-created_at"]
        verbose_name = "WhatsApp Log"
        verbose_name_plural = "WhatsApp Logs"
        indexes = [models.Index(fields=["-created_at"])]
    
    def __str__(self) -> str:
        return f"[{self.get_level_display()}] {self.message[:100]}"
