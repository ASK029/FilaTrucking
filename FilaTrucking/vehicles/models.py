from django.db import models

from drivers.models import Driver

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
    year = models.IntegerField(max_length=4)
    chassis_number = models.CharField(max_length=30)
    engine_number = models.CharField(max_length=30)
    fuel_efficiency = models.DecimalField(max_digits=10, decimal_places=2)  # mil/L
    average_mpg = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True,
        verbose_name="Average MPG (for IFTA)"
    )
    status = models.CharField(max_length=2, choices=Status, default=Status.Available)
    # Mileage

    # Ownership
    ownership_type = models.CharField(max_length=2, choices=Ownership, default=Ownership.CompanyOwned)
    # Image
    image = models.ImageField(upload_to='vehicle_images/', verbose_name="Vehicle Image")


class IFTAMileageLog(models.Model):
    """Monthly mileage per state for quarterly IFTA reporting."""
    truck = models.ForeignKey(Vehicle, on_delete=models.CASCADE, verbose_name="Truck")
    month = models.IntegerField(choices=[(i, str(i)) for i in range(1, 13)])
    year = models.IntegerField()
    state_code = models.CharField(max_length=2)
    miles_driven = models.DecimalField(max_digits=12, decimal_places=2)
    calculated_gallons = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )

    class Meta:
        verbose_name = "IFTA Mileage Log"
        verbose_name_plural = "IFTA Mileage Logs"
        ordering = ["-year", "-month", "state_code"]
        unique_together = [["truck", "month", "year", "state_code"]]

    def save(self, *args, **kwargs):
        if self.truck.average_mpg and self.truck.average_mpg > 0:
            self.calculated_gallons = self.miles_driven / self.truck.average_mpg
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.truck.name} – {self.year}-{self.month:02d} – {self.state_code}"


class Maintenance(models.Model):
    # Vehicle
    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, verbose_name="Vehicle")

    cost = models.DecimalField(max_digits=10, decimal_places=2)
    service_provider = models.CharField(max_length=40)
    type = models.CharField(max_length=40, verbose_name="Service Type")
    description = models.TextField()
    # Record
    mileage_at_service = models.IntegerField()
    date = models.DateField(auto_now_add=True, verbose_name="Service Date")
    next_service_due = models.DateField(null=True, verbose_name="Next Service Due")


