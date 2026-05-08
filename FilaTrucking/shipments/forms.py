from django import forms
from django.forms import inlineformset_factory
from datetime import date, timedelta

from FilaTrucking.utils import TailwindFormMixin
from .models import Expense, Invoice, InvoiceLineItem, Shipment, SystemSettings, ShipmentStatus


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

    def clean(self):
        cleaned_data = super().clean()
        amount = cleaned_data.get('amount')
        customer = cleaned_data.get('customer')

        if amount is None and customer and customer.default_rate is not None:
            cleaned_data['amount'] = customer.default_rate

        return cleaned_data

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
    start_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Shipments From",
    )
    end_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Shipments To",
    )

    class Meta:
        model = Invoice
        fields = ["customer", "invoice_date", "status", "start_date", "end_date"]
        widgets = {
            "invoice_date": forms.DateInput(attrs={"type": "date"}),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        today = date.today()
        week_ago = today - timedelta(days=7)
        
        if not self.instance.pk:
            if 'invoice_date' not in self.data:
                self.fields['invoice_date'].initial = today
            if 'start_date' not in self.data:
                self.fields['start_date'].initial = week_ago
            if 'end_date' not in self.data:
                self.fields['end_date'].initial = today


class InvoiceLineItemForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = InvoiceLineItem
        fields = [
            "shipment", "date_incurred", "description",
            "booking_no", "container_no", "seal_no", "location", "amount",
        ]
        widgets = {
            "date_incurred": forms.DateInput(attrs={"type": "date"}),
            "description": forms.TextInput(attrs={"placeholder": "e.g. Booking # or Yard Rent"}),
        }

    def clean(self):
        cleaned_data = super().clean()
        shipment = cleaned_data.get("shipment")
        if not shipment:
            return cleaned_data

        if not cleaned_data.get("date_incurred"):
            cleaned_data["date_incurred"] = shipment.date
        if not cleaned_data.get("description"):
            cleaned_data["description"] = f"Shipment {shipment.container}"
        if not cleaned_data.get("booking_no"):
            cleaned_data["booking_no"] = shipment.booking
        if not cleaned_data.get("container_no"):
            cleaned_data["container_no"] = shipment.container
        if not cleaned_data.get("seal_no"):
            cleaned_data["seal_no"] = shipment.seal
        if not cleaned_data.get("location"):
            cleaned_data["location"] = shipment.location
        if cleaned_data.get("amount") in (None, ""):
            cleaned_data["amount"] = shipment.amount

        return cleaned_data


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
    extra=1,
    can_delete=True,
)
