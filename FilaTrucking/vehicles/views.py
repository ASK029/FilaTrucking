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

from .models import IFTAMileage, Vehicle
from .forms import VehicleForm, IFTAMileageLogForm

class VehicleListView(LoginRequiredMixin, ListView):
    model = Vehicle
    template_name = "vehicles/vehicle_list.html"
    context_object_name = "vehicles"


class VehicleDetailView(LoginRequiredMixin, DetailView):
    model = Vehicle
    template_name = "vehicles/vehicle_detail.html"
    context_object_name = "vehicle"


class VehicleCreateView(LoginRequiredMixin, CreateView):
    model = Vehicle
    template_name = "vehicles/vehicle_form.html"
    form_class = VehicleForm
    success_url = reverse_lazy("vehicle_list")


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
