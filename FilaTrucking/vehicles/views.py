from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Sum
from django.shortcuts import render
from django.urls import reverse_lazy
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    UpdateView,
)

from .models import IFTAMileage, Vehicle, Maintenance
from .forms import VehicleForm, IFTAMileageLogForm, MaintenanceForm


class VehicleListView(LoginRequiredMixin, ListView):
    model = Vehicle
    template_name = "vehicles/vehicle_list.html"
    context_object_name = "vehicles"


class VehicleDetailView(LoginRequiredMixin, DetailView):
    model = Vehicle
    template_name = "vehicles/vehicle_detail.html"
    context_object_name = "vehicle"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['ifta_logs'] = IFTAMileage.objects.filter(vehicle=self.object).order_by('-year', '-quarter')[:5]
        context['maintenance_logs'] = Maintenance.objects.filter(vehicle=self.object).order_by('-id')[:5]
        return context


class VehicleCreateView(LoginRequiredMixin, CreateView):
    model = Vehicle
    template_name = "vehicles/vehicle_form.html"
    form_class = VehicleForm
    success_url = reverse_lazy("vehicle_list")

    def get_initial(self):
        initial = super().get_initial()
        if self.request.GET.get("gomotive_id"):
            initial["gomotive_id"] = self.request.GET.get("gomotive_id")
        if self.request.GET.get("name"):
            initial["name"] = self.request.GET.get("name")
        if self.request.GET.get("vin"):
            initial["registration_number"] = self.request.GET.get("vin")
            initial["chassis_number"] = self.request.GET.get("vin")
        if self.request.GET.get("make"):
            initial["Manufacturer"] = self.request.GET.get("make")
        if self.request.GET.get("model"):
            initial["model"] = self.request.GET.get("model")
        return initial


class VehicleUpdateView(LoginRequiredMixin, UpdateView):
    model = Vehicle
    template_name = "vehicles/vehicle_form.html"
    form_class = VehicleForm
    success_url = reverse_lazy("vehicle_list")


class VehicleDeleteView(LoginRequiredMixin, DeleteView):
    model = Vehicle
    template_name = "vehicles/vehicle_confirm_delete.html"
    success_url = reverse_lazy("vehicle_list")


class IFTALogCreateView(LoginRequiredMixin, CreateView):
    model = IFTAMileage
    template_name = "vehicles/ifta_log_form.html"
    form_class = IFTAMileageLogForm
    success_url = reverse_lazy("ifta_log_list")


class IFTALogListView(LoginRequiredMixin, ListView):
    model = IFTAMileage
    template_name = "vehicles/ifta_log_list.html"
    context_object_name = "logs"
    paginate_by = 50


def ifta_report(request):
    """IFTA report view: filter by year and quarter and calculate taxes."""
    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.get_full_path())

    year = request.GET.get("year")
    quarter = request.GET.get("quarter")
    context = {"year": year, "quarter": quarter, "summary": []}

    if year and quarter:
        try:
            year_int = int(year)
            q = int(quarter)
            if 1 <= q <= 4:
                entries = (
                    IFTAMileage.objects.filter(year=year_int, quarter=q)
                    .values("vehicle__id", "vehicle__name", "state_code")
                    .annotate(
                        total_miles=Sum("miles"),
                        total_gallons=Sum("calculated_gallons"),
                    )
                    .order_by("vehicle__name", "state_code")
                )

                summary = []
                for row in entries:
                    rate_obj = IFTARate.objects.filter(
                        state_code=row["state_code"],
                        year=year_int,
                        quarter=q,
                    ).first()
                    rate = rate_obj.rate if rate_obj else None
                    gallons = row["total_gallons"]
                    tax_owed = gallons * rate if (gallons and rate) else None
                    summary.append(
                        {
                            **row,
                            "rate": rate,
                            "tax_owed": tax_owed,
                        }
                    )

                context["summary"] = summary
                context["quarter_label"] = f"Q{q}"
                context["year"] = year_int
        except (ValueError, TypeError):
            pass

    return render(request, "vehicles/ifta_report.html", context)


class MaintenanceListView(LoginRequiredMixin, ListView):
    model = Maintenance
    template_name = "vehicles/maintenance_list.html"
    context_object_name = "logs"
    paginate_by = 50


class MaintenanceCreateView(LoginRequiredMixin, CreateView):
    model = Maintenance
    form_class = MaintenanceForm
    template_name = "vehicles/maintenance_form.html"
    success_url = reverse_lazy("maintenance_list")


class MaintenanceUpdateView(LoginRequiredMixin, UpdateView):
    model = Maintenance
    form_class = MaintenanceForm
    template_name = "vehicles/maintenance_form.html"
    success_url = reverse_lazy("maintenance_list")


class MaintenanceDeleteView(LoginRequiredMixin, DeleteView):
    model = Maintenance
    template_name = "vehicles/maintenance_confirm_delete.html"
    success_url = reverse_lazy("maintenance_list")


from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from .gomotive_client import get_client
from django.db import transaction

@login_required
def gomotive_vehicles_view(request):
    try:
        client = get_client()
        if not client.api_key:
            messages.warning(request, "GoMotive API key is not configured. Please add it in System Settings.")
            vehicles = []
        else:
            vehicles_response = client._get("vehicles")
            vehicles = vehicles_response.get("vehicles", []) if vehicles_response else []
    except Exception as e:
        messages.error(request, f"Failed to fetch vehicles from GoMotive: {str(e)}")
        vehicles = []

    return render(request, "vehicles/gomotive_vehicles.html", {"vehicles": vehicles})

@login_required
@require_POST
def sync_vehicle_gomotive(request, pk):
    vehicle = get_object_or_404(Vehicle, pk=pk)
    
    if not vehicle.gomotive_id:
        messages.error(request, f"Vehicle {vehicle.name} has no GoMotive ID set.")
        return redirect("vehicle_detail", pk=pk)

    try:
        client = get_client()
        odometer = client.fetch_odometer_reading(vehicle.gomotive_id)
        alerts = client.fetch_maintenance_alerts(vehicle.gomotive_id)

        with transaction.atomic():
            if odometer is not None:
                vehicle.current_odometer = odometer.odometer
                vehicle.save(update_fields=["current_odometer"])

            created_alerts = 0
            for alert in alerts:
                if alert.alert_id:
                    _, created = Maintenance.objects.update_or_create(
                        gomotive_alert_id=alert.alert_id,
                        defaults={
                            'vehicle': vehicle,
                            'cost': 0,
                            'service_provider': "GoMotive",
                            'type': alert.service_type,
                            'description': alert.description,
                            'mileage_at_service': alert.mileage_at_service or (vehicle.current_odometer or 0),
                            'next_service_mileage': alert.next_service_mileage,
                        }
                    )
                    if created:
                        created_alerts += 1
                else:
                    Maintenance.objects.create(
                        vehicle=vehicle,
                        cost=0,
                        service_provider="GoMotive",
                        type=alert.service_type,
                        description=alert.description,
                        mileage_at_service=alert.mileage_at_service or (vehicle.current_odometer or 0),
                        next_service_mileage=alert.next_service_mileage,
                    )
                    created_alerts += 1
            
            messages.success(request, f"Successfully synced data from GoMotive. Odometer updated. {created_alerts} new alerts added.")
            
    except Exception as e:
        messages.error(request, f"Failed to sync with GoMotive: {str(e)}")

    return redirect("vehicle_detail", pk=pk)
