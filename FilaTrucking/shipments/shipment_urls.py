from django.urls import path

from . import views
from . import api

urlpatterns = [
    path("api/ingest/", api.ingest_whatsapp_message, name="api_whatsapp_ingest"),
    path("", views.ShipmentListView.as_view(), name="shipment_list"),
    path("create/", views.ShipmentCreateView.as_view(), name="shipment_create"),
    path("<int:pk>/", views.ShipmentDetailView.as_view(), name="shipment_detail"),
    path("<int:pk>/edit/", views.ShipmentUpdateView.as_view(), name="shipment_update"),
    path(
        "<int:pk>/delete/",
        views.ShipmentDeleteView.as_view(),
        name="shipment_delete",
    ),
]

