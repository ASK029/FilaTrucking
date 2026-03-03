from django.urls import path

from . import views


urlpatterns = [
    path("", views.DriverListView.as_view(), name="driver_list"),
    path("create/", views.DriverCreateView.as_view(), name="driver_create"),
    path("<int:pk>/", views.DriverDetailView.as_view(), name="driver_detail"),
    path("<int:pk>/edit/", views.DriverUpdateView.as_view(), name="driver_update"),
    path(
        "<int:pk>/delete/",
        views.DriverDeleteView.as_view(), name="driver_delete",
    ),
    path(
        "documents/<int:pk>/delete/",
        views.DriverDocumentDeleteView.as_view(),
        name="driver_document_delete",
    ),
]

