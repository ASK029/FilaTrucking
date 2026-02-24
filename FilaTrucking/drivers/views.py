from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    UpdateView,
)

from .models import Driver


class DriverListView(LoginRequiredMixin, ListView):
    model = Driver
    template_name = "drivers/driver_list.html"
    context_object_name = "drivers"


class DriverDetailView(LoginRequiredMixin, DetailView):
    model = Driver
    template_name = "drivers/driver_detail.html"
    context_object_name = "driver"


class DriverCreateView(LoginRequiredMixin, CreateView):
    model = Driver
    template_name = "drivers/driver_form.html"
    fields = "__all__"
    success_url = reverse_lazy("driver_list")


class DriverUpdateView(LoginRequiredMixin, UpdateView):
    model = Driver
    template_name = "drivers/driver_form.html"
    fields = "__all__"
    success_url = reverse_lazy("driver_list")


class DriverDeleteView(LoginRequiredMixin, DeleteView):
    model = Driver
    template_name = "drivers/driver_confirm_delete.html"
    success_url = reverse_lazy("driver_list")
