from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.mail import EmailMessage
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    UpdateView,
)

try:
    from weasyprint import HTML
except (ImportError, OSError):
    HTML = None

from .forms import (
    ExpenseForm,
    InvoiceForm,
    InvoiceLineItemForm,
    InvoiceLineItemFormSet,
    ShipmentForm,
)
from .models import Expense, Invoice, InvoiceLineItem, Shipment, ShipmentStatus


class ShipmentListView(LoginRequiredMixin, ListView):
    model = Shipment
    template_name = "shipments/shipment_list.html"
    context_object_name = "shipments"


class ShipmentDetailView(LoginRequiredMixin, DetailView):
    model = Shipment
    template_name = "shipments/shipment_detail.html"
    context_object_name = "shipment"


class ShipmentCreateView(LoginRequiredMixin, CreateView):
    model = Shipment
    form_class = ShipmentForm
    template_name = "shipments/shipment_form.html"
    success_url = reverse_lazy("shipment_list")


class ShipmentUpdateView(LoginRequiredMixin, UpdateView):
    model = Shipment
    form_class = ShipmentForm
    template_name = "shipments/shipment_form.html"
    success_url = reverse_lazy("shipment_list")


class ShipmentDeleteView(LoginRequiredMixin, DeleteView):
    model = Shipment
    template_name = "shipments/shipment_confirm_delete.html"
    success_url = reverse_lazy("shipment_list")


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
    
    # Handle pre-population from confirmed shipments
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    
    if request.method == "POST":
        formset = InvoiceLineItemFormSet(request.POST, instance=invoice)
        if formset.is_valid():
            formset.save()
            invoice.calculate_total()
            # Update shipment statuses to 'invoiced'
            for item in invoice.line_items.all():
                if item.shipment:
                    item.shipment.status = ShipmentStatus.INVOICED
                    item.shipment.save()
            return redirect("invoice_detail", pk=invoice.pk)
    else:
        # If date range provided, fetch confirmed shipments
        formset = InvoiceLineItemFormSet(instance=invoice)
        if (start_date or end_date) and invoice.line_items.count() == 0:
            q = Shipment.objects.filter(customer=invoice.customer, status=ShipmentStatus.CONFIRMED)
            if start_date:
                q = q.filter(date__gte=start_date)
            if end_date:
                q = q.filter(date__lte=end_date)
            
            shipments = q.order_by('date')
            
            # Create line items from shipments if none exist
            initial_data = []
            for s in shipments:
                initial_data.append({
                    'shipment': s,
                    'date_incurred': s.date,
                    'description': f"Shipment {s.container}",
                    'container_no': s.container,
                    'seal_no': s.seal,
                    'location': s.location,
                    'amount': s.amount,
                })
            
            if initial_data:
                # We use extra=len(initial_data) dynamically
                from django.forms import inlineformset_factory
                DynamicFormSet = inlineformset_factory(
                    Invoice, InvoiceLineItem, form=InvoiceLineItemForm,
                    extra=len(initial_data), can_delete=True
                )
                formset = DynamicFormSet(instance=invoice, initial=initial_data)

    return render(request, "shipments/invoice_add_lines.html", {
        "invoice": invoice,
        "formset": formset,
        "start_date": start_date,
        "end_date": end_date,
    })


def invoice_pdf(request, pk):
    """Generate PDF for an invoice using WeasyPrint."""
    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.get_full_path())

    invoice = get_object_or_404(Invoice, pk=pk)
    html_string = render_to_string("shipments/invoice_pdf.html", {"invoice": invoice})
    
    if HTML is None:
        return HttpResponse("WeasyPrint not installed. Cannot generate PDF.", status=500)
    
    html = HTML(string=html_string, base_url=request.build_absolute_uri("/"))
    pdf = html.write_pdf()
    
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="Invoice_{invoice.pk}.pdf"'
    return response


def invoice_email(request, pk):
    """Email the invoice PDF to the customer."""
    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.get_full_path())

    invoice = get_object_or_404(Invoice, pk=pk)
    customer = invoice.customer

    if not customer.email:
        messages.error(request, f"Customer {customer.name} has no email address.")
        return redirect("invoice_detail", pk=invoice.pk)

    # Generate PDF
    html_string = render_to_string("shipments/invoice_pdf.html", {"invoice": invoice})
    if HTML is None:
        messages.error(request, "WeasyPrint not installed. Cannot generate PDF.")
        return redirect("invoice_detail", pk=invoice.pk)

    html = HTML(string=html_string, base_url=request.build_absolute_uri("/"))
    pdf = html.write_pdf()

    # Create email
    subject = f"Invoice #{invoice.pk} from Fila Trucking"
    body = f"Hello {customer.name},\n\nPlease find attached the invoice #{invoice.pk} for your recent shipments.\n\nThank you for your business!\n\nBest regards,\nFila Trucking"
    email = EmailMessage(
        subject,
        body,
        settings.DEFAULT_FROM_EMAIL,
        [customer.email],
    )
    email.attach(f"Invoice_{invoice.pk}.pdf", pdf, "application/pdf")
    
    try:
        email.send()
        invoice.status = "sent"
        invoice.save()
        messages.success(request, f"Invoice emailed to {customer.email}")
    except Exception as e:
        messages.error(request, f"Failed to send email: {str(e)}")

    return redirect("invoice_detail", pk=invoice.pk)


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


# ---------------------------------------------------------------------------
# Expense Views
# ---------------------------------------------------------------------------

class ExpenseListView(LoginRequiredMixin, ListView):
    model = Expense
    template_name = "shipments/expense_list.html"
    context_object_name = "expenses"
    paginate_by = 20


class ExpenseCreateView(LoginRequiredMixin, CreateView):
    model = Expense
    form_class = ExpenseForm
    template_name = "shipments/expense_form.html"
    success_url = reverse_lazy("expense_list")


class ExpenseUpdateView(LoginRequiredMixin, UpdateView):
    model = Expense
    form_class = ExpenseForm
    template_name = "shipments/expense_form.html"
    success_url = reverse_lazy("expense_list")


class ExpenseDeleteView(LoginRequiredMixin, DeleteView):
    model = Expense
    template_name = "shipments/expense_confirm_delete.html"
    success_url = reverse_lazy("expense_list")


# ============================================================================
# WhatsApp Configuration Views
# ============================================================================

class WhatsAppSettingsView(LoginRequiredMixin, DetailView):
    """Display WhatsApp configuration and group selection dashboard."""
    model = Shipment
    template_name = "settings/whatsapp_settings.html"
    context_object_name = "whatsapp"
    
    def get_object(self, queryset=None):
        """Redirect to WhatsApp configuration - singleton pattern."""
        from .models import WhatsAppConfig
        return WhatsAppConfig.get_instance()
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from .models import WhatsAppConfig, WhatsAppGroup, WhatsAppLog
        
        config = WhatsAppConfig.get_instance()
        groups = WhatsAppGroup.objects.all().order_by('-last_synced_at')
        logs = WhatsAppLog.objects.all()[:50]
        
        context.update({
            'config': config,
            'groups': groups,
            'logs': logs,
            'active_groups_count': groups.filter(is_active=True).count(),
            'total_groups': groups.count(),
        })
        
        return context
