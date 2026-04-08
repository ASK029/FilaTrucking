from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.urls import reverse_lazy
from django.views.generic import (
    CreateView,
    DetailView,
    ListView,
    UpdateView,
)

from .models import Customer
from .forms import CustomerForm


class CustomerListView(LoginRequiredMixin, ListView):
    model = Customer
    template_name = "customers/customer_list.html"
    context_object_name = "customers"

    def get_queryset(self):
        qs = super().get_queryset()
        query = self.request.GET.get("q")
        if query:
            qs = qs.filter(
                Q(name__icontains=query) |
                Q(abbreviation__icontains=query) |
                Q(email__icontains=query)
            )
        return qs


class CustomerDetailView(LoginRequiredMixin, DetailView):
    model = Customer
    template_name = "customers/customer_detail.html"
    context_object_name = "customer"


class CustomerCreateView(LoginRequiredMixin, CreateView):
    model = Customer
    template_name = "customers/customer_form.html"
    form_class = CustomerForm
    success_url = reverse_lazy("customer_list")


class CustomerUpdateView(LoginRequiredMixin, UpdateView):
    model = Customer
    template_name = "customers/customer_form.html"
    form_class = CustomerForm
    success_url = reverse_lazy("customer_list")

