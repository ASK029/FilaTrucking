from django.urls import path
from . import views

urlpatterns = [
    path("", views.ExpenseListView.as_view(), name="expense_list"),
    path("create/", views.ExpenseCreateView.as_view(), name="expense_create"),
    path("<int:pk>/edit/", views.ExpenseUpdateView.as_view(), name="expense_update"),
    path("<int:pk>/delete/", views.ExpenseDeleteView.as_view(), name="expense_delete"),
]
