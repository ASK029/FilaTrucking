from django import forms
from django.forms import inlineformset_factory
from django.utils import timezone
from datetime import date, timedelta

from FilaTrucking.utils import TailwindFormMixin
from .models import Expense, Invoice, InvoiceLineItem, Shipment, SystemSettings, ShipmentStatus


class ShipmentForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = Shipment
        fields = [
            'date', 'booking', 'container', 'seal', 'chassis', 'location',
            'customer', 'driver', 'vehicle', 'amount', 'status', 'is_flagged', 'flag_reason', 'notes'
        ]
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 3}),
            'flag_reason': forms.Textarea(attrs={'rows': 2}),
            'is_flagged': forms.CheckboxInput(attrs={'class': 'w-4 h-4 text-accent border-slate-600 rounded bg-slate-800 focus:ring-accent focus:ring-1 border-gray-300 dark:border-slate-600 focus:ring-accent focus:ring-1'})
        }

    def clean(self):
        cleaned_data = super().clean()
        amount = cleaned_data.get('amount')
        customer = cleaned_data.get('customer')

        if amount is None and customer and customer.default_rate is not None:
            cleaned_data['amount'] = customer.default_rate

        # a blank location means "the customer's abbreviation" (QA-051)
        if not (cleaned_data.get('location') or '').strip() and customer:
            cleaned_data['location'] = customer.abbreviation[:100]

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
            self.fields['date'].widget.attrs['value'] = timezone.localdate().strftime('%Y-%m-%d')

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
        today = timezone.localdate()
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

        # A shipment is billed once (QA-038): refuse it when a line of ANOTHER invoice already carries it.
        elsewhere = InvoiceLineItem.objects.filter(shipment=shipment).exclude(invoice_id=self.instance.invoice_id)
        if self.instance.pk:
            elsewhere = elsewhere.exclude(pk=self.instance.pk)
        taken = elsewhere.first() if self.instance.invoice_id else InvoiceLineItem.objects.filter(shipment=shipment).first()
        if taken:
            raise forms.ValidationError(
                f"Shipment {shipment.container} is already on invoice #{taken.invoice_id}. Remove it there first."
            )

        # blank fields are filled from the shipment (description = its notes, empty when there are none;
        # location = the customer's abbreviation when the shipment has none) (QA-051)
        defaults = shipment.invoice_line_defaults()
        for name in ("date_incurred", "description", "booking_no", "container_no", "seal_no", "location"):
            if not cleaned_data.get(name):
                cleaned_data[name] = defaults[name]
        if cleaned_data.get("amount") in (None, ""):
            cleaned_data["amount"] = defaults["amount"]

        return cleaned_data


class SystemSettingsForm(TailwindFormMixin, forms.ModelForm):
    """Stored secrets are NEVER rendered back into the page (QA-031): the fields stay empty, a hint says whether a value is
    stored, and an empty submission keeps the stored value. (They used to be written into the HTML in clear text.)"""

    motive_api_key = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False, attrs={'placeholder': 'Paste your Motive API key here', 'autocomplete': 'off'}),
        label="Motive API Key"
    )
    email_password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False, attrs={'placeholder': 'Enter email app password', 'autocomplete': 'new-password'}),
        label="Email Password"
    )
    clear_motive_api_key = forms.BooleanField(required=False, label="Remove the stored Motive API key")
    clear_email_password = forms.BooleanField(required=False, label="Remove the stored email password")

    class Meta:
        model = SystemSettings
        fields = ['company_name', 'company_address', 'company_phone', 'company_email', 'email_host_user', 'email_from_email']
        widgets = {'company_address': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Reorder fields to look better
        field_order = ['company_name', 'company_address', 'company_phone', 'company_email', 'motive_api_key', 'clear_motive_api_key', 'email_host_user', 'email_password',
                       'clear_email_password', 'email_from_email']
        self.order_fields(field_order)

        if self.instance and self.instance.pk:
            if self.instance.get_motive_api_key():
                self.fields['motive_api_key'].help_text = "A key is stored. Leave empty to keep it, or paste a new one to replace it."
            if self.instance.get_email_password():
                self.fields['email_password'].help_text = "A password is stored. Leave empty to keep it, or enter a new one to replace it."

    def save(self, commit=True):
        instance = super().save(commit=False)
        motive_key = self.cleaned_data.get('motive_api_key')
        email_pass = self.cleaned_data.get('email_password')

        if self.cleaned_data.get('clear_motive_api_key'):
            instance.set_motive_api_key("")
        elif motive_key:
            instance.set_motive_api_key(motive_key)
        if self.cleaned_data.get('clear_email_password'):
            instance.set_email_password("")
        elif email_pass:
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
