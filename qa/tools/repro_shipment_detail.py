import sys; sys.path.insert(0, r"D:ProjectsFilaTruckingFilaTrucking")
import os, sys, traceback, django
os.environ["DJANGO_SETTINGS_MODULE"] = "FilaTrucking.settings"
django.setup()
from django.test.utils import setup_test_environment, get_runner
from django.conf import settings
from django.test.runner import DiscoverRunner
r = DiscoverRunner(verbosity=0, interactive=False); setup_test_environment(); old = r.setup_databases()
from datetime import date
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import Client
from customers.models import Customer
from shipments.models import Shipment
u = get_user_model().objects.create_user("a", "a@x.io", "p")
c = Customer.objects.create(name="A", abbreviation="A", phone_number="+13125550100", address="x", email="a@x.io")
for label, kw in (("no driver/vehicle/amount", dict(amount=None)), ("with amount", dict(amount=Decimal("5")))):
    s = Shipment.objects.create(customer=c, date=date(2026,1,5), booking="B", container="C", seal="S", location="L", **kw)
    cl = Client(raise_request_exception=True); cl.force_login(u)
    try:
        resp = cl.get(f"/shipments/{s.pk}/"); print(label, "->", resp.status_code)
    except Exception as e:
        print(label, "-> EXC", type(e).__name__, str(e)[:300]); tb = traceback.format_exc().splitlines(); print("\n".join(tb[-6:]))
