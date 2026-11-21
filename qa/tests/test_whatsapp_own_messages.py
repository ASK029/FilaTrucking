"""QA-054: the website option "accept shipments typed from the connected WhatsApp account".

The sidecar side (drop / accept the account's own messages, never re-process the bot's replies) is covered by
qa/tests/sidecar.test.cjs; this file covers Django: the stored option, the API the sidecar and the page use, the page.
"""
import json

from django.test import Client

from shipments.api import WHATSAPP_SECRET
from shipments.models import WhatsAppConfig, WhatsAppGroup, WhatsAppLog

from .base import QABase

BASE = "/shipments/api/whatsapp"
BEARER = {"HTTP_AUTHORIZATION": f"Bearer {WHATSAPP_SECRET}"}


class OwnMessagesOption(QABase):
    def _post(self, payload, client=None, **extra):
        body = payload if isinstance(payload, str) else json.dumps(payload)
        return (client or self.client).post(f"{BASE}/options/", data=body, content_type="application/json", **extra)

    def test_off_by_default(self):
        self.assertFalse(WhatsAppConfig.get_instance().accept_own_messages)

    def test_group_list_carries_the_option_for_the_sidecar(self):
        WhatsAppGroup.objects.create(group_jid="1-2@g.us", group_name="Ops", is_active=True)
        self.assertIs(self.client.get(f"{BASE}/groups/", **BEARER).json()["accept_own_messages"], False)
        config = WhatsAppConfig.get_instance()
        config.accept_own_messages = True
        config.save()
        self.assertIs(self.client.get(f"{BASE}/groups/", **BEARER).json()["accept_own_messages"], True)

    def test_logged_in_admin_can_switch_it_on_and_off(self):
        self.login()
        r = self._post({"accept_own_messages": True})
        self.assertEqual(r.status_code, 200)
        self.assertIs(r.json()["accept_own_messages"], True)
        self.assertTrue(WhatsAppConfig.get_instance().accept_own_messages)
        self.assertEqual(self._post({"accept_own_messages": False}).json()["accept_own_messages"], False)
        self.assertFalse(WhatsAppConfig.get_instance().accept_own_messages)

    def test_changes_are_logged_but_a_repeat_is_not(self):
        self.login()
        self._post({"accept_own_messages": True})
        self._post({"accept_own_messages": True})
        self._post({"accept_own_messages": False})
        messages = list(WhatsAppLog.objects.filter(message__contains="connected account").values_list("message", flat=True))
        self.assertEqual(len(messages), 2)
        self.assertTrue(any("accepted" in m for m in messages) and any("ignored" in m for m in messages))

    def test_anonymous_and_wrong_bearer_are_refused(self):
        self.assertEqual(self._post({"accept_own_messages": True}).status_code, 401)
        self.assertEqual(self._post({"accept_own_messages": True}, HTTP_AUTHORIZATION="Bearer wrong").status_code, 401)
        self.login()
        self.assertEqual(self._post({"accept_own_messages": True}, HTTP_AUTHORIZATION="Bearer wrong").status_code, 401)
        self.assertFalse(WhatsAppConfig.get_instance().accept_own_messages)

    def test_a_cross_site_post_from_the_admins_browser_is_refused(self):
        c = Client(enforce_csrf_checks=True, raise_request_exception=False)
        c.force_login(self.user)
        self.assertEqual(self._post({"accept_own_messages": True}, client=c).status_code, 401)
        self.assertFalse(WhatsAppConfig.get_instance().accept_own_messages)
        c.get("/auth/login/")
        token = c.cookies["csrftoken"].value
        self.assertEqual(self._post({"accept_own_messages": True}, client=c, HTTP_X_CSRFTOKEN=token).status_code, 200)
        self.assertTrue(WhatsAppConfig.get_instance().accept_own_messages)

    def test_only_real_booleans_are_accepted(self):
        self.login()
        for bad in ({"accept_own_messages": "yes"}, {"accept_own_messages": 1}, {"accept_own_messages": None},
                    {}, [], "not json", '"true"'):
            with self.subTest(bad=bad):
                self.assertEqual(self._post(bad).status_code, 400)
        self.assertFalse(WhatsAppConfig.get_instance().accept_own_messages)

    def test_settings_page_shows_the_checkbox_in_its_current_state(self):
        self.login()
        html = self.client.get("/shipments/settings/whatsapp/").content.decode()
        self.assertIn('id="accept-own-messages"', html)
        tag = html[html.index('id="accept-own-messages"'):]
        tag = tag[:tag.index("/>")]
        self.assertNotIn("checked", tag)
        config = WhatsAppConfig.get_instance()
        config.accept_own_messages = True
        config.save()
        html = self.client.get("/shipments/settings/whatsapp/").content.decode()
        tag = html[html.index('id="accept-own-messages"'):]
        self.assertIn("checked", tag[:tag.index("/>")])

    def test_the_ingest_endpoint_treats_the_accounts_own_message_like_any_member(self):
        """The sidecar sends the connected account as an ordinary sender: a driver with that phone gets the shipment."""
        r = self.ingest("Klaipeda\n95938662\nHAMU3898114\n179458\nNAPZ225644", sender="13125550101@s.whatsapp.net",
                        message_id="OWN-1")
        self.assertEqual(r.json()["status"], "success", r.content)
        from shipments.models import Shipment
        shipment = Shipment.objects.get(booking="95938662")
        self.assertEqual(shipment.driver, self.driver)
        self.assertEqual(shipment.chassis, "NAPZ225644")
