from django import forms
from .models import Vehicle, IFTAMileage, Maintenance
from FilaTrucking.utils import TailwindFormMixin

US_STATES = [
    ("", "Select State"),
    ("AL", "Alabama"), ("AK", "Alaska"), ("AZ", "Arizona"), ("AR", "Arkansas"),
    ("CA", "California"), ("CO", "Colorado"), ("CT", "Connecticut"), ("DE", "Delaware"),
    ("FL", "Florida"), ("GA", "Georgia"), ("HI", "Hawaii"), ("ID", "Idaho"),
    ("IL", "Illinois"), ("IN", "Indiana"), ("IA", "Iowa"), ("KS", "Kansas"),
    ("KY", "Kentucky"), ("LA", "Louisiana"), ("ME", "Maine"), ("MD", "Maryland"),
    ("MA", "Massachusetts"), ("MI", "Michigan"), ("MN", "Minnesota"), ("MS", "Mississippi"),
    ("MO", "Missouri"), ("MT", "Montana"), ("NE", "Nebraska"), ("NV", "Nevada"),
    ("NH", "New Hampshire"), ("NJ", "New Jersey"), ("NM", "New Mexico"), ("NY", "New York"),
    ("NC", "North Carolina"), ("ND", "North Dakota"), ("OH", "Ohio"), ("OK", "Oklahoma"),
    ("OR", "Oregon"), ("PA", "Pennsylvania"), ("RI", "Rhode Island"), ("SC", "South Carolina"),
    ("SD", "South Dakota"), ("TN", "Tennessee"), ("TX", "Texas"), ("UT", "Utah"),
    ("VT", "Vermont"), ("VA", "Virginia"), ("WA", "Washington"), ("WV", "West Virginia"),
    ("WI", "Wisconsin"), ("WY", "Wyoming"),
]


class VehicleForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = Vehicle
        fields = [
            'driver', 'registration_number', 'Manufacturer', 
            'model', 'year', 'chassis_number',
            'average_mpg', 'status', 'ownership_type', 'image'
        ]


class IFTAMilesLogForm(TailwindFormMixin, forms.ModelForm):
    state_code = forms.ChoiceField(
        choices=US_STATES,
        label="State",
        initial="IL",
    )

    class Meta:
        model = IFTAMileage
        fields = ["vehicle", "state_code", "month", "year", "miles"]

class IFTAGallonsLogForm(TailwindFormMixin, forms.ModelForm):
    state_code = forms.ChoiceField(
        choices=US_STATES,
        label="State",
        initial="IL",
    )

    class Meta:
        model = IFTAMileage
        fields = ["vehicle", "state_code", "month", "year", "gallons"]


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
            "gomotive_alert_id",
        ]
        widgets = {
            "next_service_due": forms.DateInput(attrs={"type": "date"}),
        }
