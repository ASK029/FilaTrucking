from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Sum
from django.shortcuts import get_object_or_404, render
from django.urls import reverse_lazy
from django.views.generic import CreateView, ListView

from .models import IFTAMileageLog, Vehicle


class IFTALogCreateView(LoginRequiredMixin, CreateView):
    model = IFTAMileageLog
    template_name = "vehicles/ifta_log_form.html"
    fields = ["truck", "month", "year", "state_code", "miles_driven"]
    success_url = reverse_lazy("ifta_log_list")


class IFTALogListView(LoginRequiredMixin, ListView):
    model = IFTAMileageLog
    template_name = "vehicles/ifta_log_list.html"
    context_object_name = "logs"
    paginate_by = 50


def ifta_report(request):
    """IFTA report view: filter by year and quarter (months 1-3, 4-6, 7-9, 10-12)."""
    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.get_full_path())

    year = request.GET.get("year")
    quarter = request.GET.get("quarter")
    context = {"year": year, "quarter": quarter, "summary": []}

    if year and quarter:
        try:
            year = int(year)
            q = int(quarter)
            if 1 <= q <= 4:
                start_month = (q - 1) * 3 + 1
                end_month = start_month + 2
                months = list(range(start_month, end_month + 1))
                logs = (
                    IFTAMileageLog.objects.filter(year=year, month__in=months)
                    .values("truck__id", "truck__name", "state_code")
                    .annotate(
                        total_miles=Sum("miles_driven"),
                        total_gallons=Sum("calculated_gallons"),
                    )
                    .order_by("truck__name", "state_code")
                )
                context["summary"] = list(logs)
                context["quarter_label"] = f"Q{q} ({start_month}-{end_month})"
        except (ValueError, TypeError):
            pass

    return render(request, "vehicles/ifta_report.html", context)
