from django.db import models
from phonenumber_field.modelfields import PhoneNumberField


class DriverStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    INACTIVE = "inactive", "Inactive"


class Driver(models.Model):
    name = models.CharField(max_length=100, verbose_name="Driver Name")
    phone_number = PhoneNumberField(region="US")
    license_number = models.CharField(max_length=20, unique=True)
    license_expiry = models.DateField(null=True, blank=True)
    joined = models.DateField(null=True, blank=True, verbose_name="Date Joined")
    status = models.CharField(
        max_length=10,
        choices=DriverStatus,
        default=DriverStatus.ACTIVE,
        verbose_name="Status",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class DriverDocument(models.Model):
    driver = models.ForeignKey(
        Driver,
        on_delete=models.CASCADE,
        related_name="documents",
        verbose_name="Driver",
    )
    name = models.CharField(max_length=100, verbose_name="Document Name")
    document = models.FileField(upload_to="driver_documents/")

    def __str__(self) -> str:
        return f"{self.driver.name} – {self.name}"
