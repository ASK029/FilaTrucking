from django.db import models
from django.db.models import Sum
from decimal import Decimal

from customers.models import Customer
from drivers.models import Driver
from vehicles.models import Vehicle


class Shipment(models.Model):
    driver = models.ForeignKey(Driver, on_delete=models.CASCADE, verbose_name='Shipment Driver')
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, verbose_name='Shipment Customer')
    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, verbose_name='Shipment Vehicle')

    date = models.DateField(auto_now_add=True)
    booking = models.CharField(max_length=10)
    container = models.CharField(max_length=10)
    seal = models.CharField(max_length=10)
    location = models.CharField(max_length=10)
    amount = models.IntegerField()


class InvoiceStatus(models.TextChoices):
    DRAFT = "Draft", "Draft"
    SENT = "Sent", "Sent"
    PAID = "Paid", "Paid"


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

    def __str__(self):
        return f"Invoice #{self.pk} – {self.customer.name} – {self.invoice_date}"

    def calculate_total(self):
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

    def __str__(self):
        return f"{self.description} – {self.amount}"
