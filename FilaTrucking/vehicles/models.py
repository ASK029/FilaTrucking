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
    driver = models.ForeignKey(Driver, on_delete=models.CASCADE, verbose_name='Driver')
    assigned_at = models.DateField(auto_now_add=True, verbose_name="Assigned At")
    # Vehicle Specification
    registration_number = models.CharField(max_length=20)
    name = models.CharField(max_length=30)
    Manufacturer = models.CharField(max_length=20)
    model = models.CharField(max_length=30)
    year = models.IntegerField(verbose_name="Year")
    chassis_number = models.CharField(max_length=30)
    engine_number = models.CharField(max_length=30)
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
    image = models.ImageField(upload_to='vehicle_images/', verbose_name="Vehicle Image")


class IFTAMileage(models.Model):
    """Quarterly mileage per state for IFTA reporting."""

    QUARTER_CHOICES = (
        (1, "Q1 (Jan–Mar)"),
        (2, "Q2 (Apr–Jun)"),
        (3, "Q3 (Jul–Sep)"),
        (4, "Q4 (Oct–Dec)"),
    )

    vehicle = models.ForeignKey(
        Vehicle,
        on_delete=models.CASCADE,
        verbose_name="Vehicle",
        related_name="ifta_mileage_entries",
    )
    state_code = models.CharField(max_length=2, verbose_name="State")
    quarter = models.IntegerField(choices=QUARTER_CHOICES, verbose_name="Quarter")
    year = models.IntegerField(verbose_name="Year")
    miles = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name="Miles Driven",
    )
    calculated_gallons = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Computed Gallons (from MPG)",
    )

    class Meta:
        verbose_name = "IFTA Mileage Entry"
        verbose_name_plural = "IFTA Mileage Entries"
        ordering = ["-year", "-quarter", "state_code"]
        unique_together = [["vehicle", "state_code", "quarter", "year"]]

    def save(self, *args, **kwargs):
        if self.vehicle.average_mpg and self.vehicle.average_mpg > 0:
            self.calculated_gallons = self.miles / self.vehicle.average_mpg
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.vehicle.name} – Q{self.quarter} {self.year} – {self.state_code}"


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


