from django import forms
from .models import Vehicle, IFTAMileageLog, Maintenance
from FilaTrucking.utils import TailwindFormMixin


class VehicleForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = Vehicle
        fields = [
            'driver', 'registration_number', 'name', 'Manufacturer', 
            'model', 'year', 'chassis_number', 'engine_number', 
            'average_mpg', 'status', 'ownership_type', 'image'
        ]


class IFTAMileageLogForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = IFTAMileageLog
        fields = ['truck', 'month', 'year', 'state_code', 'miles_driven']
