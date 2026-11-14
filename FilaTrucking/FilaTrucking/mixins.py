from django.contrib import messages
from django.db.models import ProtectedError
from django.shortcuts import redirect


class ProtectedDeleteMixin:
    """For DeleteViews of records that other records point to with on_delete=PROTECT (QA-039).

    Instead of cascading (silently erasing IFTA/service/billing history) or crashing with a 500, the delete is refused
    with an explanation and the user is sent back to the record's detail page.
    """

    protected_message = "This record has history attached to it and cannot be deleted."
    protected_redirect = None  # URL name of the detail page, called with pk=<object pk>

    def form_valid(self, form):
        try:
            return super().form_valid(form)
        except ProtectedError as error:
            kinds = sorted({str(obj._meta.verbose_name_plural) for obj in error.protected_objects})
            messages.error(self.request, f"{self.protected_message} It is still used by: {', '.join(kinds)}.")
            return redirect(self.protected_redirect, pk=self.object.pk)
