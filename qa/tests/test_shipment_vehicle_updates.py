"""QA-051: vehicle documents, invoice description/location defaults, shipment chassis, the new WhatsApp format."""
import importlib
import tempfile
from datetime import date
from decimal import Decimal
from unittest import mock

from django.apps import apps as django_apps
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings

from customers.models import Customer
from shipments import tasks
from shipments.forms import InvoiceLineItemFormSet
from shipments.message_parser import normalise_lines, parse_message
from shipments.models import Invoice, InvoiceLineItem, Shipment, ShipmentStatus, WhatsAppMessage
from vehicles.forms import VehicleDocumentFormSet
from vehicles.models import Vehicle, VehicleDocument

from .base import QABase, formset_data
from .test_fixes import release

INVOICE_PREFIX = InvoiceLineItemFormSet.get_default_prefix()
DOC_PREFIX = VehicleDocumentFormSet.get_default_prefix()

# the example from the request, one value per line
EXAMPLE = "Klaipeda\n95938662\nHAMU3898114\n179458\nNAPZ225644"


class MediaTmp:
    """Uploads go to a throw-away folder."""

    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        override = override_settings(MEDIA_ROOT=tmp.name)
        override.enable()
        self.addCleanup(override.disable)


# ---------------------------------------------------------------------------
# A. Vehicle documents (like drivers)
# ---------------------------------------------------------------------------

class VehicleDocuments(MediaTmp, QABase):
    def setUp(self):
        super().setUp()
        self.login()

    def vehicle_post(self, rows, initial=0, **over):
        data = {"registration_number": "T-202", "model": "579", "chassis_number": "1XKYD49X0LJ000202",
                "status": "AV", "ownership_type": "CO", "driver": ""}
        data.update(over)
        data.update(formset_data(DOC_PREFIX, rows, initial=initial))
        return data

    def pdf(self, name="reg.pdf"):
        return SimpleUploadedFile(name, b"%PDF-1.4 fake")

    def test_create_a_truck_with_two_documents(self):
        r = self.client.post("/vehicles/create/", self.vehicle_post([
            {"name": "Registration", "document": self.pdf()},
            {"name": "Insurance", "document": self.pdf("ins.pdf")},
        ]))
        self.assertEqual(r.status_code, 302)
        vehicle = Vehicle.objects.get(registration_number="T-202")
        self.assertEqual(sorted(vehicle.documents.values_list("name", flat=True)), ["Insurance", "Registration"])
        self.assertRegex(vehicle.documents.first().document.name, r"^vehicle_documents/[0-9a-f]{32}\.pdf$")

    def test_a_truck_without_documents_still_saves(self):
        r = self.client.post("/vehicles/create/", self.vehicle_post([{}]))  # the single empty extra form
        self.assertEqual(r.status_code, 302)
        self.assertEqual(VehicleDocument.objects.count(), 0)

    def test_edit_adds_one_and_removes_one(self):
        doc = VehicleDocument.objects.create(vehicle=self.vehicle, name="Old", document=self.pdf("old.pdf"))
        r = self.client.post(f"/vehicles/{self.vehicle.pk}/edit/", self.vehicle_post([
            {"id": doc.pk, "vehicle": self.vehicle.pk, "name": "Old", "DELETE": "on"},
            {"name": "Inspection", "document": self.pdf("insp.pdf")},
        ], initial=1, registration_number="T-101", chassis_number="1XKYD49X0LJ000001"))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(list(self.vehicle.documents.values_list("name", flat=True)), ["Inspection"])

    def test_a_bad_file_type_is_refused_and_the_truck_is_not_created(self):
        r = self.client.post("/vehicles/create/", self.vehicle_post([
            {"name": "Malware", "document": SimpleUploadedFile("run.exe", b"MZ")},
        ]))
        self.assertEqual(r.status_code, 200)  # form shown again with the error
        self.assertFalse(Vehicle.objects.filter(registration_number="T-202").exists())

    def test_a_file_needs_a_name(self):
        r = self.client.post("/vehicles/create/", self.vehicle_post([{"document": self.pdf()}]))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Vehicle.objects.filter(registration_number="T-202").exists())

    def test_form_page_renders_the_documents_section(self):
        r = self.client.get("/vehicles/create/")
        self.assertContains(r, "Add Document")
        self.assertContains(r, f"{DOC_PREFIX}-TOTAL_FORMS")

    def test_detail_lists_documents_and_delete_works(self):
        doc = VehicleDocument.objects.create(vehicle=self.vehicle, name="Registration", document=self.pdf())
        r = self.client.get(f"/vehicles/{self.vehicle.pk}/")
        self.assertContains(r, "Registration")
        self.assertContains(r, f"/media/{doc.document.name}")
        r = self.client.post(f"/vehicles/documents/{doc.pk}/delete/")
        self.assertRedirects(r, f"/vehicles/{self.vehicle.pk}/")
        self.assertEqual(VehicleDocument.objects.count(), 0)
        self.assertContains(self.client.get(f"/vehicles/{self.vehicle.pk}/"), "No documents attached.")

    def test_documents_need_a_login_to_view_or_delete(self):
        doc = VehicleDocument.objects.create(vehicle=self.vehicle, name="Registration", document=self.pdf())
        self.client.logout()
        for method, url in (("get", "/vehicles/create/"), ("get", f"/media/{doc.document.name}"),
                            ("post", f"/vehicles/documents/{doc.pk}/delete/")):
            with self.subTest(url=url):
                r = getattr(self.client, method)(url)
                self.assertEqual(r.status_code, 302)
                self.assertIn("/auth/login/", r["Location"])
        self.assertEqual(VehicleDocument.objects.count(), 1)
        self.login()
        r = self.client.get(f"/media/{doc.document.name}")
        try:
            self.assertEqual(r.status_code, 200)
        finally:
            release(r)

    def test_deleting_a_truck_with_history_is_still_refused_and_keeps_its_documents(self):
        VehicleDocument.objects.create(vehicle=self.vehicle, name="Registration", document=self.pdf())
        Shipment.objects.create(customer=self.customer, vehicle=self.vehicle, date=date(2026, 1, 5), booking="B", container="C", seal="S")
        self.client.post(f"/vehicles/{self.vehicle.pk}/delete/")
        self.assertTrue(Vehicle.objects.filter(pk=self.vehicle.pk).exists())
        self.assertEqual(VehicleDocument.objects.count(), 1)


# ---------------------------------------------------------------------------
# B. Shipment: chassis, location default, flag reason
# ---------------------------------------------------------------------------

class ShipmentFields(QABase):
    def setUp(self):
        super().setUp()
        self.login()

    def form(self, **over):
        data = {"date": "2026-01-05", "booking": "B1", "container": "C1", "seal": "S1", "chassis": "NAPZ225644",
                "location": "", "customer": self.customer.pk, "driver": "", "vehicle": "", "amount": "",
                "status": "confirmed", "flag_reason": "", "notes": ""}
        data.update(over)
        return data

    def test_chassis_is_saved_and_a_blank_location_becomes_the_customer_abbreviation(self):
        r = self.client.post("/shipments/create/", self.form())
        self.assertEqual(r.status_code, 302)
        s = Shipment.objects.get()
        self.assertEqual((s.chassis, s.location, s.amount), ("NAPZ225644", "ASL", Decimal("400.00")))

    def test_an_explicit_location_wins(self):
        self.client.post("/shipments/create/", self.form(location="Klaipeda"))
        self.assertEqual(Shipment.objects.get().location, "Klaipeda")

    def test_chassis_is_optional(self):
        self.assertEqual(self.client.post("/shipments/create/", self.form(chassis="")).status_code, 302)

    def test_detail_and_list_show_chassis_and_the_flag_reason(self):
        s = Shipment.objects.create(customer=self.customer, date=date(2026, 1, 5), booking="B1", container="C1", seal="S1",
                                    chassis="NAPZ225644", is_flagged=True, flag_reason="Duplicate container this billing period.")
        r = self.client.get(f"/shipments/{s.pk}/")
        self.assertContains(r, "NAPZ225644")
        self.assertContains(r, "Duplicate container this billing period.")
        self.assertContains(r, "ASL")  # location falls back to the customer's abbreviation
        self.assertContains(self.client.get("/shipments/"), "NAPZ225644")

    def test_effective_location_follows_a_customer_change(self):
        other = Customer.objects.create(name="Other Co", abbreviation="OTH", phone_number="+13125550111",
                                        address="2 Main St", email="o@example.com")
        s = Shipment.objects.create(customer=self.customer, date=date(2026, 1, 5), booking="B", container="C", seal="S")
        self.assertEqual(s.effective_location, "ASL")
        s.customer = other
        self.assertEqual(s.effective_location, "OTH")


# ---------------------------------------------------------------------------
# C. Invoice lines: description = notes (empty without), location = customer abbreviation
# ---------------------------------------------------------------------------

class InvoiceDefaults(QABase):
    def setUp(self):
        super().setUp()
        self.login()

    def shipment(self, container="C1", **over):
        fields = dict(customer=self.customer, date=date(2026, 1, 5), booking="B1", container=container, seal="S1",
                      location="", amount=Decimal("400"), status=ShipmentStatus.CONFIRMED)
        fields.update(over)
        return Shipment.objects.create(**fields)

    def test_defaults_use_the_notes_and_the_customer_abbreviation(self):
        d = self.shipment(notes="  Klaipeda ").invoice_line_defaults()
        self.assertEqual((d["description"], d["location"]), ("Klaipeda", "ASL"))
        d = self.shipment("C2").invoice_line_defaults()
        self.assertEqual(d["description"], "")  # no notes: empty, not "Shipment C2"
        self.assertEqual(self.shipment("C3", location="CHI").invoice_line_defaults()["location"], "CHI")

    def test_new_invoice_screen_prefills_empty_description_and_abbreviation(self):
        self.shipment()
        r = self.client.get(f"/invoices/create/?customer={self.customer.pk}&start_date=2026-01-01&end_date=2026-01-31")
        initial = r.context["formset"].forms[0].initial
        self.assertEqual((initial["description"], initial["location"]), ("", "ASL"))

    def test_blank_fields_on_a_posted_line_are_filled_from_the_shipment(self):
        s = self.shipment(notes="Klaipeda")
        row = {"shipment": s.pk, "date_incurred": "2026-01-05", "description": "", "booking_no": "", "container_no": "",
               "seal_no": "", "location": "", "amount": "400.00"}
        data = {"customer": self.customer.pk, "invoice_date": "2026-01-10", "status": "draft"}
        data.update(formset_data(INVOICE_PREFIX, [row]))
        self.assertEqual(self.client.post("/invoices/create/", data).status_code, 302)
        item = InvoiceLineItem.objects.get()
        self.assertEqual((item.description, item.location, item.container_no), ("Klaipeda", "ASL", "C1"))

    def test_a_line_may_have_an_empty_description(self):
        s = self.shipment()
        row = {"shipment": s.pk, "date_incurred": "2026-01-05", "description": "", "booking_no": "B1",
               "container_no": "C1", "seal_no": "S1", "location": "CHI", "amount": "400.00"}
        data = {"customer": self.customer.pk, "invoice_date": "2026-01-10", "status": "draft"}
        data.update(formset_data(INVOICE_PREFIX, [row]))
        self.assertEqual(self.client.post("/invoices/create/", data).status_code, 302)
        item = InvoiceLineItem.objects.get()
        self.assertEqual((item.description, item.location), ("", "CHI"))

    def test_add_lines_screen_data_matches(self):
        s = self.shipment()
        inv = Invoice.objects.create(customer=self.customer, invoice_date=date(2026, 1, 10))
        r = self.client.get(f"/invoices/{inv.pk}/lines/?start_date=2026-01-01&end_date=2026-01-31")
        self.assertEqual(r.context["shipment_data"][s.pk]["description"], "")
        self.assertEqual(r.context["shipment_data"][s.pk]["location"], "ASL")
        self.assertEqual(r.context["formset"].forms[0].initial["location"], "ASL")

    def test_recurring_invoice_lines_follow_the_same_rules(self):
        self.shipment("C1", notes="Klaipeda")
        self.shipment("C2")
        with mock.patch("shipments.tasks.today_local", return_value=date(2026, 2, 3)):
            self.assertEqual(tasks.generate_recurring_invoices(), 1)
        items = {i.container_no: i for i in InvoiceLineItem.objects.all()}
        self.assertEqual((items["C1"].description, items["C1"].location), ("Klaipeda", "ASL"))
        self.assertEqual(items["C2"].description, "")

    def test_pdf_and_invoice_screen_render_with_an_empty_description(self):
        s = self.shipment()
        inv = Invoice.objects.create(customer=self.customer, invoice_date=date(2026, 1, 10))
        InvoiceLineItem.objects.create(invoice=inv, **s.invoice_line_defaults())
        self.assertEqual(self.client.get(f"/invoices/{inv.pk}/").status_code, 200)


# ---------------------------------------------------------------------------
# D. WhatsApp message parser (pure)
# ---------------------------------------------------------------------------

def fields_of(text):
    p = parse_message(text)
    return p.kind, p.fields


class ParserShapes(SimpleTestCase):
    FULL = {"note": "Klaipeda", "booking": "95938662", "container": "HAMU3898114", "seal": "179458", "chassis": "NAPZ225644"}

    def test_the_example_from_the_request(self):
        self.assertEqual(fields_of(EXAMPLE), ("shipment", self.FULL))

    def test_note_and_chassis_are_optional(self):
        required = {k: v for k, v in self.FULL.items() if k in ("booking", "container", "seal")}
        cases = {
            "95938662\nHAMU3898114\n179458": required,
            "Klaipeda\n95938662\nHAMU3898114\n179458": dict(required, note="Klaipeda"),
            "95938662\nHAMU3898114\n179458\nNAPZ225644": dict(required, chassis="NAPZ225644"),
            EXAMPLE: self.FULL,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(fields_of(text), ("shipment", expected))

    def test_a_multi_word_note(self):
        kind, fields = fields_of("Port of Klaipeda\n95938662\nHAMU3898114\n179458")
        self.assertEqual((kind, fields["note"]), ("shipment", "Port of Klaipeda"))

    def test_tagged_lines_in_any_order(self):
        text = "179458 #seal\nNAPZ225644 #chassis\nKlaipeda #note\nHAMU3898114 #container\n95938662 #booking"
        self.assertEqual(fields_of(text), ("shipment", self.FULL))

    def test_the_documented_tags_are_accepted_literally_and_in_any_case(self):
        text = ("Klaipeda #note/description (just words without numbers)\n95938662 #Booking\nHAMU3898114 #CONTAINER\n"
                "179458 #seal\nNAPZ225644 #chassis")
        self.assertEqual(fields_of(text), ("shipment", self.FULL))

    def test_tags_glued_to_the_value_or_written_before_it(self):
        self.assertEqual(fields_of("95938662#booking\n#container HAMU3898114\n179458 #seal")[0], "shipment")

    def test_an_unknown_tag_is_not_a_field(self):
        kind, fields = fields_of("95938662 #booking\nHAMU3898114 #container\n179458 #seal\nfoo #bar")
        self.assertEqual(kind, "shipment")
        self.assertNotIn("bar", fields)

    def test_formatting_noise_is_ignored(self):
        text = "\r\n  *Klaipeda*  \r\n\r\n- 95938662\r\n• hamu3898114\r\n1. 179458\r\n​NAPZ225644\r\n"
        self.assertEqual(fields_of(text), ("shipment", self.FULL))

    def test_a_container_written_with_spaces_or_a_hyphen(self):
        for container in ("HAMU 389811 4", "hamu-3898114", "HAMU 3898114"):
            with self.subTest(container=container):
                self.assertEqual(fields_of(f"95938662\n{container}\n179458")[1]["container"], "HAMU3898114")

    def test_key_value_format_is_unchanged(self):
        kind, fields = fields_of("DATE: 01/05/26\nBOOKING: RICG02390500\nCONTAINER: NYKU5132858\nSEAL: 101039\n"
                                 "CUSTOMER: ASL\nRATE: 400\nDRIVER: Moe\nTRUCK: T-101")
        self.assertEqual(kind, "shipment")
        self.assertEqual((fields["customer"], fields["rate"], fields["driver"], fields["truck"], fields["date"]),
                         ("ASL", "400", "Moe", "T-101", "01/05/26"))

    def test_key_value_with_chassis_and_note_keys(self):
        kind, fields = fields_of("Booking: B1\nContainer: HAMU3898114\nSeal: 7\nChassis: napz225644\nNote: Klaipeda")
        self.assertEqual((kind, fields["chassis"], fields["note"]), ("shipment", "NAPZ225644", "Klaipeda"))

    def test_the_old_positional_format_keeps_customer_and_date(self):
        kind, fields = fields_of("RICG02390500\nNYKU5132858\n101039\nASL\n01/05/2026")
        self.assertEqual((kind, fields["customer"], fields["date"], fields.get("chassis")), ("shipment", "ASL", "01/05/2026", None))

    def test_old_positional_with_a_container_that_is_not_iso_shaped(self):
        kind, fields = fields_of("TEST001\nCNTR12345\nSEAL99999\nAMSTAR\n05/01/2026")
        self.assertEqual((kind, fields["booking"], fields["container"], fields["seal"], fields["customer"]),
                         ("shipment", "TEST001", "CNTR12345", "SEAL99999", "AMSTAR"))

    def test_chassis_and_customer_after_the_seal(self):
        kind, fields = fields_of("95938662\nHAMU3898114\n179458\nNAPZ225644\nASL")
        self.assertEqual((kind, fields["chassis"], fields["customer"]), ("shipment", "NAPZ225644", "ASL"))

    def test_a_note_with_digits_is_not_a_note(self):
        kind, fields = fields_of("Port 5\nHAMU3898114\n179458\nNAPZ225644")
        self.assertNotIn("note", fields)

    def test_a_first_line_that_is_really_a_booking_without_digits_stays_a_booking(self):
        kind, fields = fields_of("BOOKING\nCNT1\nSEAL1")
        self.assertEqual((kind, fields["booking"]), ("shipment", "BOOKING"))

    def test_a_booking_shaped_like_a_container_is_not_mistaken_for_one(self):
        kind, fields = fields_of("ABCD1234567\nHAMU3898114\n179458")
        self.assertEqual((fields["booking"], fields["container"]), ("ABCD1234567", "HAMU3898114"))

    def test_a_missing_required_field_is_named(self):
        cases = {
            "Klaipeda\n95938662\nHAMU3898114": ["seal"],
            "BK123\nCNT456": ["seal"],
            "HAMU3898114\n179458": ["booking"],
            "booking: B1\nseal: 9": ["container"],
            "95938662 #booking\n179458 #seal": ["container"],
        }
        for text, missing in cases.items():
            with self.subTest(text=text):
                p = parse_message(text)
                self.assertEqual((p.kind, p.missing), ("incomplete", missing))

    def test_long_values_are_kept_for_the_endpoint_to_refuse(self):
        kind, fields = fields_of("B" * 80 + "\nCONT1\nSEAL1\nASL")
        self.assertEqual((kind, fields["booking"]), ("shipment", "B" * 80))


class ParserChitChat(SimpleTestCase):
    def test_chatter_is_ignored(self):
        for text in (
            "ok thanks boss", "hello everyone", "👍", "On my way, 20 min", "Where is truck 5?", "Call me 3125550101",
            "Truck 12\nTruck 14\nTruck 15", "https://example.org/x", "see www.example.org 95938662", "k\nok", "?\n!",
            "yes\nthanks", "Call me\nplease", "Klaipeda", "ok\nthanks\nboss", "95938662", "3125550101\n1500\n20",
            "Truck: 5 broke down", "customer: ASL", "Note to self: buy diesel", "Running late\nsee you at 5:30",
            "Are you at the yard?\n95938662", "x\n" * 11,
        ):
            with self.subTest(text=text):
                self.assertEqual(parse_message(text).kind, "ignore")

    def test_a_pasted_paragraph_is_ignored(self):
        text = "\n".join(["The driver arrived at the port and waited for the booking 95938662 to be released"] * 3)
        self.assertEqual(parse_message(text).kind, "ignore")

    def test_overlong_lines_and_too_many_lines_are_ignored(self):
        self.assertEqual(parse_message("A1" * 100 + "\nHAMU3898114\n179458").kind, "ignore")
        self.assertEqual(parse_message("\n".join(f"BK{i}" for i in range(11))).kind, "ignore")

    def test_attempts_that_miss_something_are_not_ignored(self):
        for text in ("BK123\nCNT456", "  BK1  \n\n  CONT1  ", "booking: B1\nseal: 9", "customer: ASL\nrate: 400"):
            with self.subTest(text=text):
                self.assertNotEqual(parse_message(text).kind, "ignore")

    def test_a_question_among_the_values_makes_it_chat(self):
        self.assertEqual(parse_message("Is BK123 ready?\nCNT456").kind, "ignore")

    def test_normalise_drops_lines_without_letters_or_digits(self):
        self.assertEqual(normalise_lines(".\n👍\n  A1 \n\n-- \n"), ["A1"])


# ---------------------------------------------------------------------------
# E. The ingest endpoint with the new format
# ---------------------------------------------------------------------------

class IngestNewFormat(QABase):
    def test_the_example_creates_a_shipment_under_unassigned_and_flags_it(self):
        j = self.ingest(EXAMPLE).json()
        self.assertEqual(j["status"], "success")
        s = Shipment.objects.get()
        self.assertEqual((s.notes, s.booking, s.container, s.seal, s.chassis), ("Klaipeda", "95938662", "HAMU3898114", "179458", "NAPZ225644"))
        self.assertEqual((s.customer.name, s.customer.abbreviation, s.status), ("Unassigned", "UNASSIGNED", ShipmentStatus.PENDING_REVIEW))
        self.assertTrue(s.is_flagged)
        self.assertIn("set the customer", s.flag_reason)
        self.assertNotIn("Auto-ingested", s.notes)
        for text in ("Klaipeda", "95938662", "HAMU3898114", "179458", "NAPZ225644", "dispatch will set it"):
            self.assertIn(text, j["reply_message"])

    def test_the_unassigned_customer_is_created_once(self):
        self.ingest(EXAMPLE)
        self.ingest(EXAMPLE.replace("HAMU3898114", "HAMU3898115"))
        self.assertEqual(Customer.objects.filter(abbreviation="UNASSIGNED").count(), 1)

    def test_naming_the_customer_skips_the_placeholder_and_the_flag(self):
        j = self.ingest(EXAMPLE + "\nCUSTOMER: ASL").json()
        s = Shipment.objects.get()
        self.assertEqual((j["status"], s.customer, s.is_flagged, s.flag_reason), ("success", self.customer, False, ""))
        self.assertFalse(Customer.objects.filter(abbreviation="UNASSIGNED").exists())

    def test_a_customer_on_the_fifth_line_works_too(self):
        self.ingest("95938662\nHAMU3898114\n179458\nNAPZ225644\nASL")
        s = Shipment.objects.get()
        self.assertEqual((s.customer, s.chassis), (self.customer, "NAPZ225644"))

    def test_a_named_but_unknown_customer_creates_nothing(self):
        j = self.ingest(EXAMPLE + "\nCUSTOMER: Nobody Inc").json()
        self.assertEqual(j["status"], "flagged")
        self.assertIn("Customer not found", j["reply_message"])
        self.assertEqual(Shipment.objects.count(), 0)
        self.assertFalse(Customer.objects.filter(abbreviation="UNASSIGNED").exists())

    def test_every_optional_combination_is_a_shipment_without_a_flag_for_it(self):
        combos = {
            "95938662\nHAMU3898114\n179458\nASL": ("", ""),
            "Klaipeda\n95938662\nHAMU3898115\n179458\nASL": ("Klaipeda", ""),
            "95938662\nHAMU3898116\n179458\nNAPZ225644\nASL": ("", "NAPZ225644"),
            "Klaipeda\n95938662\nHAMU3898117\n179458\nNAPZ225644\nASL": ("Klaipeda", "NAPZ225644"),
        }
        for text, (note, chassis) in combos.items():
            with self.subTest(text=text):
                self.assertEqual(self.ingest(text).json()["status"], "success")
                s = Shipment.objects.latest("id")
                self.assertEqual((s.notes, s.chassis, s.is_flagged), (note, chassis, False))

    def test_a_missing_required_field_names_it_and_saves_nothing(self):
        j = self.ingest("Klaipeda\n95938662\nHAMU3898114").json()
        self.assertEqual(j["status"], "flagged")
        self.assertIn("Missing: seal", j["reply_message"])
        self.assertEqual(Shipment.objects.count(), 0)
        self.assertEqual(WhatsAppMessage.objects.get().error_message, "Missing required fields: seal")

    def test_chat_is_ignored_and_not_stored(self):
        for text in ("Where is truck 5?", "ok thanks boss", "Klaipeda", "95938662"):
            self.assertEqual(self.ingest(text).json()["status"], "ignored")
        self.assertEqual(WhatsAppMessage.objects.count(), 0)

    def test_an_overlong_chassis_is_refused_clearly(self):
        j = self.ingest(EXAMPLE.replace("NAPZ225644", "N" * 20 + "1" * 31)).json()
        self.assertEqual(j["status"], "flagged")
        self.assertIn("Chassis is too long", j["reply_message"])
        self.assertEqual(Shipment.objects.count(), 0)

    def test_automatic_remarks_go_to_flag_reason_not_to_notes(self):
        self.ingest(EXAMPLE + "\nCUSTOMER: ASL")
        j = self.ingest(EXAMPLE.replace("95938662", "11111111") + "\nCUSTOMER: ASL").json()  # same container, same month
        s = Shipment.objects.latest("id")
        self.assertTrue(j["flagged"])
        self.assertIn("Duplicate container", s.flag_reason)
        self.assertEqual(s.notes, "Klaipeda")

    def test_location_stays_empty_and_follows_the_customer(self):
        self.ingest(EXAMPLE)
        s = Shipment.objects.get()
        self.assertEqual((s.location, s.effective_location), ("", "UNASSIGNED"))
        s.customer = self.customer
        self.assertEqual(s.effective_location, "ASL")

    def test_the_invoice_line_of_a_whatsapp_shipment_is_clean(self):
        self.ingest(EXAMPLE + "\nCUSTOMER: ASL")
        d = Shipment.objects.get().invoice_line_defaults()
        self.assertEqual((d["description"], d["location"]), ("Klaipeda", "ASL"))
        Shipment.objects.all().delete()
        self.ingest(EXAMPLE.replace("Klaipeda\n", "") + "\nCUSTOMER: ASL")
        d = Shipment.objects.get().invoice_line_defaults()
        self.assertEqual((d["description"], d["location"]), ("", "ASL"))  # no "Auto-ingested from WhatsApp." on invoices

    def test_the_same_message_id_is_still_idempotent(self):
        a = self.ingest(EXAMPLE, message_id="3EB0X").json()
        b = self.ingest(EXAMPLE, message_id="3EB0X").json()
        self.assertEqual((a["status"], b["status"]), ("success", "duplicate"))
        self.assertEqual(Shipment.objects.count(), 1)


# ---------------------------------------------------------------------------
# F. Migration data step
# ---------------------------------------------------------------------------

class LegacyNotesMigration(QABase):
    def setUp(self):
        super().setUp()
        self.migration = importlib.import_module("shipments.migrations.0016_shipment_chassis_flag_reason")

    def make(self, container, notes):
        return Shipment.objects.create(customer=self.customer, date=date(2026, 1, 5), booking="B", container=container, seal="S", notes=notes)

    def test_system_text_moves_to_flag_reason_and_hand_written_notes_stay(self):
        legacy = self.make("C1", "Auto-ingested from WhatsApp.")
        flagged = self.make("C2", "Auto-ingested from WhatsApp. (Flagged: Duplicate container this billing period.)")
        manual = self.make("C3", "Deliver to gate 4")
        self.migration.split_system_text(django_apps, None)
        for s in (legacy, flagged, manual):
            s.refresh_from_db()
        self.assertEqual((legacy.notes, legacy.flag_reason), ("", ""))
        self.assertEqual(flagged.notes, "")
        self.assertIn("Duplicate container", flagged.flag_reason)
        self.assertEqual((manual.notes, manual.flag_reason), ("Deliver to gate 4", ""))

    def test_it_can_be_reversed(self):
        s = self.make("C1", "Auto-ingested from WhatsApp. (Flagged: Duplicate container this billing period.)")
        WhatsAppMessage.objects.create(raw_text="x", shipment=s)
        self.migration.split_system_text(django_apps, None)
        self.migration.restore_system_text(django_apps, None)
        s.refresh_from_db()
        self.assertTrue(s.notes.startswith("Auto-ingested from WhatsApp."))
        self.assertIn("Duplicate container", s.notes)
