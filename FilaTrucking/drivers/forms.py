from django import forms
from django.forms import inlineformset_factory
from .models import Driver, DriverDocument

from FilaTrucking.utils import TailwindFormMixin


class DriverForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = Driver
        fields = [
            'name',
            'phone_number',
            'license_number',
            'license_expiry',
            'status',
        ]
        widgets = {
            'license_expiry': forms.DateInput(attrs={'type': 'date'}),
        }


class DriverDocumentForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = DriverDocument
        fields = ['name', 'document']


DriverDocumentFormSet = inlineformset_factory(
    Driver,
    DriverDocument,
    form=DriverDocumentForm,
    can_delete=True,
    extra=1,
)