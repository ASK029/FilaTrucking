from django.db import models

# Create your models here.
class Customer(models.Model):
    name = models.CharField()
    abbreviation = models.CharField()
    # phone_number = models.PhoneNumberField(_(""))
    street = models.CharField()
    address1 = models.CharField()
    address2 = models.CharField()
    email = models.EmailField(verbose_name='Customer Email')