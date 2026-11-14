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


class DriverAndRateQA017(QABase):
    def test_jid_normalisation(self):
        from shipments.api import jid_to_e164 as f
        self.assertEqual(f("13125550101@s.whatsapp.net"), "+13125550101")
        self.assertEqual(f("13125550101:12@s.whatsapp.net"), "+13125550101")  # multi-device suffix
        self.assertEqual(f("+1 (312) 555-0101"), "+13125550101")
        self.assertIsNone(f("98765432101234@lid"))  # privacy id: NOT a phone number
        self.assertIsNone(f("120363@g.us"))
        self.assertIsNone(f(""))
        self.assertIsNone(f(None))
        self.assertIsNone(f("123@s.whatsapp.net"))  # too short to be a phone

    def test_phone_must_match_exactly_not_as_a_substring(self):
        from drivers.models import Driver
        Driver.objects.create(name="Other Guy", phone_number="+13125550199", license_number="LIC-9")
        # '+1312555019' is a prefix of Other Guy's number: the old icontains lookup would have matched him
        self.ingest(POSITIONAL, sender="1312555019@s.whatsapp.net")
        self.assertIsNone(Shipment.objects.get().driver)

    def test_lid_sender_is_left_unassigned(self):
        self.ingest(POSITIONAL, sender="98765432101234@lid")
        self.assertIsNone(Shipment.objects.get().driver)

    def test_driver_named_in_message_wins_and_ambiguous_partial_names_are_not_guessed(self):
        from drivers.models import Driver
        Driver.objects.create(name="Moe Second", phone_number="+13125550102", license_number="LIC-2")
        self.ingest(KV.replace("DRIVER: Moe", "DRIVER: moe driver"), sender="999@lid")
        self.assertEqual(Shipment.objects.get().driver, self.driver)  # exact (case-insensitive) match
        Shipment.objects.all().delete()
        self.ingest(KV.replace("DRIVER: Moe", "DRIVER: Moe").replace("RICG02390500", "RICG0000009"), sender="999@lid")
        self.assertIsNone(Shipment.objects.get().driver)  # "Moe" matches two drivers: do not pick one

    def test_explicit_rate_beats_default_and_garbage_rate_falls_back(self):
        self.ingest(KV.replace("RATE: 400", "RATE: 550"))
        self.assertEqual(Shipment.objects.get().amount, Decimal("550.00"))
        Shipment.objects.all().delete()
        self.ingest(KV.replace("RATE: 400", "RATE: 1.2.3").replace("RICG02390500", "RICG0000010"))
        self.assertEqual(Shipment.objects.get().amount, Decimal("400.00"))  # default rate

    def test_no_rate_anywhere_leaves_amount_empty_and_flags_it(self):
        from customers.models import Customer
        Customer.objects.filter(pk=self.customer.pk).update(default_rate=None)
        j = self.ingest(POSITIONAL).json()
        s = Shipment.objects.get()
        self.assertEqual(j["status"], "success")
        self.assertIsNone(s.amount)
        self.assertTrue(s.is_flagged)
        self.assertIn("set the amount", s.notes)
        self.assertIn("not set", j["reply_message"])
        self.assertNotIn("None", j["reply_message"])

    def test_shipment_pages_render_without_a_rate(self):
        self.login()
        from customers.models import Customer
        Customer.objects.filter(pk=self.customer.pk).update(default_rate=None)
        self.ingest(POSITIONAL)
        s = Shipment.objects.get()
        for url in ("/shipments/", f"/shipments/{s.pk}/"):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200)
            self.assertNotContains(r, "$None")


class IngestRobustnessQA018(QABase):
    def test_same_message_id_never_creates_two_shipments_even_if_text_differs(self):
        a = self.ingest(POSITIONAL, message_id="3EB0AAA").json()
        b = self.ingest(POSITIONAL.replace("RICG02390500", "RICG77777777"), message_id="3EB0AAA").json()
        self.assertEqual((a["status"], b["status"]), ("success", "duplicate"))
        self.assertEqual(b["shipment_id"], a["shipment_id"])
        self.assertNotIn("reply_message", b)  # the group was answered the first time
        self.assertEqual(Shipment.objects.count(), 1)

    def test_two_different_ids_are_two_shipments(self):
        self.ingest(POSITIONAL, message_id="A1")
        self.ingest(POSITIONAL.replace("RICG02390500", "RICG77777777"), message_id="A2")
        self.assertEqual(Shipment.objects.count(), 2)

    def test_without_id_or_timestamp_nothing_is_deduplicated(self):
        self.ingest(POSITIONAL)
        self.ingest(POSITIONAL)
        self.assertEqual(Shipment.objects.count(), 2)  # (the second is flagged as a duplicate container)

    def test_dedupe_hash_depends_on_the_sender(self):
        self.ingest(POSITIONAL, timestamp="2026-01-05T15:00:00.000Z", sender="13125550101@s.whatsapp.net")
        self.ingest(POSITIONAL, timestamp="2026-01-05T15:00:00.000Z", sender="13125550102@s.whatsapp.net")
        self.assertEqual(Shipment.objects.count(), 2)

    def test_payload_type_errors_are_400(self):
        for body in ({"text": ["a"]}, {"text": "A\nB\nC", "sender": 5}, {"text": "A\nB\nC", "message_id": 7},
                     {"text": "A\nB\nC", "timestamp": 12345}, {"text": "x" * 5000}, {"text": "   "}):
            with self.subTest(body=str(body)[:40]):
                self.assertEqual(self.ingest(raw=json.dumps(body)).status_code, 400)

    def test_overlong_values_are_flagged_with_a_clear_message_not_stored(self):
        j = self.ingest("B" * 80 + "\nCONT1\nSEAL1\nASL").json()
        self.assertEqual(j["status"], "flagged")
        self.assertIn("Booking is too long", j["reply_message"])
        self.assertEqual(Shipment.objects.count(), 0)

    def test_unexpected_errors_do_not_leak_internals_to_the_group(self):
        from unittest import mock
        with mock.patch("shipments.api.Shipment.objects.create", side_effect=RuntimeError("SELECT secret FROM t /srv/app.py")):
            j = self.ingest(POSITIONAL).json()
        self.assertEqual(j["status"], "flagged")
        self.assertNotIn("SELECT", json.dumps(j))
        self.assertNotIn("/srv", json.dumps(j))
        self.assertNotIn("SELECT", WhatsAppMessage.objects.get().error_message)

    def test_naive_and_offset_timestamps_are_accepted(self):
        for ts in ("2026-01-05T15:00:00", "2026-01-05T15:00:00+00:00", "2026-01-05T09:00:00-06:00", "garbage"):
            with self.subTest(ts=ts):
                Shipment.objects.all().delete()
                WhatsAppMessage.objects.all().delete()
                self.assertEqual(self.ingest(POSITIONAL.rsplit("\n", 1)[0], timestamp=ts).json()["status"], "success")


class ShortValuesQA048(QABase):
    def test_a_one_character_seal_is_accepted(self):
        j = self.ingest("BK123\nCNT456\n7\nASL").json()
        self.assertEqual(j["status"], "success")
        self.assertEqual(Shipment.objects.get().seal, "7")

    def test_punctuation_and_emoji_lines_are_not_values(self):
        j = self.ingest("BK123\n.\n👍\nCNT456\nSEAL9\nASL").json()
        s = Shipment.objects.get()
        self.assertEqual((j["status"], s.booking, s.container, s.seal), ("success", "BK123", "CNT456", "SEAL9"))

    def test_chatter_made_of_single_characters_is_still_ignored(self):
        for text in ("k", "👍", ".", "k\nok", "?\n!"):
            with self.subTest(text=text):
                self.assertEqual(self.ingest(text).json()["status"], "ignored")
