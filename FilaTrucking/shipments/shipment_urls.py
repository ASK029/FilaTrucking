from django.urls import path

from . import views
from . import api

urlpatterns = [
    # Shipment management views
    path("", views.ShipmentListView.as_view(), name="shipment_list"),
    path("create/", views.ShipmentCreateView.as_view(), name="shipment_create"),
    path("<int:pk>/", views.ShipmentDetailView.as_view(), name="shipment_detail"),
    path("<int:pk>/edit/", views.ShipmentUpdateView.as_view(), name="shipment_update"),
    path(
        "<int:pk>/delete/",
        views.ShipmentDeleteView.as_view(),
        name="shipment_delete",
    ),
    
    # WhatsApp API endpoints
    path("api/ingest/", api.ingest_whatsapp_message, name="api_whatsapp_ingest"),
    path("api/whatsapp/status/", api.whatsapp_status, name="api_whatsapp_status"),
    path("api/whatsapp/qr-code/", api.whatsapp_qr_code, name="api_whatsapp_qr_code"),
    path("api/whatsapp/logs/", api.whatsapp_logs, name="api_whatsapp_logs"),
    path("api/whatsapp/groups/", api.whatsapp_groups, name="api_whatsapp_groups"),
    path("api/whatsapp/groups/<int:group_id>/status/", api.whatsapp_update_group_status, name="api_whatsapp_update_group_status"),
    path("api/whatsapp/sync-groups/", api.whatsapp_sync_groups, name="api_whatsapp_sync_groups"),
    path("api/whatsapp/update-status/", api.whatsapp_update_status, name="api_whatsapp_update_status"),
    path("api/whatsapp/trigger-sync/", api.trigger_sync_groups, name="api_trigger_sync_groups"),
    path("api/whatsapp/trigger-restart/", api.trigger_restart_connection, name="api_trigger_restart"),
    path("api/whatsapp/trigger-clear-auth/", api.trigger_clear_auth, name="api_trigger_clear_auth"),
    
    # WhatsApp Settings Page
    path("settings/whatsapp/", views.WhatsAppSettingsView.as_view(), name="whatsapp_settings"),
]

