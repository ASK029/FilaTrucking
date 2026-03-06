from django import forms
from .models import Customer
from FilaTrucking.utils import TailwindFormMixin

class CustomerForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = Customer
        fields = '__all__'
        widgets = {
            'notes': forms.Textarea(attrs={'rows': 3}),
        }
