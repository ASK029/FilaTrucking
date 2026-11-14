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

    def test_status_pings_write_one_log_row_each(self):
        """[WA-LOG-GROWTH] documents behaviour (passes): unbounded table, ~1,200 rows/hour during a 3 s reconnect loop."""
        for _ in range(5):
            self._post({"sidecar_status": "disconnected", "auth_status": "not_authenticated", "last_error": "405"})
        self.assertEqual(WhatsAppLog.objects.count(), 5)


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
