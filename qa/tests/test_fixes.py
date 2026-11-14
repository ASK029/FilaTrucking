"""Regression tests for individual QA tasks that don't belong to a bigger area file."""
from datetime import date
from decimal import Decimal

from shipments.models import Shipment, ShipmentStatus

from .base import QABase


class ShipmentDetailPage(QABase):
    def make(self, **kw):
        return Shipment.objects.create(customer=self.customer, date=date(2026, 1, 5), booking="B1", container="C1",
                                       seal="S1", amount=Decimal("400"), status=ShipmentStatus.PENDING_REVIEW, **kw)

    def test_detail_renders_for_shipment_without_driver_and_vehicle(self):
        """[QA-007] every WhatsApp shipment has no driver/vehicle; the page used to 500 with NoReverseMatch."""
        self.login()
        r = self.client.get(f"/shipments/{self.make().pk}/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Unassigned", count=2)

    def test_detail_links_driver_and_vehicle_when_present(self):
        self.login()
        r = self.client.get(f"/shipments/{self.make(driver=self.driver, vehicle=self.vehicle).pk}/")
        self.assertContains(r, f"/drivers/{self.driver.pk}/")
        self.assertContains(r, f"/vehicles/{self.vehicle.pk}/")
        self.assertContains(r, self.vehicle.registration_number)
        self.assertNotContains(r, "Unassigned")


from unittest import mock

from django.contrib.messages import get_messages
from django.test import SimpleTestCase

from .test_deploy_config import ROOT, compose


class DashboardTaskButtons(QABase):
    def test_buttons_survive_a_dead_broker(self):
        """[QA-008] .delay() with no Redis hung/500'd inside a gunicorn worker; now: friendly error, 302."""
        self.login()
        for url, task in (("/test-report/", "FilaTrucking.views.generate_monthly_statement"),
                          ("/test-sync-schedule/", "FilaTrucking.views.sync_gomotive_data")):
            with self.subTest(url=url), mock.patch(f"{task}.apply_async", side_effect=OSError("redis down")):
                r = self.client.get(url)
                self.assertEqual(r.status_code, 302)
                msgs = [str(m) for m in get_messages(r.wsgi_request)]
                self.assertTrue(any("not reachable" in m for m in msgs), msgs)

    def test_buttons_report_success_when_the_job_is_queued(self):
        self.login()
        with mock.patch("FilaTrucking.views.generate_monthly_statement.apply_async") as q:
            r = self.client.get("/test-report/")
        q.assert_called_once()
        self.assertTrue(any("triggered" in str(m) for m in get_messages(r.wsgi_request)))


class ProductionHasASchedulerStack(SimpleTestCase):
    def test_prod_compose_runs_redis_worker_and_beat(self):
        """[QA-008] no scheduled job ever ran in production: the compose file had no broker/worker/beat."""
        services = compose("docker-compose.prod.yml")["services"]
        for name in ("redis", "celery-worker", "celery-beat"):
            self.assertIn(name, services)
        self.assertIn("celery -A FilaTrucking worker", services["celery-worker"]["command"])
        self.assertIn("celery -A FilaTrucking beat", services["celery-beat"]["command"])
        self.assertFalse(services["redis"].get("ports"), "the broker must not be published")
        for name in ("django", "celery-worker", "celery-beat"):
            envs = " ".join(services[name]["environment"])
            self.assertIn("CELERY_BROKER_URL=redis://redis:6379/0", envs, name)
        self.assertEqual({services[n]["image"] for n in ("django", "celery-worker", "celery-beat")}, {"filatrucking-django"})

    def test_recurring_invoices_are_opt_in(self):
        from django.conf import settings
        self.assertNotIn("recurring-invoices", settings.CELERY_BEAT_SCHEDULE)
        self.assertIn("gomotive-nightly-sync", settings.CELERY_BEAT_SCHEDULE)
