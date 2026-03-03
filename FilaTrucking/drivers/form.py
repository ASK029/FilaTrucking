from django import forms
from django.forms import inlineformset_factory
from .models import Driver, DriverDocument

class DriverForm(forms.ModelForm):
    class Meta:
        model = Driver
        fields = ['name', 'phone_number', 'license_number', 'license_expiry'
        # , 'joined_at'
        ]
        # Adding a date picker widget for the expiry date makes it user-friendly
        widgets = {
            'license_expiry': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            # 'joined_at': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        }

class DriverDocumentForm(forms.ModelForm):
    class Meta:
        model = DriverDocument
        fields = ['name', 'document']

# Create the inline formset
# 'extra=1' means it will show one blank document row by default. 
# You can increase this if you want users to upload multiple docs at once.
DriverDocumentFormSet = inlineformset_factory(
    Driver, 
    DriverDocument, 
    form=DriverDocumentForm, 
    can_delete=True
)