from django.db import models

from ..drivers.models import Driver

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
    fuel_efficiency = models.DecimalField(decimal_places=2) # mil/L
    status = models.CharField(max_length=2, choices=Status, default=Status.Available)
    # Mileage
    
    # Ownership
    ownership_type = models.CharField(max_length=2, choices=Ownership, default=Ownership.CompanyOwned)
    # Image
    image = models.ImageField(upload_to='vehicle_images/', verbose_name="Vehicle Image")


class maintainance(models.Model):
    # Vehicle
    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, verbose_name='Driver')

    cost = models.DecimalField(decimal_places=2)
    service_provider = models.CharField(max_length=40)
    type = models.CharField(max_length=40, varbose_name="Service Type")
    description = models.TextField()
    # Record
    mileage_at_service = models.IntegerField()
    date = models.DateField(auto_now_add=True, verbose_name="Service Date")
    next_service_due = models.DateField(null=True, verbose_name="Next Service Due")


