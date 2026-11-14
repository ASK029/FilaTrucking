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


import os
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings


def release(response):
    """Close a FileResponse's file without response.close(): that fires request_finished -> close_old_connections,
    which kills the test's own DB connection on MySQL (and an open file blocks temp-dir cleanup on Windows)."""
    for closer in getattr(response, "_resource_closers", []):
        closer()


class UploadsQA023(QABase):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self._override = override_settings(MEDIA_ROOT=self._tmp.name)
        self._override.enable()
        self.addCleanup(self._override.disable)

    def make_doc(self, name="license_john_smith.pdf", content=b"%PDF-1.4 fake"):
        from drivers.models import DriverDocument
        doc = DriverDocument(driver=self.driver, name="Licence", document=SimpleUploadedFile(name, content))
        doc.full_clean()
        doc.save()
        return doc

    def test_stored_name_is_random_and_keeps_only_the_extension(self):
        doc = self.make_doc()
        self.assertRegex(doc.document.name, r"^driver_documents/[0-9a-f]{32}\.pdf$")
        self.assertNotIn("john", doc.document.name)
        self.assertNotEqual(self.make_doc().document.name, doc.document.name)

    def test_anonymous_cannot_download_uploads_but_logged_in_users_can(self):
        doc = self.make_doc()
        url = f"/media/{doc.document.name}"
        r = self.client.get(url)
        self.assertEqual(r.status_code, 302)
        self.assertIn("/auth/login/", r["Location"])
        self.login()
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(b"".join(r.streaming_content), b"%PDF-1.4 fake")
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertEqual(r["X-Content-Type-Options"], "nosniff")
        release(r)

    def test_path_traversal_and_missing_files_are_404(self):
        self.login()
        secret = os.path.join(os.path.dirname(self._tmp.name), "outside.txt")
        with open(secret, "w") as fh:
            fh.write("x")
        self.addCleanup(os.remove, secret)
        for bad in ("../outside.txt", "..%2foutside.txt", "driver_documents/nope.pdf", "driver_documents/"):
            with self.subTest(path=bad):
                self.assertEqual(self.client.get(f"/media/{bad}").status_code, 404)

    def test_unknown_types_are_downloaded_never_rendered_inline(self):
        from drivers.models import DriverDocument
        self.login()
        doc = DriverDocument.objects.create(driver=self.driver, name="x", document=SimpleUploadedFile("a.docx", b"PK"))
        r = self.client.get(f"/media/{doc.document.name}")
        self.assertIn("attachment", r["Content-Disposition"])
        release(r)

    def test_dangerous_extensions_and_oversize_files_are_rejected(self):
        from django.core.exceptions import ValidationError
        from drivers.models import DriverDocument
        for name in ("evil.html", "evil.svg", "evil.exe", "evil.php", "noextension"):
            with self.subTest(name=name):
                doc = DriverDocument(driver=self.driver, name="x", document=SimpleUploadedFile(name, b"<script>"))
                with self.assertRaises(ValidationError):
                    doc.full_clean()
        big = SimpleUploadedFile("big.pdf", b"0" * (10 * 1024 * 1024 + 1))
        with self.assertRaises(ValidationError):
            DriverDocument(driver=self.driver, name="x", document=big).full_clean()

    def test_expense_receipt_and_vehicle_image_use_random_names_too(self):
        from shipments.models import Expense
        e = Expense.objects.create(date=date(2026, 1, 1), amount=Decimal("5"), receipt=SimpleUploadedFile("Receipt Pilot.PDF", b"%PDF"))
        self.assertRegex(e.receipt.name, r"^expense_receipts/[0-9a-f]{32}\.pdf$")

    def test_nginx_no_longer_serves_media_from_disk(self):
        conf = (ROOT / "nginx.conf").read_text(encoding="utf-8")
        self.assertNotIn("location /media/", conf)
        self.assertNotIn("alias /media/", conf)
        services = compose("docker-compose.prod.yml")["services"]
        self.assertFalse([v for v in services["nginx"]["volumes"] if "/media" in v])


from django.core.cache import cache


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])  # fast: dozens of logins
class LoginThrottleQA030(QABase):
    def setUp(self):
        super().setUp()
        cache.clear()

    def attempt(self, password, username="qa_admin", **meta):
        return self.client.post("/auth/login/", {"username": username, "password": password}, **meta)

    def test_five_wrong_passwords_lock_the_pair_even_for_the_right_password(self):
        for i in range(5):
            self.assertEqual(self.attempt(f"wrong{i}").status_code, 200)
        r = self.attempt("qa-pass-123")  # the CORRECT password, but the account is locked from this address
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r["Retry-After"], "900")
        self.assertContains(r, "Too many failed sign-in attempts", status_code=429)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_another_address_can_still_sign_in_so_an_attacker_cannot_lock_the_admin_out(self):
        for i in range(6):
            self.attempt(f"wrong{i}", HTTP_X_FORWARDED_FOR="203.0.113.9")
        r = self.attempt("qa-pass-123", HTTP_X_FORWARDED_FOR="198.51.100.7")
        self.assertEqual(r.status_code, 302)

    def test_spoofed_forwarded_for_prefix_does_not_dodge_the_lock(self):
        for i in range(5):  # nginx appends the real peer (right-most); the client-supplied prefix changes every time
            self.attempt(f"wrong{i}", HTTP_X_FORWARDED_FOR=f"10.0.0.{i}, 203.0.113.9")
        self.assertEqual(self.attempt("x", HTTP_X_FORWARDED_FOR="10.9.9.9, 203.0.113.9").status_code, 429)

    def test_successful_login_resets_the_counter(self):
        for i in range(4):
            self.attempt(f"wrong{i}")
        self.assertEqual(self.attempt("qa-pass-123").status_code, 302)
        self.client.logout()
        for i in range(4):
            self.assertEqual(self.attempt(f"again{i}").status_code, 200)

    def test_one_address_is_limited_across_usernames(self):
        for i in range(20):
            self.attempt("nope", username=f"user{i}")
        self.assertEqual(self.attempt("qa-pass-123").status_code, 429)

    def test_nginx_also_rate_limits_the_login_form(self):
        conf = (ROOT / "nginx.conf").read_text(encoding="utf-8")
        self.assertIn("limit_req_zone", conf)
        self.assertRegex(conf, r"location = /auth/login/ \{\s*limit_req zone=login")
        self.assertIn("CACHE_URL=redis://redis:6379/1", " ".join(compose("docker-compose.prod.yml")["services"]["django"]["environment"]))
