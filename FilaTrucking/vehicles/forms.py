from django import forms
from .models import Vehicle, IFTAMileage, Maintenance
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
        model = IFTAMileage
        fields = ["vehicle", "state_code", "quarter", "year", "miles"]
