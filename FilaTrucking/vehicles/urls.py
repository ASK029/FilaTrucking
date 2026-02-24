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
    # IFTA
    path("ifta/log/", views.IFTALogCreateView.as_view(), name="ifta_log_create"),
    path("ifta/logs/", views.IFTALogListView.as_view(), name="ifta_log_list"),
    path("ifta/report/", views.ifta_report, name="ifta_report"),
]
