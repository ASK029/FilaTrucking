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


class MaintenanceForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = Maintenance
        fields = [
            "vehicle",
            "cost",
            "service_provider",
            "type",
            "description",
            "mileage_at_service",
            "next_service_mileage",
            "next_service_due",
        ]
        widgets = {
            "next_service_due": forms.DateInput(attrs={"type": "date"}),
        }
