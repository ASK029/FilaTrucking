from django.urls import path

from . import views


urlpatterns = [
    # Vehicle CRUD
    path("", views.VehicleListView.as_view(), name="vehicle_list"),
    path("create/", views.VehicleCreateView.as_view(), name="vehicle_create"),
    path("<int:pk>/", views.VehicleDetailView.as_view(), name="vehicle_detail"),
    path("<int:pk>/edit/", views.VehicleUpdateView.as_view(), name="vehicle_update"),
    path(
        "<int:pk>/delete/",
        views.VehicleDeleteView.as_view(),
        name="vehicle_delete",
    ),
    path("<int:pk>/sync/", views.sync_vehicle_gomotive, name="sync_vehicle"),
    path("gomotive/", views.gomotive_vehicles_view, name="gomotive_vehicles"),
    # IFTA
    path("ifta/log/miles/", views.IFTAMilesCreateView.as_view(), name="ifta_log_miles_create"),
    path("ifta/log/gallons/", views.IFTAGallonsCreateView.as_view(), name="ifta_log_gallons_create"),
    path("ifta/logs/", views.IFTALogListView.as_view(), name="ifta_log_list"),
    path("ifta/report/", views.ifta_report, name="ifta_report"),
    # Maintenance
    path("maintenance/", views.MaintenanceListView.as_view(), name="maintenance_list"),
    path("maintenance/create/", views.MaintenanceCreateView.as_view(), name="maintenance_create"),
    path("maintenance/<int:pk>/edit/", views.MaintenanceUpdateView.as_view(), name="maintenance_update"),
    path("maintenance/<int:pk>/delete/", views.MaintenanceDeleteView.as_view(), name="maintenance_delete"),
]
