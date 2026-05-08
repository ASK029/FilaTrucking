from django.urls import path

from . import views

urlpatterns = [
    path("", views.InvoiceListView.as_view(), name="invoice_list"),
    path("create/", views.invoice_create, name="invoice_create"),
    path("<int:invoice_pk>/lines/", views.invoice_add_lines, name="invoice_add_lines"),
    path("<int:pk>/", views.InvoiceDetailView.as_view(), name="invoice_detail"),
    path("<int:pk>/change-status/", views.invoice_change_status, name="invoice_change_status"),
    path("<int:pk>/delete/", views.invoice_delete, name="invoice_delete"),
    path("<int:pk>/pdf/", views.invoice_pdf, name="invoice_pdf"),
    path("<int:pk>/email/", views.invoice_email, name="invoice_email"),
    path(
        "<int:invoice_pk>/line-item/add/",
        views.InvoiceLineItemCreateView.as_view(),
        name="invoice_line_item_add",
    ),
]
