from django import forms
from .models import Customer
from FilaTrucking.utils import TailwindFormMixin

class CustomerForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = Customer
        exclude = ['country']
        widgets = {
            'notes': forms.Textarea(attrs={'rows': 3}),
        }
