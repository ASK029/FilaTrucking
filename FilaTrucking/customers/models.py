from django.db import models
from phonenumber_field.modelfields import PhoneNumberField


# Create your models here.
class Customer(models.Model):
    name = models.CharField(max_length=20)
    abbreviation = models.CharField(max_length=20)
    phone_number = PhoneNumberField(region="US")
    street = models.CharField(max_length=30)
    address1 = models.CharField(max_length=30)
    address2 = models.CharField(max_length=30)
    email = models.EmailField(verbose_name='Customer Email')