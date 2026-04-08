"""
URL configuration for FilaTrucking project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import include, path
from django.conf import settings
from django.conf.urls.static import static

from . import views as project_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("auth/", include("django.contrib.auth.urls")),
    path("", project_views.dashboard, name="dashboard"),
    path("test-report/", project_views.test_report, name="test_report"),
    path("test-sync-schedule/", project_views.test_sync_schedule, name="test_sync_schedule"),
    path("customers/", include("customers.urls")),
    path("drivers/", include("drivers.urls")),
    path("shipments/", include("shipments.shipment_urls")),
    path("vehicles/", include("vehicles.urls")),
    path("invoices/", include("shipments.urls")),
    path("expenses/", include("shipments.expense_urls")),
    path("settings/", include("shipments.settings_urls")),
    path(
        "reports/financial/monthly/",
        project_views.monthly_statement,
        name="monthly_statement",
    ),
    path(
        "reports/financial/yearly/",
        project_views.yearly_statement,
        name="yearly_statement",
    ),
    path(
        "reports/financial/monthly/pdf/",
        project_views.monthly_statement_pdf,
        name="monthly_statement_pdf",
    ),
    path(
        "reports/financial/yearly/pdf/",
        project_views.yearly_statement_pdf,
        name="yearly_statement_pdf",
    ),
]


# Serve media files during development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)

