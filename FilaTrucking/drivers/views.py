from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.urls import reverse_lazy
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    UpdateView,
)

from .models import Driver, DriverDocument
from .form import DriverDocumentFormSet, DriverForm


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
    form_class = DriverForm
    template_name = "drivers/driver_form.html"
    success_url = reverse_lazy("driver_list")

    def get_context_data(self, **kwargs):
        """Inject the formset into the context."""
        context = super().get_context_data(**kwargs)
        
        # If it's a POST request, populate the formset with the submitted data and files
        if self.request.POST:
            context['document_formset'] = DriverDocumentFormSet(
                self.request.POST, self.request.FILES
            )
        # If it's a GET request, create an empty formset
        else:
            context['document_formset'] = DriverDocumentFormSet()
            
        return context

    def form_valid(self, form):
        """Save both the parent form and the child formset."""
        context = self.get_context_data()
        document_formset = context['document_formset']
        
        # Check if the formset is valid (CreateView already checked if 'form' is valid)
        if document_formset.is_valid():
            with transaction.atomic():
                # Save the driver
                self.object = form.save()
                
                # Link the newly created driver to the formset
                document_formset.instance = self.object
                
                # Save the documents
                document_formset.save()
                
            # Proceed to success_url
            return super().form_valid(form)
        else:
            # If the formset is invalid, re-render the page with the errors
            return self.render_to_response(self.get_context_data(form=form))


class DriverUpdateView(LoginRequiredMixin, UpdateView):
    model = Driver
    form_class = DriverForm
    # You can reuse the exact same template from the CreateView!
    template_name = "drivers/driver_form.html" 
    success_url = reverse_lazy("driver_list")

    def get_context_data(self, **kwargs):
        """Inject the formset into the context, bound to the existing instance."""
        context = super().get_context_data(**kwargs)
        
        if self.request.POST:
            # Pass the instance so Django knows we are updating, not creating entirely new ones
            context['document_formset'] = DriverDocumentFormSet(
                self.request.POST, self.request.FILES, instance=self.object
            )
        else:
            # On GET, load the formset with the documents already attached to this driver
            context['document_formset'] = DriverDocumentFormSet(instance=self.object)
            
        return context

    def form_valid(self, form):
        """Save both the parent form and the child formset."""
        context = self.get_context_data()
        document_formset = context['document_formset']
        
        if document_formset.is_valid():
            with transaction.atomic():
                self.object = form.save()
                
                # Make sure the formset is linked to the current driver instance
                document_formset.instance = self.object
                document_formset.save()
                
            return super().form_valid(form)
        else:
            return self.render_to_response(self.get_context_data(form=form))


class DriverDeleteView(LoginRequiredMixin, DeleteView):
    model = Driver
    template_name = "drivers/driver_confirm_delete.html"
    success_url = reverse_lazy("driver_list")


class DriverDocumentDeleteView(LoginRequiredMixin, DeleteView):
    model = DriverDocument
    template_name = "drivers/driver_document_confirm_delete.html"

    def get_success_url(self):
        return reverse_lazy("driver_detail", kwargs={"pk": self.object.driver_id})
