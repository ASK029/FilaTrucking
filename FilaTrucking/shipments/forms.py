from django import forms
from django.forms import inlineformset_factory

from FilaTrucking.utils import TailwindFormMixin
from .models import Invoice, InvoiceLineItem, Shipment


class ShipmentForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = Shipment
        fields = [
            'date', 'booking', 'container', 'seal', 'location', 
            'customer', 'driver', 'vehicle', 'amount', 'status', 'is_flagged', 'notes'
        ]
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 3}),
            'is_flagged': forms.CheckboxInput(attrs={'class': 'w-4 h-4 text-accent border-slate-600 rounded bg-slate-800 focus:ring-accent focus:ring-1 border-gray-300 dark:border-slate-600 focus:ring-accent focus:ring-1'})
        }

class InvoiceForm(forms.ModelForm):
    class Meta:
        model = Invoice
        fields = ["customer", "invoice_date", "status"]
        widgets = {
            "invoice_date": forms.DateInput(attrs={"type": "date"}),
        }


class InvoiceLineItemForm(forms.ModelForm):
    class Meta:
        model = InvoiceLineItem
        fields = [
            "shipment", "date_incurred", "description",
            "container_no", "seal_no", "location", "amount",
        ]
        widgets = {
            "date_incurred": forms.DateInput(attrs={"type": "date"}),
            "description": forms.TextInput(attrs={"placeholder": "e.g. Booking # or Yard Rent"}),
        }


InvoiceLineItemFormSet = inlineformset_factory(
    Invoice,
    InvoiceLineItem,
    form=InvoiceLineItemForm,
    extra=2,
    can_delete=True,
)
