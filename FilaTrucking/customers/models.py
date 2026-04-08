from django.db import models
from phonenumber_field.modelfields import PhoneNumberField
from django.core.exceptions import ValidationError


class Customer(models.Model):
    name = models.CharField(max_length=100, verbose_name="Customer Name")
    abbreviation = models.CharField(max_length=20, verbose_name="Abbreviation / Short Name")
    phone_number = PhoneNumberField(region="US")
    address = models.CharField(max_length=100, default="")
    city_state = models.CharField(max_length=100, blank=True, default="", verbose_name="City & State")
    country = models.CharField(max_length=100, blank=True, default="United States")
    email = models.EmailField(verbose_name="Customer Email")
    notes = models.TextField(blank=True, verbose_name="Notes")
    default_rate = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Default Rate ($)",
        help_text="Default per-shipment rate for this customer (can be overridden per shipment).",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def delete(self, *args, **kwargs):
        if self.shipment_set.exists() or self.invoices.exists():
            raise ValidationError("Cannot delete a customer that has shipments or invoices.")
        return super().delete(*args, **kwargs)