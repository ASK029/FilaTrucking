"""WhatsApp -> Django ingestion endpoint (shipments/api.py:ingest_whatsapp_message)."""
import json
import unittest
from decimal import Decimal

from django.db import connection

from shipments.models import Shipment, ShipmentStatus, WhatsAppMessage

from .base import QABase

KV = ("DATE: 01/05/26\nBOOKING: RICG02390500\nCONTAINER: NYKU5132858\nSEAL: 101039\n"
      "CUSTOMER: ASL\nRATE: 400\nDRIVER: Moe\nTRUCK: T-101")
POSITIONAL = "RICG02390500\nNYKU5132858\n101039\nASL\n01/05/2026"


class IngestHappyPath(QABase):
    def test_key_value_message_creates_pending_shipment(self):
        r = self.ingest(KV)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "success")
        s = Shipment.objects.get()
        self.assertEqual((s.status, str(s.date), s.amount), (ShipmentStatus.PENDING_REVIEW, "2026-01-05", Decimal("400.00")))
        self.assertEqual((s.driver, s.vehicle, s.customer), (self.driver, self.vehicle, self.customer))

    def test_positional_message_creates_shipment(self):
        r = self.ingest(POSITIONAL)
        self.assertEqual(r.json()["status"], "success")
        self.assertEqual(Shipment.objects.count(), 1)

    def test_duplicate_container_same_month_is_flagged(self):
        self.ingest(KV)
        r = self.ingest(KV.replace("RICG02390500", "RICG0000002"))
        self.assertTrue(r.json()["flagged"])

    def test_rate_formats(self):
        self.ingest(KV.replace("RATE: 400", "RATE: $1,500.50"))
        self.assertEqual(Shipment.objects.get().amount, Decimal("1500.50"))


class IngestAuthAndRobustness(QABase):
    def test_missing_or_wrong_token_is_rejected(self):
        self.assertEqual(self.ingest(KV, token=None).status_code, 401)
        self.assertEqual(self.ingest(KV, token="wrong").status_code, 401)

    def test_invalid_json_is_400(self):
        self.assertEqual(self.ingest(raw="{not json").status_code, 400)

    def test_json_array_body_is_a_client_error_not_500(self):
        self.assertEqual(self.ingest(raw="[]").status_code, 400)

    def test_non_string_text_is_a_client_error_not_500(self):
        self.assertEqual(self.ingest(raw=json.dumps({"text": 12345, "sender": "x"})).status_code, 400)

    def test_empty_text_is_400(self):
        self.assertEqual(self.ingest("").status_code, 400)

    def test_get_not_allowed(self):
        self.assertEqual(self.client.get("/shipments/api/ingest/").status_code, 405)

    @unittest.skipUnless(connection.vendor == "mysql", "length limits are only enforced by MySQL strict mode")
    def test_overlong_field_is_flagged_not_500(self):
        r = self.ingest("B" * 80 + "\nCONT1\nSEAL1\nASL")
        self.assertIn(r.status_code, (200, 400))
        self.assertNotEqual(r.json().get("status"), "success")

    def test_arabic_text_does_not_crash(self):
        r = self.ingest("RICG0239\nNYKU5132858\n101039\nASL\nملاحظة: عاجل")
        self.assertEqual(r.status_code, 200)
        r = self.ingest("RICG0240\nNYKU5132859\n101040\nشركة")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "flagged")


class IngestBusinessRules(QABase):
    def test_chat_noise_is_ignored_silently(self):
        """[WA-NOISE] Drivers chat in the group. Chit-chat must not become an error row nor a bot reply."""
        r = self.ingest("ok thanks boss")
        j = r.json()
        self.assertEqual(j.get("status"), "ignored", f"got {j}")
        self.assertFalse(WhatsAppMessage.objects.filter(is_flagged=True).exists(), "chit-chat was stored as a flagged error")

    def test_chat_noise_response_cannot_trigger_a_sidecar_reply(self):
        """sidecar/index.js replies when status=='flagged' or reply_message is present."""
        j = self.ingest("hello everyone").json()
        self.assertNotEqual(j.get("status"), "flagged")
        self.assertNotIn("reply_message", j)

    def test_missing_rate_falls_back_to_customer_default_rate(self):
        """[WA-RATE] customer.default_rate=400; PRD: 'each customer has a default rate'."""
        self.ingest(POSITIONAL)
        self.assertEqual(Shipment.objects.get().amount, Decimal("400.00"))

    def test_driver_is_resolved_from_whatsapp_jid(self):
        """[WA-DRIVER] sender arrives as '<digits>@s.whatsapp.net'; Driver.phone_number is E.164."""
        self.ingest(POSITIONAL, sender="13125550101@s.whatsapp.net")
        self.assertEqual(Shipment.objects.get().driver, self.driver)

    def test_message_without_date_uses_business_local_date(self):
        """[WA-TZ] 02:30 UTC on Jan 6 is 8:30 pm Jan 5 in Illinois (PRD: Illinois fleet); TIME_ZONE is UTC."""
        self.ingest("RICG1234\nNYKU1234\n112233\nASL", timestamp="2026-01-06T02:30:00.000Z")
        self.assertEqual(str(Shipment.objects.get().date), "2026-01-05")

    def test_same_whatsapp_message_delivered_twice_creates_one_shipment(self):
        """[WA-DEDUPE] Baileys can re-emit messages after reconnect; the payload has no message id to dedupe on."""
        self.ingest(POSITIONAL, timestamp="2026-01-05T15:00:00.000Z")
        self.ingest(POSITIONAL, timestamp="2026-01-05T15:00:00.000Z")
        self.assertEqual(Shipment.objects.count(), 1)


class NoiseClassifierQA016(QABase):
    def test_what_counts_as_a_shipment_attempt(self):
        from shipments.api import looks_like_shipment_message as f
        for text in ("ok thanks boss", "hello everyone", "👍", "On my way, 20 min", "Truck: 5 broke down", "customer: ASL",
                     "yes\nthanks", "Call me\nplease", "https://example.org/x"):
            with self.subTest(text=text):
                self.assertFalse(f(text))
        for text in (POSITIONAL, KV, "BK123\nCNT456", "booking: B1\nseal: 9", "  BK1  \n\n  CONT1  "):
            with self.subTest(text=text):
                self.assertTrue(f(text))

    def test_ignored_messages_are_not_stored_at_all(self):
        self.ingest("ok thanks boss")
        self.ingest("Where are you?")
        self.assertEqual(WhatsAppMessage.objects.count(), 0)

    def test_a_real_but_incomplete_attempt_is_flagged_and_stored(self):
        j = self.ingest("BK123\nCNT456").json()
        self.assertEqual(j["status"], "flagged")
        self.assertIn("BOOKING CONTAINER SEAL", j["reply_message"])  # the driver is told what is missing
        self.assertEqual(WhatsAppMessage.objects.filter(is_flagged=True).count(), 1)
