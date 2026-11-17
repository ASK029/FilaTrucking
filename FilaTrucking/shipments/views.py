import json
import logging
import uuid
from datetime import date

from django.conf import settings
from django.core.exceptions import BadRequest
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.mail import EmailMessage
from django.db import transaction
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy
from django.utils.crypto import constant_time_compare
from django.utils import timezone
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    UpdateView,
    TemplateView,
)
from django.views import View

from FilaTrucking.params import date_param

logger = logging.getLogger(__name__)

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
    SystemSettingsForm,
)
from .models import Expense, Invoice, InvoiceLineItem, InvoiceStatus, Shipment, ShipmentStatus, SystemSettings, WhatsAppConfig, WhatsAppGroup, WhatsAppLog


class ShipmentListView(LoginRequiredMixin, ListView):
    model = Shipment
    template_name = "shipments/shipment_list.html"
    context_object_name = "shipments"
    paginate_by = 25  # unbounded before (QA-034)

    def get_queryset(self):
        # the list shows customer, driver and vehicle of every row: fetch them in the same query (N+1 before)
        return super().get_queryset().select_related("customer", "driver", "vehicle")


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

    def get_queryset(self):
        return super().get_queryset().select_related("customer")


class InvoiceDetailView(LoginRequiredMixin, DetailView):
    model = Invoice
    template_name = "shipments/invoice_detail.html"
    context_object_name = "invoice"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["company"] = SystemSettings.get_instance()
        return context


def invoice_create(request):
    """Create invoice for a customer with line items in a single step."""
    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.get_full_path())

    if request.method == "POST":
        # Validate the invoice AND its line items before anything is written, and write them in one
        # transaction: the old code saved the invoice first, so a bad line left an empty orphan invoice and the
        # page came back with the user's input thrown away (QA-011).
        form = InvoiceForm(request.POST)
        formset = InvoiceLineItemFormSet(request.POST, instance=form.instance)
        form_ok = form.is_valid()
        formset_ok = formset.is_valid()  # evaluate both so every error is shown at once
        if form_ok and formset_ok:
            with transaction.atomic():
                invoice = form.save()
                formset.instance = invoice
                formset.save()
                invoice.calculate_total()

                for item in invoice.line_items.select_related("shipment"):
                    if item.shipment and item.shipment.status == ShipmentStatus.CONFIRMED:
                        item.shipment.status = ShipmentStatus.INVOICED
                        item.shipment.save(update_fields=["status"])

            messages.success(request, f"Invoice created for {invoice.customer.name}")
            return redirect("invoice_detail", pk=invoice.pk)
        return render(
            request,
            "shipments/invoice_form.html",
            {
                "form": form,
                "formset": formset,
                "start_date": request.POST.get("start_date", ""),
                "end_date": request.POST.get("end_date", ""),
            },
        )

    form = InvoiceForm()

    customer_id = request.GET.get("customer")
    start_date = date_param(request, "start_date")
    end_date = date_param(request, "end_date")
    
    customer_obj = None
    if customer_id:
        from .models import Customer
        if not customer_id.isdigit():
            raise BadRequest("'customer' must be a customer id")
        customer_obj = get_object_or_404(Customer, pk=customer_id)
        form.fields["customer"].initial = customer_obj
    
    if start_date:
        form.fields["start_date"].initial = start_date
    if end_date:
        form.fields["end_date"].initial = end_date
    
    formset = InvoiceLineItemFormSet()
    
    if customer_obj and start_date and end_date:
        shipments = Shipment.objects.filter(
            customer=customer_obj,
            status=ShipmentStatus.CONFIRMED,
            date__gte=start_date,
            date__lte=end_date,
        ).order_by("date")
        
        if shipments.exists():
            initial_data = [s.invoice_line_defaults() for s in shipments.select_related("customer")]

            from django.forms import inlineformset_factory
            DynamicFormSet = inlineformset_factory(
                Invoice,
                InvoiceLineItem,
                form=InvoiceLineItemForm,
                extra=len(initial_data) + 1,
                can_delete=True,
            )
            formset = DynamicFormSet(initial=initial_data)

    return render(
        request,
        "shipments/invoice_form.html",
        {
            "form": form,
            "formset": formset,
            "start_date": start_date,
            "end_date": end_date,
        },
    )


def invoice_add_lines(request, invoice_pk):
    """Add or edit line items for an existing invoice (formset)."""
    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.get_full_path())

    invoice = get_object_or_404(Invoice, pk=invoice_pk)
    
    start_date = date_param(request, "start_date")
    end_date = date_param(request, "end_date")
    
    if request.method == "POST":
        formset = InvoiceLineItemFormSet(request.POST, instance=invoice)
        if formset.is_valid():
            with transaction.atomic():
                before = set(invoice.line_items.exclude(shipment__isnull=True).values_list("shipment_id", flat=True))
                formset.save()
                invoice.calculate_total()
                # shipments on the invoice are 'invoiced'...
                for item in invoice.line_items.select_related("shipment"):
                    if item.shipment and item.shipment.status != ShipmentStatus.INVOICED:
                        item.shipment.status = ShipmentStatus.INVOICED
                        item.shipment.save(update_fields=["status"])
                # ...and a shipment whose line was removed goes back to 'confirmed' (it used to stay 'invoiced' forever,
                # unreachable for any future invoice) (QA-038)
                after = set(invoice.line_items.exclude(shipment__isnull=True).values_list("shipment_id", flat=True))
                Shipment.objects.filter(pk__in=before - after, status=ShipmentStatus.INVOICED).update(status=ShipmentStatus.CONFIRMED)
            return redirect("invoice_detail", pk=invoice.pk)
    else:
        formset = InvoiceLineItemFormSet(instance=invoice)
        if start_date or end_date:
            q = Shipment.objects.filter(
                customer=invoice.customer,
                status=ShipmentStatus.CONFIRMED,
            )
            if start_date:
                q = q.filter(date__gte=start_date)
            if end_date:
                q = q.filter(date__lte=end_date)

            shipments = q.select_related("customer").order_by("date")
            existing_shipment_ids = set(
                invoice.line_items.exclude(shipment__isnull=True).values_list("shipment_id", flat=True)
            )
            initial_data = []
            for shipment in shipments:
                if shipment.id in existing_shipment_ids:
                    continue
                initial_data.append(shipment.invoice_line_defaults())

            if initial_data:
                from django.forms import inlineformset_factory

                DynamicFormSet = inlineformset_factory(
                    Invoice,
                    InvoiceLineItem,
                    form=InvoiceLineItemForm,
                    extra=len(initial_data),
                    can_delete=True,
                )
                formset = DynamicFormSet(instance=invoice, initial=initial_data)

    shipment_data = {
        s.id: {
            "date_incurred": s.date.strftime("%Y-%m-%d"),
            "description": (s.notes or "").strip()[:255],
            "booking_no": s.booking or "",
            "container_no": s.container or "",
            "seal_no": s.seal or "",
            "location": s.effective_location,
            "amount": str(s.amount or ""),
        }
        for s in Shipment.objects.filter(customer=invoice.customer).select_related("customer").order_by("-date")
    }

    return render(
        request,
        "shipments/invoice_add_lines.html",
        {
            "invoice": invoice,
            "formset": formset,
            "start_date": start_date,
            "end_date": end_date,
            "shipment_data": shipment_data,
        },
    )


def invoice_change_status(request, pk):
    """Update invoice status via dedicated action."""
    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login

        return redirect_to_login(request.get_full_path())
    if request.method != "POST":
        return HttpResponseRedirect(reverse("invoice_detail", kwargs={"pk": pk}))

    invoice = get_object_or_404(Invoice, pk=pk)
    new_status = request.POST.get("status", "").strip()
    valid_statuses = {choice[0] for choice in InvoiceStatus.choices}
    if new_status not in valid_statuses:
        messages.error(request, "Invalid invoice status selected.")
        return redirect("invoice_detail", pk=invoice.pk)

    invoice.status = new_status
    if new_status == InvoiceStatus.PAID:
        if not invoice.paid_at:
            invoice.paid_at = timezone.localdate()
    else:
        invoice.paid_at = None
    invoice.save(update_fields=["status", "paid_at"])
    messages.success(request, "Invoice status updated.")
    return redirect("invoice_detail", pk=invoice.pk)


def invoice_delete(request, pk):
    """Delete a draft invoice and restore linked shipments to confirmed."""
    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.get_full_path())

    if request.method != "POST":
        return HttpResponseRedirect(reverse("invoice_list"))

    invoice = get_object_or_404(Invoice, pk=pk)
    
    if invoice.status != InvoiceStatus.DRAFT:
        messages.error(request, "Only draft invoices can be deleted.")
        return redirect("invoice_detail", pk=invoice.pk)

    for item in invoice.line_items.all():
        if item.shipment:
            item.shipment.status = ShipmentStatus.CONFIRMED
            item.shipment.save()

    customer_name = invoice.customer.name
    invoice.delete()
    messages.success(request, f"Invoice deleted. Shipments restored for {customer_name}.")
    return redirect("invoice_list")


def invoice_pdf(request, pk):
    """Generate PDF for an invoice using WeasyPrint."""
    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.get_full_path())

    invoice = get_object_or_404(Invoice, pk=pk)
    html_string = render_to_string("shipments/invoice_pdf.html", {"invoice": invoice, "company": SystemSettings.get_instance()})
    
    if HTML is None:
        return HttpResponse("WeasyPrint not installed. Cannot generate PDF.", status=500)
    
    html = HTML(string=html_string, base_url=request.build_absolute_uri("/"))
    pdf = html.write_pdf()
    
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="Invoice_{invoice.pk}.pdf"'
    return response


def invoice_email(request, pk):
    """Email the invoice PDF to the customer. POST only (it sends mail and may change the status)."""
    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.get_full_path())
    if request.method != "POST":
        return redirect("invoice_detail", pk=pk)

    invoice = get_object_or_404(Invoice, pk=pk)
    customer = invoice.customer

    if not customer.email:
        messages.error(request, f"Customer {customer.name} has no email address.")
        return redirect("invoice_detail", pk=invoice.pk)

    # Generate PDF
    html_string = render_to_string("shipments/invoice_pdf.html", {"invoice": invoice, "company": SystemSettings.get_instance()})
    if HTML is None:
        messages.error(request, "WeasyPrint not installed. Cannot generate PDF.")
        return redirect("invoice_detail", pk=invoice.pk)

    html = HTML(string=html_string, base_url=request.build_absolute_uri("/"))
    pdf = html.write_pdf()

    # Create email
    # Try to use dynamic settings if configured
    settings_obj = SystemSettings.get_instance()
    company_name = settings_obj.company_name  # editable in System Settings (QA-052)
    subject = f"Invoice #{invoice.pk} from {company_name}"
    body = f"Hello {customer.name},\n\nPlease find attached the invoice #{invoice.pk} for your recent shipments.\n\nThank you for your business!\n\nBest regards,\n{company_name}"
    from_email = settings_obj.email_from_email or settings.DEFAULT_FROM_EMAIL
    
    email_kwargs = {
        'subject': subject,
        'body': body,
        'from_email': from_email,
        'to': [customer.email],
    }

    if settings_obj.email_host_user and settings_obj.get_email_password():
        from django.core.mail import get_connection
        try:
            connection = get_connection(
                host=getattr(settings, 'EMAIL_HOST', 'smtp.gmail.com'),
                port=getattr(settings, 'EMAIL_PORT', 587),
                username=settings_obj.email_host_user,
                password=settings_obj.get_email_password(),
                use_tls=getattr(settings, 'EMAIL_USE_TLS', True),
            )
            email_kwargs['connection'] = connection
        except Exception as e:
            messages.warning(request, f"Using default email server as custom configuration failed: {str(e)}")

    email = EmailMessage(**email_kwargs)
    email.attach(f"Invoice_{invoice.pk}.pdf", pdf, "application/pdf")
    
    try:
        email.send()
        # Only a draft becomes 'sent'. Re-sending a sent/paid invoice must not change its status or wipe paid_at:
        # statements count PAID invoices by paid_at, so the old unconditional reset removed revenue (QA-013).
        if invoice.status == InvoiceStatus.DRAFT:
            invoice.status = InvoiceStatus.SENT
            invoice.save(update_fields=["status"])
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

    def get_queryset(self):
        return super().get_queryset().select_related("vehicle", "driver")


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
        return WhatsAppConfig.get_instance()
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
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

# ============================================================================
# System Settings Views
# ============================================================================

from django.http import JsonResponse
from django.views import View
from .forms import SystemSettingsForm

class SystemSettingsView(LoginRequiredMixin, UpdateView):
    model = SystemSettings
    form_class = SystemSettingsForm
    template_name = "shipments/settings_form.html"
    success_url = reverse_lazy("system_settings")

    def get_object(self, queryset=None):
        return SystemSettings.get_instance()

    def form_valid(self, form):
        messages.success(self.request, "System settings updated successfully.")
        return super().form_valid(form)


class TestMotiveConnectionView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        from vehicles.gomotive_client import GoMotiveClient
        settings_obj = SystemSettings.get_instance()
        api_key = request.POST.get('motive_api_key') or settings_obj.get_motive_api_key()
        
        if not api_key:
            return JsonResponse({'success': False, 'message': 'API Key is missing.'})
            
        client = GoMotiveClient(api_key=api_key)
        try:
            if client.test_connection():
                return JsonResponse({'success': True, 'message': 'Successfully connected to Motive API.'})
            else:
                return JsonResponse({'success': False, 'message': 'Failed to connect to Motive. Check your API key or Base URL.'})
        except Exception as e:
            return JsonResponse({'success': False, 'message': f'Error: {str(e)}'})


class TestEmailConnectionView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        from django.core.mail import get_connection, EmailMessage
        
        settings_obj = SystemSettings.get_instance()
        host_user = request.POST.get('email_host_user') or settings_obj.email_host_user
        password = request.POST.get('email_password') or settings_obj.get_email_password()
        from_email = request.POST.get('email_from_email') or settings_obj.email_from_email
        
        if not all([host_user, password, from_email]):
            return JsonResponse({'success': False, 'message': 'Email configuration is incomplete.'})
            
        try:
            # Use dynamic connection settings
            connection = get_connection(
                host=getattr(settings, 'EMAIL_HOST', 'smtp.gmail.com'),
                port=getattr(settings, 'EMAIL_PORT', 587),
                username=host_user,
                password=password,
                use_tls=getattr(settings, 'EMAIL_USE_TLS', True),
                fail_silently=False,
            )
            
            subject = "FilaTrucking - Test Email"
            body = "This is a test email from your FilaTrucking system settings."
            
            email = EmailMessage(
                subject, body, from_email, [request.user.email or from_email],
                connection=connection
            )
            email.send()
            return JsonResponse({'success': True, 'message': f'Test email sent to {request.user.email or from_email}'})
        except Exception as e:
            return JsonResponse({'success': False, 'message': f'Failed to send email: {str(e)}'})


class CSVImportView(LoginRequiredMixin, TemplateView):
    template_name = "settings/csv_import.html"


IMPORT_SESSION_KEY = "statement_import"


class CSVImportProcessView(LoginRequiredMixin, View):
    """Step 1 of the statement import (QA-050): read the file and show what *would* happen. Nothing is saved."""

    def post(self, request, *args, **kwargs):
        from . import statement_import as si

        upload = request.FILES.get("csv_file")
        if not upload:
            messages.error(request, "Please select a CSV or Excel (.xlsx) file.")
            return redirect("csv_import")
        if upload.size > si.MAX_UPLOAD_BYTES:
            messages.error(request, f"The file is too large. The limit is {si.MAX_UPLOAD_BYTES // 1024 // 1024} MB.")
            return redirect("csv_import")

        try:
            parsed = si.parse_sheets(si.read_sheets(upload.read(), upload.name))
            if not parsed.rows:
                reasons = "; ".join(f"{sh.name}: {sh.reason}" for sh in parsed.sheets if sh.status == "skipped")
                messages.error(request, "No importable rows found. " + (reasons or "The file has a DATE column but no dated rows with amounts."))
                return redirect("csv_import")
            plan = si.build_plan(parsed.rows)
        except si.StatementImportError as exc:
            messages.error(request, str(exc))
            return redirect("csv_import")
        except Exception:
            logger.exception("Statement import: could not process %r", upload.name)
            messages.error(request, "The file could not be processed. Check that it is a statement export and try again.")
            return redirect("csv_import")

        token = uuid.uuid4().hex
        request.session[IMPORT_SESSION_KEY] = {"token": token, "filename": upload.name, "rows": parsed.rows}
        return render(request, "settings/csv_import_preview.html", {
            "plan": plan, "token": token, "filename": upload.name,
            "sheets": parsed.sheets, "warnings": parsed.warnings + plan.warnings,
            "months": list(plan.months.values()), "totals": plan.totals,
        })


class CSVImportConfirmView(LoginRequiredMixin, View):
    """Step 2: the user confirmed the preview. The plan is rebuilt from the stored rows against the database as it is
    *now* (so duplicates are judged at commit time) and applied in one transaction."""

    def post(self, request, *args, **kwargs):
        from . import statement_import as si

        pending = request.session.get(IMPORT_SESSION_KEY)
        if not pending or not constant_time_compare(str(pending.get("token", "")), request.POST.get("token", "")):
            messages.error(request, "That import has expired or was already completed. Please upload the file again.")
            return redirect("csv_import")

        plan = si.build_plan(pending["rows"])
        if plan.total_new == 0:
            request.session.pop(IMPORT_SESSION_KEY, None)
            messages.warning(request, f"Nothing new to import: all {plan.duplicates} entries from {pending['filename']} are already in the system.")
            return redirect("csv_import")
        try:
            result = si.apply_plan(plan)
        except Exception:
            logger.exception("Statement import: failed to save %r", pending.get("filename"))
            messages.error(request, "The import failed and nothing was saved. Please try again; if it keeps failing, check the server log.")
            return redirect("csv_import")

        request.session.pop(IMPORT_SESSION_KEY, None)
        parts = []
        if result.invoices:
            parts.append(f"{result.invoices} deposit{'' if result.invoices == 1 else 's'} (paid invoices)")
        if result.expenses:
            parts.append(f"{result.expenses} expense{'' if result.expenses == 1 else 's'} (driver pay included)")
        text = f"Imported {' and '.join(parts)} for {plan.period_label}."
        if result.duplicates:
            text += f" {result.duplicates} already-imported entries were skipped."
        if result.customers or result.drivers:
            made = [f"{n} {word}{'' if n == 1 else 's'}" for n, word in ((result.customers, "customer"), (result.drivers, "driver")) if n]
            text += f" Created {' and '.join(made)} with placeholder contact details: please complete them."
        messages.success(request, text)
        return redirect("csv_import")
