from django.core.validators import MaxLengthValidator
from django.db import models
from django.db.models import Sum
from decimal import Decimal

from FilaTrucking.uploads import RandomUploadPath, document_validators
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
    # PROTECT everywhere (QA-039): deleting a customer/driver/truck must never silently erase or blank billing history
    driver = models.ForeignKey(Driver, on_delete=models.PROTECT, null=True, blank=True, verbose_name="Driver")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, verbose_name="Customer")
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, null=True, blank=True, verbose_name="Truck Numbe")

    date = models.DateField(verbose_name="Shipment Date")
    booking = models.CharField(max_length=50, verbose_name="Booking #")
    container = models.CharField(max_length=50, verbose_name="Container #")
    seal = models.CharField(max_length=50, verbose_name="Seal #")
    chassis = models.CharField(max_length=50, blank=True, verbose_name="Chassis #")
    # blank = "use the customer's abbreviation" (QA-051); see effective_location
    location = models.CharField(
        max_length=100, blank=True, verbose_name="Location",
        help_text="Leave empty to use the customer's abbreviation.",
    )
    amount = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="Rate ($)"
    )
    status = models.CharField(
        max_length=20,
        choices=ShipmentStatus,
        default=ShipmentStatus.PENDING_REVIEW,
        verbose_name="Status",
    )
    is_flagged = models.BooleanField(default=False, verbose_name="Flagged (Issue)")
    # Notes are what a person wrote about the shipment (for WhatsApp: the description line). They become the invoice
    # description, so system text must never go here: it goes to flag_reason (QA-051).
    notes = models.TextField(blank=True, verbose_name="Notes")
    flag_reason = models.TextField(blank=True, verbose_name="Why it is flagged")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self) -> str:
        return f"Shipment #{self.pk} – {self.container} ({self.customer})"

    @property
    def effective_location(self) -> str:
        """The shipment's own location, else the customer's abbreviation."""
        return (self.location or "").strip() or (self.customer.abbreviation if self.customer_id else "")

    def invoice_line_defaults(self) -> dict:
        """What a new invoice line copies from this shipment. The description is the shipment's notes (empty when there
        are none) and the location falls back to the customer's abbreviation. One place for all five callers (QA-051)."""
        return {
            "shipment": self,
            "date_incurred": self.date,
            "description": (self.notes or "").strip()[:255],
            "booking_no": self.booking,
            "container_no": self.container,
            "seal_no": self.seal,
            "location": self.effective_location,
            "amount": self.amount,
        }


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
    # Idempotency (QA-018): WhatsApp message id, or a hash of sender+timestamp+text. Baileys re-emits messages after
    # reconnects and the sidecar retries failed posts; the same message must never create two shipments.
    dedupe_key = models.CharField(max_length=80, null=True, blank=True, unique=True)

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
        Customer, on_delete=models.PROTECT, related_name="invoices"
    )
    invoice_date = models.DateField()
    status = models.CharField(
        max_length=10, choices=InvoiceStatus, default=InvoiceStatus.DRAFT
    )
    paid_at = models.DateField(null=True, blank=True)
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
    description = models.CharField(max_length=255, blank=True)  # empty when the shipment has no notes (QA-051)
    booking_no = models.CharField(max_length=50, null=True, blank=True)
    container_no = models.CharField(max_length=50, null=True, blank=True)
    seal_no = models.CharField(max_length=50, null=True, blank=True)
    location = models.CharField(max_length=255, blank=True)  # blank: filled from the shipment / customer abbreviation (QA-051)
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ["date_incurred", "id"]

    def __str__(self) -> str:
        return f"{self.description} – {self.amount}"


# ---------------------------------------------------------------------------
# Expense
# ---------------------------------------------------------------------------

class ExpenseCategory(models.TextChoices):
    IRP = "irp", "IRP"
    PARKING = "parking", "PARKING"
    MAINTENANCE = "maintenance", "Maintenance"
    TRUCK = "truck", "TRUCK"
    CHECK_CHARGE = "check_charge", "CHECK CHARGE"
    INSURANCE = "insurance", "INSURANCE"
    TOLL = "toll", "TOLL"
    FUEL = "fuel", "FUEL"
    CHASSIS = "chassis", "CHASSIS"
    DRIVER = "driver", "DRIVER"
    OTHER = "other", "OTHER"


class Expense(models.Model):
    date = models.DateField(verbose_name="Date")
    category = models.CharField(
        max_length=20, choices=ExpenseCategory, default=ExpenseCategory.OTHER,
        verbose_name="Category",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Amount ($)")
    vehicle = models.ForeignKey(
        Vehicle, on_delete=models.PROTECT, null=True, blank=True, verbose_name="Truck Number"
    )
    driver = models.ForeignKey(
        Driver, on_delete=models.PROTECT, null=True, blank=True, verbose_name="Driver"
    )
    notes = models.TextField(blank=True, verbose_name="Notes")
    receipt = models.FileField(
        upload_to=RandomUploadPath("expense_receipts"), validators=document_validators,
        null=True, blank=True, verbose_name="Receipt"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "Expense"
        verbose_name_plural = "Expenses"

    def __str__(self) -> str:
        return f"{self.get_category_display()} – ${self.amount} ({self.date})"


# ---------------------------------------------------------------------------
# Financial statement snapshots (written by the scheduled statement tasks)
# ---------------------------------------------------------------------------

class FinancialStatement(models.Model):
    """Snapshot of a period's figures, stored by generate_monthly/yearly_statement so the scheduled run
    leaves something to review (PRD: 'auto-generate draft statements'). Re-running a period updates its row."""

    class PeriodType(models.TextChoices):
        MONTHLY = "monthly", "Monthly"
        YEARLY = "yearly", "Yearly"

    period_type = models.CharField(max_length=10, choices=PeriodType)
    year = models.PositiveIntegerField()
    month = models.PositiveSmallIntegerField(default=0, help_text="1-12 for monthly statements, 0 for yearly")
    start_date = models.DateField()
    end_date = models.DateField()
    revenue = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_expenses = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    driver_pay_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    net_profit = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    generated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-year", "-month"]
        constraints = [
            models.UniqueConstraint(fields=["period_type", "year", "month"], name="uniq_statement_period"),
        ]

    def __str__(self) -> str:
        label = f"{self.year}-{self.month:02d}" if self.period_type == self.PeriodType.MONTHLY else str(self.year)
        return f"{self.get_period_type_display()} statement {label}"


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
    # QA-054: the connected account's own messages are dropped unless this is ticked (opt-in, like the groups).
    accept_own_messages = models.BooleanField(
        default=False,
        verbose_name="Accept shipments sent from the connected account",
        help_text="If ticked, shipments typed from the phone linked to the bot are processed like a group member's",
    )
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
    # Opt-in (QA-020): a newly discovered group is ignored until the admin ticks it. Defaulting to True made the
    # bot read (and answer in) every group the linked phone belongs to, family groups included.
    is_active = models.BooleanField(
        default=False,
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

# ---------------------------------------------------------------------------
# System Settings (Singleton)
# ---------------------------------------------------------------------------

class SystemSettings(models.Model):
    """
    Singleton model to store global system configurations.
    Sensitive fields are stored encrypted.
    """
    # Company information printed on invoices and used in the invoice email (QA-052). The defaults are the text that
    # used to be hard-coded in the templates, so nothing changes until the owner edits them.
    company_name = models.CharField(max_length=100, default="Fila Trucking", verbose_name="Company Name")
    company_address = models.TextField(
        blank=True, default="123 Fleet Street, Suite 100\nChicago, IL 60601", validators=[MaxLengthValidator(300)],
        verbose_name="Company Address", help_text="One line per row; printed as written under the company name.",
    )
    company_phone = models.CharField(max_length=50, blank=True, default="(555) 012-3456", verbose_name="Company Phone")
    company_email = models.EmailField(blank=True, verbose_name="Company Email")

    # Motive (GoMotive) Settings
    motive_api_key_encrypted = models.TextField(blank=True, verbose_name="Motive API Key (Encrypted)")
    motive_base_url = models.URLField(
        default="https://api.keeptruckin.com/v1",
        verbose_name="Motive Base URL"
    )

    # Email Settings (Dynamic SMTP credentials)
    email_host_user = models.CharField(max_length=255, blank=True, verbose_name="Email Host User")
    email_host_password_encrypted = models.TextField(blank=True, verbose_name="Email Host Password (Encrypted)")
    email_from_email = models.EmailField(blank=True, verbose_name="From Email Address")

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "System Settings"
        verbose_name_plural = "System Settings"

    def __str__(self) -> str:
        return "System Settings"

    @classmethod
    def get_instance(cls):
        instance, _ = cls.objects.get_or_create(pk=1)
        return instance

    @property
    def company_address_lines(self):
        """The address as non-empty lines (what the invoice prints, one per row)."""
        return [line.strip() for line in (self.company_address or "").splitlines() if line.strip()]

    def set_motive_api_key(self, value):
        from .utils import encrypt_value
        self.motive_api_key_encrypted = encrypt_value(value)

    def get_motive_api_key(self):
        from .utils import decrypt_value
        return decrypt_value(self.motive_api_key_encrypted)

    def set_email_password(self, value):
        from .utils import encrypt_value
        self.email_host_password_encrypted = encrypt_value(value)

    def get_email_password(self):
        from .utils import decrypt_value
        return decrypt_value(self.email_host_password_encrypted)
