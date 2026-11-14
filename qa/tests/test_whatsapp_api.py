"""Django-side WhatsApp status/control endpoints (shipments/api.py)."""
import json
import os
import subprocess
import sys
from unittest import mock

import requests
from django.conf import settings

from shipments.api import WHATSAPP_SECRET
from shipments.models import WhatsAppConfig, WhatsAppGroup, WhatsAppLog

from .base import QABase

BASE = "/shipments/api/whatsapp"
BEARER = {"HTTP_AUTHORIZATION": f"Bearer {WHATSAPP_SECRET}"}


class StatusEndpointsAreNotPublic(QABase):
    """[WA-AUTH] These routes are @csrf_exempt with no check_auth(); anyone on the internet can call them."""

    def test_anonymous_cannot_read_qr_code(self):
        WhatsAppConfig.objects.update_or_create(pk=1, defaults={"qr_code_data": "2@PAIRING-PAYLOAD"})
        r = self.client.get(f"{BASE}/qr-code/")
        self.assertIn(r.status_code, (401, 403), "pairing QR is readable without login")

    def test_anonymous_cannot_read_status(self):
        self.assertIn(self.client.get(f"{BASE}/status/").status_code, (401, 403))

    def test_anonymous_cannot_read_logs(self):
        self.assertIn(self.client.get(f"{BASE}/logs/").status_code, (401, 403))

    def test_anonymous_cannot_list_groups(self):
        WhatsAppGroup.objects.create(group_jid="1-2@g.us", group_name="Private ops group")
        self.assertIn(self.client.get(f"{BASE}/groups/").status_code, (401, 403))

    def test_mutating_endpoints_reject_anonymous(self):
        for path in ("update-status/", "sync-groups/", "trigger-sync/", "trigger-restart/", "trigger-clear-auth/"):
            with self.subTest(path=path):
                r = self.client.post(f"{BASE}/{path}", data="{}", content_type="application/json")
                self.assertEqual(r.status_code, 401)


class StatusLifecycle(QABase):
    def _post(self, payload):
        return self.client.post(f"{BASE}/update-status/", data=json.dumps(payload),
                                content_type="application/json", **BEARER)

    def test_qr_is_cleared_once_connected(self):
        """[WA-STALE-QR] sidecar sends qr_code=null when connected; Django keeps the old QR forever."""
        self._post({"sidecar_status": "connecting", "auth_status": "authenticating", "qr_code": "2@OLD-QR"})
        self._post({"sidecar_status": "connected", "auth_status": "authenticated", "qr_code": None})
        self.assertFalse(WhatsAppConfig.get_instance().qr_code_data)

    def test_last_error_is_cleared_once_connected(self):
        """[WA-STALE-ERR] the settings page keeps showing the last error after a successful reconnect."""
        self._post({"sidecar_status": "disconnected", "auth_status": "not_authenticated", "last_error": "boom"})
        self._post({"sidecar_status": "connected", "auth_status": "authenticated"})
        self.assertFalse(WhatsAppConfig.get_instance().last_error)

    def test_repeated_identical_pings_write_one_log_row(self):
        """[WA-LOG-GROWTH] used to document 'one row per ping' (unbounded growth); fixed in QA-045."""
        for _ in range(5):
            self._post({"sidecar_status": "disconnected", "auth_status": "not_authenticated", "last_error": "405"})
        self.assertEqual(WhatsAppLog.objects.count(), 1)


class TriggerEndpoints(QABase):
    def setUp(self):
        super().setUp()
        self.login()

    def test_trigger_sync_keeps_groups_the_admin_disabled(self):
        """[WA-SYNC-RESET] 'Sync groups' silently re-enables every group (is_active=True in update_or_create defaults)."""
        g = WhatsAppGroup.objects.create(group_jid="111-222@g.us", group_name="Chatter", is_active=False)
        resp = mock.Mock(status_code=200)
        resp.json.return_value = {"groups": [{"jid": "111-222@g.us", "name": "Chatter", "participants": 3}]}
        with mock.patch("requests.get", return_value=resp):
            self.client.post(f"{BASE}/trigger-sync/")
        g.refresh_from_db()
        self.assertFalse(g.is_active)

    def test_every_django_to_sidecar_call_sends_the_shared_secret(self):
        """[QA-002] the sidecar now rejects unauthenticated calls; Django must send the bearer on all three."""
        ok = mock.Mock(status_code=200, text="")
        ok.json.return_value = {"groups": []}
        with mock.patch("requests.get", return_value=ok) as g, mock.patch("requests.post", return_value=ok) as p,                 mock.patch("requests.delete", return_value=ok) as d:
            self.client.post(f"{BASE}/trigger-sync/")
            self.client.post(f"{BASE}/trigger-restart/")
            self.client.post(f"{BASE}/trigger-clear-auth/")
        for name, m in (("sync", g), ("restart", p), ("clear-auth", d)):
            with self.subTest(call=name):
                self.assertEqual(m.call_args.kwargs["headers"], {"Authorization": f"Bearer {WHATSAPP_SECRET}"})

    def test_sidecar_down_returns_a_json_error(self):
        for path in ("trigger-sync/", "trigger-restart/", "trigger-clear-auth/"):
            with self.subTest(path=path), mock.patch("requests.get", side_effect=requests.ConnectionError), \
                    mock.patch("requests.post", side_effect=requests.ConnectionError), \
                    mock.patch("requests.delete", side_effect=requests.ConnectionError):
                r = self.client.post(f"{BASE}/{path}")
                self.assertEqual(r["Content-Type"], "application/json")
                self.assertIn(r.status_code, (500, 502, 503, 504))

    def test_sidecar_timeout_is_reported(self):
        with mock.patch("requests.get", side_effect=requests.Timeout):
            r = self.client.post(f"{BASE}/trigger-sync/")
        self.assertIn("timeout", r.json()["error"].lower())

    def test_logs_endpoint_rejects_non_numeric_limit(self):
        """[WA-LIMIT] ?limit=abc -> uncaught ValueError -> HTTP 500."""
        self.assertEqual(self.client.get(f"{BASE}/logs/?limit=abc").status_code, 400)


class SecretHandling(QABase):
    def test_ingest_secret_is_configurable_through_environment(self):
        """[WA-SECRET] docker-compose sets WHATSAPP_INGEST_SECRET, but settings.py never reads it, so api.py always
        falls back to the constant committed in the repo."""
        code = ("import os,django;os.environ['DJANGO_SETTINGS_MODULE']='FilaTrucking.settings';django.setup();"
                "from shipments import api;print(api.WHATSAPP_SECRET)")
        env = dict(os.environ, WHATSAPP_INGEST_SECRET="some-other-value", DATABASE_URL="sqlite:///:memory:",
                   CELERY_BROKER_URL="memory://")
        out = subprocess.run([sys.executable, "-c", code], env=env, cwd=settings.BASE_DIR,
                             capture_output=True, text=True).stdout.strip().splitlines()[-1]
        self.assertTrue(out == "some-other-value", "env var WHATSAPP_INGEST_SECRET is ignored; hard-coded default used")

    def _settings_import(self, **env):
        code = ("import os,django;os.environ['DJANGO_SETTINGS_MODULE']='FilaTrucking.settings';django.setup();"
                "from django.conf import settings;print('OK', len(settings.WHATSAPP_INGEST_SECRET))")
        base = {k: v for k, v in os.environ.items() if k != "WHATSAPP_INGEST_SECRET"}
        base.update(DATABASE_URL="sqlite:///:memory:", CELERY_BROKER_URL="memory://", SECRET_KEY="x" * 60)
        base.update(env)
        return subprocess.run([sys.executable, "-c", code], env=base, cwd=settings.BASE_DIR,
                              capture_output=True, text=True)

    def test_production_refuses_to_start_without_the_secret(self):
        """[QA-004] no default value: DEBUG=False and no WHATSAPP_INGEST_SECRET must fail fast."""
        r = self._settings_import(DEBUG="False", ALLOWED_HOSTS="example.org")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("WHATSAPP_INGEST_SECRET", r.stderr)

    def test_the_secret_that_was_published_in_git_is_rejected(self):
        """[QA-004] the old constant is public; configuring it again must fail."""
        r = self._settings_import(DEBUG="False", ALLOWED_HOSTS="example.org", WHATSAPP_INGEST_SECRET="fila_secret_2026")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("published", r.stderr)

    def test_short_secret_is_rejected(self):
        r = self._settings_import(DEBUG="False", ALLOWED_HOSTS="example.org", WHATSAPP_INGEST_SECRET="short")
        self.assertNotEqual(r.returncode, 0)

    def test_old_public_secret_no_longer_authenticates(self):
        """[QA-004] the value from the repository must get 401 on every bearer-protected call."""
        old = {"HTTP_AUTHORIZATION": "Bearer fila_secret_2026"}
        r = self.client.post("/shipments/api/ingest/", data=json.dumps({"text": "A\nB\nC"}),
                             content_type="application/json", **old)
        self.assertEqual(r.status_code, 401)
        r = self.client.post(f"{BASE}/update-status/", data=json.dumps({"sidecar_status": "connected"}),
                             content_type="application/json", **old)
        self.assertEqual(r.status_code, 401)


class AccessControlQA003(QABase):
    """[QA-003] the status/QR/logs/groups routes accept a login or the bearer secret, nothing else."""

    def test_logged_in_admin_can_read_all_four(self):
        self.login()
        WhatsAppGroup.objects.create(group_jid="1-2@g.us", group_name="Ops")
        for path in ("status/", "qr-code/", "logs/", "groups/"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(f"{BASE}/{path}").status_code, 200)

    def test_sidecar_bearer_can_read_the_group_list(self):
        """The sidecar polls GET /groups/ with the bearer to learn which groups are enabled."""
        WhatsAppGroup.objects.create(group_jid="1-2@g.us", group_name="Ops", is_active=True)
        r = self.client.get(f"{BASE}/groups/", **BEARER)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["groups"][0]["is_active"])

    def test_wrong_bearer_is_rejected_even_when_logged_in(self):
        self.login()
        r = self.client.get(f"{BASE}/groups/", HTTP_AUTHORIZATION="Bearer wrong")
        self.assertEqual(r.status_code, 401)

    def test_session_writes_need_a_csrf_token(self):
        """Control endpoints are csrf_exempt (the sidecar has no cookies), so a cross-site form post from the admin's
        browser would have restarted the bot / wiped the session. The session path must enforce CSRF itself."""
        from django.test import Client
        c = Client(enforce_csrf_checks=True, raise_request_exception=False)
        c.force_login(self.user)
        sidecar_ok = mock.Mock(status_code=200, text="")
        with mock.patch("requests.post", return_value=sidecar_ok) as post:
            r = c.post(f"{BASE}/trigger-restart/")
            self.assertEqual(r.status_code, 401, "cross-site POST must be refused")
            post.assert_not_called()
            c.get("/auth/login/")  # sets the csrftoken cookie
            token = c.cookies["csrftoken"].value
            r = c.post(f"{BASE}/trigger-restart/", HTTP_X_CSRFTOKEN=token)
            self.assertEqual(r.status_code, 200)
            post.assert_called_once()

    def test_bearer_calls_do_not_need_csrf(self):
        from django.test import Client
        c = Client(enforce_csrf_checks=True)
        r = c.post(f"{BASE}/update-status/", data=json.dumps({"sidecar_status": "connected"}),
                   content_type="application/json", **BEARER)
        self.assertEqual(r.status_code, 200)


class StatusAndGroupsQA019(QABase):
    def _post(self, payload):
        return self.client.post(f"{BASE}/update-status/", data=json.dumps(payload),
                                content_type="application/json", **BEARER)

    def test_qr_is_kept_while_pairing_and_replaced_by_the_newest(self):
        self._post({"sidecar_status": "connecting", "auth_status": "authenticating", "qr_code": "2@QR-1"})
        self._post({"sidecar_status": "connecting", "auth_status": "authenticating"})  # no qr key: unchanged
        self.assertEqual(WhatsAppConfig.get_instance().qr_code_data, "2@QR-1")
        self._post({"sidecar_status": "connecting", "auth_status": "authenticating", "qr_code": "2@QR-2"})
        self.assertEqual(WhatsAppConfig.get_instance().qr_code_data, "2@QR-2")
        self._post({"sidecar_status": "disconnected", "auth_status": "not_authenticated", "qr_code": None})
        self.assertEqual(WhatsAppConfig.get_instance().qr_code_data, "")

    def test_error_is_shown_until_a_successful_connection(self):
        self._post({"sidecar_status": "disconnected", "auth_status": "not_authenticated", "last_error": "405"})
        self._post({"sidecar_status": "connecting", "auth_status": "authenticating"})
        c = WhatsAppConfig.get_instance()
        self.assertEqual(c.last_error, "405")  # still the open incident
        self.assertIsNotNone(c.last_error_time)
        self._post({"sidecar_status": "connected", "auth_status": "authenticated"})
        c = WhatsAppConfig.get_instance()
        self.assertEqual((c.last_error, c.qr_code_data), ("", ""))
        self.assertIsNotNone(c.last_connection_time)

    def test_clear_auth_keeps_the_admins_group_selection(self):
        self.login()
        WhatsAppGroup.objects.create(group_jid="1-2@g.us", group_name="Dispatch", is_active=True)
        ok = mock.Mock(status_code=200, text="")
        with mock.patch("requests.delete", return_value=ok):
            r = self.client.post(f"{BASE}/trigger-clear-auth/")
        self.assertEqual(r.json()["status"], "success")
        self.assertEqual(WhatsAppGroup.objects.get().is_active, True)

    def test_sidecar_group_sync_adds_new_groups_and_updates_names_without_touching_is_active(self):
        g = WhatsAppGroup.objects.create(group_jid="1-2@g.us", group_name="Old", is_active=False)
        r = self.client.post(f"{BASE}/sync-groups/", data=json.dumps({"groups": [
            {"jid": "1-2@g.us", "name": "Renamed", "participants": 9}, {"jid": "3-4@g.us", "name": "New", "participants": 2}]}),
            content_type="application/json", **BEARER)
        self.assertEqual(r.json()["groups_synced"], 2)
        g.refresh_from_db()
        self.assertEqual((g.group_name, g.participant_count, g.is_active), ("Renamed", 9, False))


class NewGroupsAreOptInQA020(QABase):
    def test_a_newly_discovered_group_is_not_listened_to_until_the_admin_enables_it(self):
        r = self.client.post(f"{BASE}/sync-groups/", data=json.dumps({"groups": [{"jid": "9-9@g.us", "name": "Family", "participants": 7}]}),
                             content_type="application/json", **BEARER)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(WhatsAppGroup.objects.get().is_active)
        listed = self.client.get(f"{BASE}/groups/", **BEARER).json()["groups"]
        self.assertEqual([g["is_active"] for g in listed], [False])

    def test_trigger_sync_creates_new_groups_disabled(self):
        self.login()
        resp = mock.Mock(status_code=200)
        resp.json.return_value = {"groups": [{"jid": "7-7@g.us", "name": "Neighbours", "participants": 4}]}
        with mock.patch("requests.get", return_value=resp):
            self.client.post(f"{BASE}/trigger-sync/")
        self.assertFalse(WhatsAppGroup.objects.get().is_active)


class LogGrowthQA045(QABase):
    def _post(self, payload):
        return self.client.post(f"{BASE}/update-status/", data=json.dumps(payload), content_type="application/json", **BEARER)

    def test_identical_pings_do_not_add_rows(self):
        for _ in range(50):
            self._post({"sidecar_status": "disconnected", "auth_status": "not_authenticated", "last_error": "405 Connection Failure"})
        self.assertEqual(WhatsAppLog.objects.count(), 1)  # one error row, not fifty
        for _ in range(20):
            self._post({"sidecar_status": "connecting", "auth_status": "authenticating"})
        self.assertEqual(WhatsAppLog.objects.count(), 2)  # + one 'status changed' row

    def test_a_new_error_text_and_each_state_change_are_logged(self):
        self._post({"sidecar_status": "disconnected", "auth_status": "not_authenticated", "last_error": "A"})
        self._post({"sidecar_status": "disconnected", "auth_status": "not_authenticated", "last_error": "B"})
        self._post({"sidecar_status": "connected", "auth_status": "authenticated"})
        self.assertEqual(list(WhatsAppLog.objects.order_by("id").values_list("level", flat=True)), ["error", "error", "info"])

    def test_prune_task_deletes_only_old_rows(self):
        from datetime import timedelta
        from django.utils import timezone
        from shipments import tasks
        from shipments.models import WhatsAppMessage
        old = WhatsAppLog.objects.create(level="info", message="old")
        fresh = WhatsAppLog.objects.create(level="info", message="fresh")
        WhatsAppLog.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=31))
        stale_msg = WhatsAppMessage.objects.create(raw_text="BK\nC\nS", sender_phone="x")
        recent_msg = WhatsAppMessage.objects.create(raw_text="BK\nC\nS", sender_phone="y")
        WhatsAppMessage.objects.filter(pk=stale_msg.pk).update(received_at=timezone.now() - timedelta(days=400))
        result = tasks.prune_whatsapp_data()
        self.assertEqual(result, {"logs_deleted": 1, "messages_deleted": 1})
        self.assertEqual(list(WhatsAppLog.objects.values_list("pk", flat=True)), [fresh.pk])
        self.assertEqual(list(WhatsAppMessage.objects.values_list("pk", flat=True)), [recent_msg.pk])

    def test_prune_is_scheduled_nightly(self):
        from django.conf import settings
        self.assertEqual(settings.CELERY_BEAT_SCHEDULE["prune-whatsapp-data"]["task"], "shipments.tasks.prune_whatsapp_data")
