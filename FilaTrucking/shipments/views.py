from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views.generic import CreateView, DetailView, ListView

from .forms import InvoiceForm, InvoiceLineItemForm, InvoiceLineItemFormSet
from .models import Invoice, InvoiceLineItem


class InvoiceListView(LoginRequiredMixin, ListView):
    model = Invoice
    template_name = "shipments/invoice_list.html"
    context_object_name = "invoices"
    paginate_by = 20


class InvoiceDetailView(LoginRequiredMixin, DetailView):
    model = Invoice
    template_name = "shipments/invoice_detail.html"
    context_object_name = "invoice"


def invoice_create(request):
    """Create invoice for a customer; then add line items (formset)."""
    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.get_full_path())

    if request.method == "POST":
        form = InvoiceForm(request.POST)
        if form.is_valid():
            invoice = form.save()
            return redirect("invoice_add_lines", invoice_pk=invoice.pk)
    else:
        form = InvoiceForm()

    return render(request, "shipments/invoice_form.html", {"form": form})


def invoice_add_lines(request, invoice_pk):
    """Add or edit line items for an existing invoice (formset)."""
    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.get_full_path())

    invoice = get_object_or_404(Invoice, pk=invoice_pk)
    if request.method == "POST":
        formset = InvoiceLineItemFormSet(request.POST, instance=invoice)
        if formset.is_valid():
            formset.save()
            invoice.calculate_total()
            return redirect("invoice_detail", pk=invoice.pk)
    else:
        formset = InvoiceLineItemFormSet(instance=invoice)

    return render(request, "shipments/invoice_add_lines.html", {
        "invoice": invoice,
        "formset": formset,
    })


class InvoiceLineItemCreateView(LoginRequiredMixin, CreateView):
    model = InvoiceLineItem
    form_class = InvoiceLineItemForm
    template_name = "shipments/invoice_line_item_form.html"

    def get_initial(self):
        initial = super().get_initial()
        invoice = get_object_or_404(Invoice, pk=self.kwargs["invoice_pk"])
        initial["invoice"] = invoice
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["invoice"] = get_object_or_404(Invoice, pk=self.kwargs["invoice_pk"])
        return context

    def form_valid(self, form):
        invoice = get_object_or_404(Invoice, pk=self.kwargs["invoice_pk"])
        form.instance.invoice = invoice
        result = super().form_valid(form)
        invoice.calculate_total()
        return result

    def get_success_url(self):
        return reverse("invoice_detail", kwargs={"pk": self.kwargs["invoice_pk"]})
