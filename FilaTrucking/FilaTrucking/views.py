from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render

# @staff_member_required
@login_required
def dashboard(request):
    return render(request, 'dashboard.html')
