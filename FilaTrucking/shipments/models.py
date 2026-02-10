from django.db import models

from FilaTrucking.customers.models import Customer
from FilaTrucking.drivers.models import Driver
from FilaTrucking.vehicles.models import Vehicle

# Create your models here.
class Shipment(models.Model):
    driver = models.ForeignKey(Driver, on_delete=models.CASCADE, verbose_name='Shipment Driver')
    customer = models.ForeignKey(Customer,  on_delete=models.CASCADE, verbose_name='Shipment Customer')
    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, verbose_name='Shipment Vehicle')
    
    date = models.DateField(auto_now_add=True)
    booking = models.CharField()
    container = models.CharField()
    seal = models.CharField()
    location = models.CharField()
    amount = models.IntegerField()
