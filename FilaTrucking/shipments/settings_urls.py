from django.urls import path
from . import views

urlpatterns = [
    path("system/", views.SystemSettingsView.as_view(), name="system_settings"),
    path("test/motive/", views.TestMotiveConnectionView.as_view(), name="test_motive_connection"),
    path("test/email/", views.TestEmailConnectionView.as_view(), name="test_email_connection"),
    path("import/", views.CSVImportView.as_view(), name="csv_import"),
    path("import/process/", views.CSVImportProcessView.as_view(), name="csv_import_process"),
]
