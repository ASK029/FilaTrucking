from django.urls import path

from . import views

urlpatterns = [
    path("ifta/log/", views.IFTALogCreateView.as_view(), name="ifta_log_create"),
    path("ifta/logs/", views.IFTALogListView.as_view(), name="ifta_log_list"),
    path("ifta/report/", views.ifta_report, name="ifta_report"),
]
