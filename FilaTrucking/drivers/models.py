from django.db import models
from phonenumber_field.modelfields import PhoneNumberField

# Create your models here.
class Driver(models.Model):
    name = models.CharField(max_length=20, verbose_name="Driver Name")
    phone_number = PhoneNumberField(region= 'US')
    license_number = models.CharField(max_length=20, unique=True)
    license_expiry = models.DateField(null=True)
    joined_at = models.DateField(auto_now_add=True)

class DriverDocument(models.Model):
    driver = models.ForeignKey(Driver, on_delete=models.CASCADE, verbose_name='Driver')
    name = models.CharField(max_length=20, verbose_name='Document Name')
    document = models.FileField(upload_to='driver_documents/')
