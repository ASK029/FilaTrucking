from django.db import models

from drivers.models import Driver
# from shipments.models import IFTARate

class Status(models.TextChoices):
    Available = 'AV', 'Available'
    InUse = 'IU', 'In Use'
    Maintenance = 'MA', 'Maintenance'

class Ownership(models.TextChoices):
    CompanyOwned = 'CO', 'Company Owned'
    VendorLeased = 'VL', 'Vendor Lease'
    DriverOwned = 'DO', 'Driver Owned'

# Create your models here.
class Vehicle(models.Model):
    # Assigned Driver
    driver = models.ForeignKey(Driver, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='Driver')
    assigned_at = models.DateField(auto_now_add=True, verbose_name="Assigned At")
    # Vehicle Specification
    registration_number = models.CharField(max_length=20)
    Manufacturer = models.CharField(max_length=20, null=True, blank=True)
    model = models.CharField(max_length=30)
    year = models.IntegerField(verbose_name="Year", null=True, blank=True)
    chassis_number = models.CharField(max_length=30, verbose_name="VIN (Chassis Number)")
    average_mpg = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True,
        verbose_name="Average MPG (for IFTA)"
    )
    status = models.CharField(max_length=2, choices=Status, default=Status.Available)
    # GoMotive / mileage
    gomotive_id = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        verbose_name="GoMotive Vehicle ID",
        help_text="If set, mileage and maintenance can be synced from GoMotive.",
    )
    current_odometer = models.IntegerField(
        null=True,
        blank=True,
        verbose_name="Current Odometer",
        help_text="Most recent odometer reading (miles).",
    )
    # Ownership
    ownership_type = models.CharField(max_length=2, choices=Ownership, default=Ownership.CompanyOwned)
    # Image
    image = models.ImageField(upload_to='vehicle_images/', verbose_name="Vehicle Image", null=True, blank=True)

    def __str__(self) -> str:
        return self.chassis_number


class IFTAMileage(models.Model):
    """Quarterly mileage per state for IFTA reporting."""

    MONTH_CHOICES = (
        (1, "January"),
        (2, "February"),
        (3, "March"),
        (4, "April"),
        (5, "May"),
        (6, "June"),
        (7, "July"),
        (8, "August"),
        (9, "September"),
        (10, "October"),
        (11, "November"),
        (12, "December"),
    )

    vehicle = models.ForeignKey(
        Vehicle,
        on_delete=models.CASCADE,
        verbose_name="Vehicle",
        related_name="ifta_mileage_entries",
    )
    state_code = models.CharField(max_length=2, verbose_name="State", default="IL")
    month = models.IntegerField(choices=MONTH_CHOICES, verbose_name="Month", default=1)
    year = models.IntegerField(verbose_name="Year")
    miles = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Miles Driven",
    )
    gallons = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Gallons Consumed",
    )

    class Meta:
        verbose_name = "IFTA Entry"
        verbose_name_plural = "IFTA Entries"
        ordering = ["-year", "-month", "state_code"]
        unique_together = [["vehicle", "state_code", "month", "year"]]

    def __str__(self) -> str:
        return f"{self.vehicle.chassis_number} – {self.get_month_display()} {self.year} – {self.state_code}"


class Maintenance(models.Model):
    # Vehicle
    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, verbose_name="Vehicle")

    cost = models.DecimalField(max_digits=10, decimal_places=2)
    service_provider = models.CharField(max_length=40)
    type = models.CharField(max_length=40, verbose_name="Service Type")
    description = models.TextField()
    # Record
    mileage_at_service = models.IntegerField()
    next_service_mileage = models.IntegerField(
        null=True,
        blank=True,
        verbose_name="Next Service Mileage",
        help_text="Mileage at which the next service should occur.",
    )
    date = models.DateField(auto_now_add=True, verbose_name="Service Date")
    next_service_due = models.DateField(null=True, verbose_name="Next Service Due")
    # GoMotive sync metadata
    gomotive_alert_id = models.CharField(max_length=64, null=True, blank=True, unique=True, verbose_name="GoMotive Alert ID")

    @property
    def status_group(self):
        from datetime import date
        if self.next_service_due:
            days_left = (self.next_service_due - date.today()).days
            if days_left < 0: return ("1_OVERDUE", "Overdue")
            if days_left <= 7: return ("2_SOON", "Due Soon")
            if days_left <= 30: return ("3_NEXT_30_DAYS", "Next 30 Days")
            return ("4_SAFE", "Safe")
        if self.next_service_mileage and self.vehicle.current_odometer:
            miles_left = self.next_service_mileage - self.vehicle.current_odometer
            if miles_left < 0: return ("1_OVERDUE", "Overdue")
            if miles_left <= 500: return ("2_SOON", "Due Soon")
            if miles_left <= 2000: return ("3_NEXT_30_DAYS", "Next 30 Days")
        return ("4_SAFE", "Safe")


