from django import forms
from django.forms import inlineformset_factory
from datetime import date

from FilaTrucking.utils import TailwindFormMixin
from .models import Expense, Invoice, InvoiceLineItem, Shipment, SystemSettings


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

class ExpenseForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = Expense
        fields = [
            'date', 'category', 'amount', 'vehicle', 'driver', 'notes', 'receipt'
        ]
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk and 'date' not in self.data:
            self.fields['date'].widget.attrs['value'] = date.today().strftime('%Y-%m-%d')

class InvoiceForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = Invoice
        fields = ["customer", "invoice_date", "status"]
        widgets = {
            "invoice_date": forms.DateInput(attrs={"type": "date"}),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk and 'invoice_date' not in self.data:
            self.fields['invoice_date'].widget.attrs['value'] = date.today().strftime('%Y-%m-%d')


class InvoiceLineItemForm(TailwindFormMixin, forms.ModelForm):
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


class SystemSettingsForm(TailwindFormMixin, forms.ModelForm):
    motive_api_key = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=True, attrs={'placeholder': 'Paste your Motive API key here'}),
        label="Motive API Key"
    )
    email_password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=True, attrs={'placeholder': 'Enter email app password'}),
        label="Email Password"
    )

    class Meta:
        model = SystemSettings
        fields = ['email_host_user', 'email_from_email']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Reorder fields to look better
        field_order = ['motive_api_key', 'email_host_user', 'email_password', 'email_from_email']
        self.order_fields(field_order)
        
        if self.instance and self.instance.pk:
            # Pre-fill from encrypted storage
            self.fields['motive_api_key'].initial = self.instance.get_motive_api_key()
            self.fields['email_password'].initial = self.instance.get_email_password()

    def save(self, commit=True):
        instance = super().save(commit=False)
        motive_key = self.cleaned_data.get('motive_api_key')
        email_pass = self.cleaned_data.get('email_password')
        
        if motive_key is not None:
            instance.set_motive_api_key(motive_key)
        if email_pass is not None:
            instance.set_email_password(email_pass)
            
        if commit:
            instance.save()
        return instance


InvoiceLineItemFormSet = inlineformset_factory(
    Invoice,
    InvoiceLineItem,
    form=InvoiceLineItemForm,
    extra=2,
    can_delete=True,
)
