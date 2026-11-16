"""[QA-050] Import Data: the statement layout (wide CSV / Excel workbook), preview then confirm, idempotent re-import.

The two real files that answered "Successfully imported 0 expenses" are wide (one column per category) while the
importer only knew a long Category/Amount layout.  Fixtures here reproduce the real layouts and the quirks of the 2025
workbook (double header row, PARTS for OTHER, empty cells, lower-case names, summary rows, non-transaction sheets) with
INVENTED names and amounts: the real statements are private business data and this repository is public.
"""
import io
import tempfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest import mock

import openpyxl
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext

from customers.models import Customer
from drivers.models import Driver
from shipments import statement_import as si
from shipments.models import Expense, ExpenseCategory, Invoice, InvoiceStatus

from .base import QABase

ROOT = Path(__file__).resolve().parents[2]
HEADER = "DATE,DEPOSIT,DEPOSIT FROM,IRP,PARKING,ON SITE,TRUCK,CHECK CHARGE,INSURANS,TOLL,FUEL,OTHER,CHASSIS,PAY,PAY TO,\r\n"

# Same shape as the real "STATMENTS - JAN.csv": quoted thousands, CRLF, '-' placeholders, trailing comma, mm-dd-yyyy.
JAN_ROWS = (
    '01-02-2025,"$7,500.00",ALPHA,$142.00,$300.00,$160.00,"$20,000.00",$10.00,$0.00,$0.00,$0.00,$0.00,$0.00,$0.00,-,\r\n'
    '01-06-2025,$0.00,-,$0.00,$0.00,$400.00,$0.00,$0.00,$0.00,$75.00,$350.00,$0.00,$0.00,"$3,000.00",DRIVER ONE,\r\n'
    '01-08-2025,"$5,000.00",BRAVO HAUL,$0.00,$0.00,$120.00,$0.00,$0.00,$0.00,$0.00,$420.00,$0.00,$0.00,$250.00,DRIVER TWO,\r\n'
)
JAN_CSV = (HEADER + JAN_ROWS).encode()

# Same shape as the real FEB file: PARTS instead of OTHER, 16 junk "Column N" headers, m/d/yyyy dates, a PROFIT summary row.
FEB_HEADER = ("DATE,DEPOSIT,DEPOSIT FROM,IRP,PARKING,ON SITE,TRUCK,CHECK CHARGE,INSURANS,TOLL,FUEL,PARTS,CHASSIS,PAY,PAY TO,"
              + ",".join(f"Column {i}" for i in range(1, 17)) + "\r\n")
FEB_ROWS = (
    '2/3/2025,$0.00,-,$0.00,$300.00,$0.00,$0.00,$0.00,$0.00,$75.00,$450.00,$0.00,$0.00,"$1,500.00",DRIVER ONE,,,,,,,,,,,,,,,,\r\n'
    '2/11/2025,$0.00,-,$0.00,$0.00,$0.00,$0.00,$0.00,$0.00,$75.00,$330.00,$70.00,$0.00,$0.00,-,,,,,,,,,,,,,,,,\r\n'
    'PROFIT,"$1,000.00",,,,,,,,,,,,,,,,,,,,,,,,,,,,,\r\n'
)
FEB_CSV = (FEB_HEADER + FEB_ROWS).encode()


def workbook_bytes(sheets):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


COLS = ["DATE", "DEPOSIT", "DEPOSIT FROM", "IRP", "PARKING", "ON SITE", "TRUCK", "CHECK CHARGE", "INSURANS", "TOLL", "FUEL", "OTHER", "CHASSIS", "PAY", "PAY TO"]


def demo_workbook():
    """A workbook with the quirks of the real 2025 file (also used to produce the demo screenshots)."""
    d = lambda m, day: date(2025, m, day)   # noqa: E731
    return workbook_bytes({
        "JAN": [COLS + [None] * 3,
                [d(1, 2), 7000, "ALPHA", 100, 300, 150, 20000, 10, 0, 0, 0, 0, 0, 0, "-"],
                [d(1, 6), 0, "-", 0, 0, 400, 0, 0, 0, 75, 350.5, 0, 0, 3000, "ALPHA"],
                [None] * 15,
                ["GROSS", 7000, None, 100, 300],
                ["TOTAL SPENDING", 24385.5],
                [None, None, None, None, None, None, None, None, None, None, None, None, None, "DRIVER THREE", 3555]],
        "MAY": [COLS,                                                   # the partial header row ...
                COLS + [f"Column {i}" for i in range(1, 17)],           # ... and the full one again
                [d(5, 1), 0, "-", 0, 300, 0, 0, 5, 0, 0, 0, 0, 0, 1600, "driver three"],
                [d(5, 2), None, None, None, 300, None, None, None, None, 75, 500, None, None, None, None],     # empty cells
                [d(5, 3), 1000, "charlie", None, None, None, None, None, None, None, None, None, None, None, None],
                ["GROSS", 8000],
                ["PROFIT", 1000, None, None, None, None, None, None, None, None, None, None, None, None, "driver three"]],
        "2025 ALL": [["MONTH", "IRP", "PARKING", "DEPOSIT"], ["JANUARY", 100, 300, 7000.0]],
        "1099 DRIVER": [["MONTHE", "PAYED"], ["JAN", 3555.0]],
    })


class ImportBase(QABase):
    def setUp(self):
        super().setUp()
        self.login()

    def preview(self, name, data):
        return self.client.post("/settings/import/process/", {"csv_file": SimpleUploadedFile(name, data)})

    def confirm(self, response, **extra):
        data = {"token": response.context["token"]}
        data.update(extra)
        return self.client.post("/settings/import/confirm/", data, follow=True)

    def do_import(self, name, data):
        r = self.preview(name, data)
        self.assertEqual(r.status_code, 200, "preview page expected")
        return self.confirm(r)

    @staticmethod
    def messages(response):
        return [str(m) for m in response.context["messages"]]

    def totals(self):
        paid = Invoice.objects.filter(status=InvoiceStatus.PAID)
        return (sum(i.total_amount for i in paid), sum(e.amount for e in Expense.objects.all()))


class WideCsvLayout(ImportBase):
    def test_wide_january_rows_become_invoices_expenses_and_driver_pay(self):
        """[IMP-WIDE] the exact situation that printed 'imported 0 expenses'."""
        r = self.do_import("Copy of 2025 STATMENTS - JAN.csv", JAN_CSV)
        self.assertTrue(any("Imported 2 deposits (paid invoices) and 12 expenses" in m and "January 2025" in m for m in self.messages(r)), self.messages(r))
        self.assertEqual(Invoice.objects.count(), 2)
        self.assertEqual({i.total_amount for i in Invoice.objects.all()}, {Decimal("7500.00"), Decimal("5000.00")})
        self.assertTrue(all(i.status == "paid" and i.paid_at == i.invoice_date for i in Invoice.objects.all()))
        by_cat = {}
        for e in Expense.objects.all():
            by_cat.setdefault(e.category, []).append(e.amount)
        self.assertEqual(sorted(by_cat["maintenance"]), sorted([Decimal("160.00"), Decimal("400.00"), Decimal("120.00")]))
        self.assertEqual(by_cat["truck"], [Decimal("20000.00")])
        self.assertEqual(sorted(by_cat["fuel"]), sorted([Decimal("350.00"), Decimal("420.00")]))
        self.assertEqual(sorted(by_cat["driver"]), [Decimal("250.00"), Decimal("3000.00")])
        self.assertEqual(Expense.objects.count(), 12, "10 cost cells + 2 driver-pay rows; '$0.00' and '-' cells are not rows")

    def test_totals_match_the_sheet_arithmetic(self):
        self.do_import("jan.csv", JAN_CSV)
        deposits, expenses = self.totals()
        self.assertEqual(deposits, Decimal("12500.00"))
        # 142+300+160+20000+10 + 400+75+350+3000 + 120+420+250
        self.assertEqual(expenses, Decimal("25227.00"))

    def test_driver_pay_is_stored_as_driver_with_the_driver_linked(self):
        self.do_import("jan.csv", JAN_CSV)
        pay = Expense.objects.filter(category=ExpenseCategory.DRIVER).order_by("amount")
        self.assertEqual([(e.driver.name, e.notes) for e in pay],
                         [("DRIVER TWO", "Imported: Pay → DRIVER TWO"), ("DRIVER ONE", "Imported: Pay → DRIVER ONE")])

    def test_february_parts_column_is_other_and_junk_columns_are_ignored(self):
        r = self.preview("feb.csv", FEB_CSV)
        self.assertEqual(r.context["warnings"], [], "no 'Column N' noise, and the PROFIT summary row is not an error")
        self.confirm(r)
        other = Expense.objects.get(amount=Decimal("70.00"))
        self.assertEqual((other.category, other.date, other.notes), ("other", date(2025, 2, 11), "Imported: PARTS"))
        self.assertEqual(Expense.objects.filter(category="toll").count(), 2)

    def test_dates_money_and_names(self):
        self.assertEqual(si.parse_date("01-02-2025"), date(2025, 1, 2))
        self.assertEqual(si.parse_date("2/3/2025"), date(2025, 2, 3))
        self.assertEqual(si.parse_date("2025-03-04"), date(2025, 3, 4))
        self.assertEqual(si.parse_date("2025-03-04 00:00:00"), date(2025, 3, 4))
        self.assertEqual(si.parse_date(45658), date(2025, 1, 1))     # Excel serial number
        self.assertIsNone(si.parse_date("GROSS"))
        self.assertIsNone(si.parse_date(""))
        for raw, want in [("$7,500.00", "7500.00"), ("1250.5", "1250.50"), (449.38, "449.38"), (0.1 + 0.2, "0.30"), ("(12.00)", "-12.00"),
                          ("-", "0.00"), ("--", "0.00"), ("", "0.00"), (None, "0.00"), ("$ -", "0.00"), (3, "3.00")]:
            self.assertEqual(si.parse_money(raw), Decimal(want), repr(raw))
        for bad in ("TBD", "12abc", "$$5", True):
            self.assertIsNone(si.parse_money(bad), repr(bad))
        self.assertEqual(si.clean_name(" CASH  "), "CASH")
        for none in ("-", "--", "", None, "N/A"):
            self.assertIsNone(si.clean_name(none))

    def test_windows_1252_file_is_read(self):
        data = (HEADER + '01-02-2025,"$100.00",Café Ltd,$0.00,$0.00,$0.00,$0.00,$0.00,$0.00,$0.00,$0.00,$0.00,$0.00,$0.00,-,\r\n').encode("cp1252")
        self.do_import("x.csv", data)
        self.assertTrue(Customer.objects.filter(name="Café Ltd").exists())

    def test_legacy_long_layout_still_works(self):
        data = (b"DATE,Category,Description,Amount\n01-05-2026,FUEL,Pilot,$250.00\n01/06/2026,toll,IPASS,12.50\n"
                b",FUEL,no date,5\n01-07-2026,FUEL,zero,0\n")
        self.do_import("long.csv", data)
        got = sorted(Expense.objects.values_list("date", "category", "amount", "notes"))
        self.assertEqual(got, [(date(2026, 1, 5), "fuel", Decimal("250.00"), "Imported: Pilot"),
                               (date(2026, 1, 6), "toll", Decimal("12.50"), "Imported: IPASS")])
        self.assertEqual(Invoice.objects.count(), 0)


class WorkbookLayout(ImportBase):
    def test_every_month_sheet_is_read_and_summary_sheets_are_skipped(self):
        r = self.preview("2025.xlsx", demo_workbook())
        self.assertEqual({(m.year, m.month) for m in r.context["months"]}, {(2025, 1), (2025, 5)})
        skipped = {s.name for s in r.context["sheets"] if s.status == "skipped"}
        self.assertEqual(skipped, {"2025 ALL", "1099 DRIVER"})
        self.assertEqual(r.context["warnings"], [])

    def test_workbook_import_values(self):
        self.do_import("2025.xlsx", demo_workbook())
        deposits, expenses = self.totals()
        self.assertEqual(deposits, Decimal("8000.00"))
        # JAN 100+300+150+20000+10 + 400+75+350.5+3000 = 24385.50 ; MAY 300+5+1600 + 300+75+500 = 2780
        self.assertEqual(expenses, Decimal("27165.50"))
        self.assertEqual(Expense.objects.filter(date__month=5, category="driver").get().driver.name, "Driver Three")

    def test_empty_cells_and_lowercase_names(self):
        self.do_import("2025.xlsx", demo_workbook())
        self.assertEqual(sorted(Customer.objects.exclude(pk=self.customer.pk).values_list("name", flat=True)), ["ALPHA", "Charlie"])
        self.assertEqual(sorted(Driver.objects.exclude(pk=self.driver.pk).values_list("name", flat=True)), ["ALPHA", "Driver Three"])

    def test_same_name_in_different_case_is_one_customer(self):
        data = workbook_bytes({"JAN": [COLS,
                                       [date(2025, 1, 2), 100, "Delta", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, "-"],
                                       [date(2025, 1, 3), 200, "DELTA", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, "-"],
                                       [date(2025, 1, 4), 300, "delta ", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, "-"]]})
        self.do_import("x.xlsx", data)
        self.assertEqual(Customer.objects.filter(name__iexact="delta").count(), 1)
        self.assertEqual(Invoice.objects.filter(customer__name__iexact="delta").count(), 3)

    def test_corrupt_and_wrong_files_get_a_friendly_error(self):
        for name, data, needle in [("x.xlsx", b"not a zip at all", "valid .xlsx"),
                                   ("old.xls", b"\xd0\xcf\x11\xe0", ".xls"),
                                   ("x.exe", b"MZ", "Only .csv and .xlsx"),
                                   ("empty.csv", b"", "No importable rows"),
                                   ("nodate.csv", b"A,B\n1,2\n", "No importable rows")]:
            with self.subTest(name=name):
                r = self.client.post("/settings/import/process/", {"csv_file": SimpleUploadedFile(name, data)}, follow=True)
                msgs = " ".join(self.messages(r))
                self.assertIn(needle, msgs)
                self.assertNotIn("Successfully imported", msgs)
        self.assertEqual((Expense.objects.count(), Invoice.objects.count()), (0, 0))

    def test_oversize_upload_is_refused(self):
        r = self.client.post("/settings/import/process/", {"csv_file": SimpleUploadedFile("big.csv", b"x" * (si.MAX_UPLOAD_BYTES + 1))}, follow=True)
        self.assertIn("too large", " ".join(self.messages(r)))


class PreviewAndConfirm(ImportBase):
    def test_preview_saves_nothing_and_shows_the_numbers(self):
        r = self.preview("jan.csv", JAN_CSV)
        self.assertEqual(r.status_code, 200)
        self.assertEqual((Expense.objects.count(), Invoice.objects.count(), Customer.objects.count(), Driver.objects.count()), (0, 0, 1, 1))
        self.assertEqual(r.context["totals"]["deposits_total"], Decimal("12500.00"))
        html = r.content.decode()
        self.assertIn("January 2025", html)
        self.assertIn("12,500.00", html)
        self.assertIn("Confirm import", html)

    def test_confirm_requires_a_valid_one_time_token(self):
        r = self.preview("jan.csv", JAN_CSV)
        bad = self.client.post("/settings/import/confirm/", {"token": "nope"}, follow=True)
        self.assertIn("expired", " ".join(self.messages(bad)))
        self.assertEqual(Invoice.objects.count(), 0)
        self.assertEqual(self.confirm(r).status_code, 200)
        self.assertEqual(Invoice.objects.count(), 2)
        again = self.client.post("/settings/import/confirm/", {"token": r.context["token"]}, follow=True)
        self.assertIn("expired", " ".join(self.messages(again)))
        self.assertEqual(Invoice.objects.count(), 2, "a second click must not import twice")

    def test_confirm_without_a_preview(self):
        r = self.client.post("/settings/import/confirm/", {}, follow=True)
        self.assertIn("expired", " ".join(self.messages(r)))

    def test_both_steps_require_login_and_post(self):
        self.client.logout()
        for url in ("/settings/import/process/", "/settings/import/confirm/"):
            self.assertEqual(self.client.post(url, {}).status_code, 302)
            self.assertIn("/login", self.client.post(url, {})["Location"])
        self.login()
        self.assertEqual(self.client.get("/settings/import/confirm/").status_code, 405)

    def test_the_form_accepts_excel_and_describes_the_real_format(self):
        html = self.client.get("/settings/import/").content.decode()
        self.assertIn(".xlsx", html)
        self.assertIn("DEPOSIT FROM", html)
        self.assertNotIn("Required columns", html)

    def test_import_is_all_or_nothing(self):
        r = self.preview("jan.csv", JAN_CSV)
        with mock.patch("shipments.statement_import.Invoice.objects.bulk_create", side_effect=RuntimeError("disk full")):
            res = self.confirm(r)
        self.assertEqual((Expense.objects.count(), Invoice.objects.count(), Customer.objects.count(), Driver.objects.count()), (0, 0, 1, 1))
        self.assertIn("nothing was saved", " ".join(self.messages(res)))
        self.assertNotIn("disk full", " ".join(self.messages(res)), "internal error text must not be shown to the user")
        self.assertEqual(self.confirm(r).status_code, 200)       # the preview is still valid: retry works
        self.assertEqual(Invoice.objects.count(), 2)


class Idempotent(ImportBase):
    def test_uploading_the_same_file_twice_adds_nothing(self):
        self.do_import("jan.csv", JAN_CSV)
        before = (Expense.objects.count(), Invoice.objects.count(), Customer.objects.count(), Driver.objects.count())
        r = self.preview("jan.csv", JAN_CSV)
        self.assertEqual(r.context["plan"].total_new, 0)
        self.assertEqual(r.context["plan"].duplicates, 14)       # 12 expenses + 2 deposits
        res = self.confirm(r)
        self.assertEqual((Expense.objects.count(), Invoice.objects.count(), Customer.objects.count(), Driver.objects.count()), before)
        self.assertIn("Nothing new", " ".join(self.messages(res)))

    def test_identical_rows_inside_one_file_are_all_kept_then_not_repeated(self):
        row = '01-15-2025,$0.00,-,$0.00,$0.00,$0.00,$0.00,$0.00,$0.00,$0.00,$450.00,$0.00,$0.00,$0.00,-,\r\n'
        data = (HEADER + row * 2).encode()
        self.do_import("x.csv", data)
        self.assertEqual(Expense.objects.filter(amount=Decimal("450.00")).count(), 2)
        self.do_import("x.csv", data)
        self.assertEqual(Expense.objects.filter(amount=Decimal("450.00")).count(), 2)
        three = (HEADER + row * 3).encode()          # one genuinely new entry in a bigger file
        self.do_import("x.csv", three)
        self.assertEqual(Expense.objects.filter(amount=Decimal("450.00")).count(), 3)

    def test_overlapping_files_only_add_what_is_new(self):
        self.do_import("jan.csv", JAN_CSV)
        extra = '01-09-2025,"$1,375.00",CHARLIE,$0.00,$0.00,$0.00,$0.00,$0.00,$0.00,$75.00,$0.00,$10.20,$0.00,$0.00,-,\r\n'
        self.do_import("jan2.csv", (HEADER + JAN_ROWS + extra).encode())
        self.assertEqual(Invoice.objects.count(), 3)
        self.assertEqual(Expense.objects.filter(date=date(2025, 1, 9)).count(), 2)

    def test_hand_entered_expenses_never_count_as_duplicates(self):
        Expense.objects.create(date=date(2025, 1, 2), category="irp", amount=Decimal("142.00"), notes="typed by hand")
        self.do_import("jan.csv", JAN_CSV)
        self.assertEqual(Expense.objects.filter(date=date(2025, 1, 2), category="irp").count(), 2)

    def test_a_previously_imported_row_counts_even_when_the_name_case_differs(self):
        self.do_import("jan.csv", JAN_CSV)
        lower = JAN_CSV.replace(b",DRIVER ONE,", b",driver one,").replace(b",ALPHA,", b",alpha,")
        r = self.preview("jan.csv", lower)
        self.assertEqual(r.context["plan"].total_new, 0)


class CustomersAndDrivers(ImportBase):
    def test_unknown_customer_and_driver_are_created_with_valid_fields(self):
        """[IMP-STREET] the old command did Customer.objects.create(street=...), a field that no longer exists."""
        self.do_import("jan.csv", JAN_CSV)
        c = Customer.objects.get(name="ALPHA")
        self.assertEqual((c.address, c.abbreviation, c.email), ("N/A", "ALPHA", "alpha@import.local"))
        self.assertIn("statement import", c.notes)
        self.assertEqual(Customer.objects.get(name="BRAVO HAUL").abbreviation, "BRAVO HAUL")   # spelling is kept, never "corrected"
        d = Driver.objects.get(name="DRIVER ONE")
        self.assertTrue(d.license_number.startswith("IMP-") and len(d.license_number) <= 20)

    def test_existing_customer_is_matched_by_name_and_abbreviation_ignoring_case(self):
        data = (HEADER
                + '01-02-2025,"$100.00",acme shipping line,' + "$0.00," * 10 + "$0.00,-,\r\n"
                + '01-03-2025,"$200.00",asl,' + "$0.00," * 10 + "$0.00,-,\r\n").encode()
        self.do_import("x.csv", data)
        self.assertEqual(Customer.objects.count(), 1)
        self.assertEqual(Invoice.objects.filter(customer=self.customer).count(), 2)

    def test_existing_driver_is_matched_ignoring_case(self):
        data = (HEADER + "01-02-2025,$0.00,-," + "$0.00," * 10 + '"$300.00",moe driver,\r\n').encode()
        self.do_import("x.csv", data)
        self.assertEqual(Driver.objects.count(), 1)
        self.assertEqual(Expense.objects.get().driver, self.driver)

    def test_a_deposit_without_a_name_goes_to_an_explicit_customer_not_to_an_arbitrary_one(self):
        data = (HEADER + '01-02-2025,"$100.00",-,' + "$0.00," * 10 + "$0.00,-,\r\n").encode()
        self.do_import("x.csv", data)
        inv = Invoice.objects.get()
        self.assertEqual(inv.customer.name, si.UNASSIGNED_CUSTOMER)
        self.assertNotEqual(inv.customer, Customer.objects.exclude(name=si.UNASSIGNED_CUSTOMER).first())
        self.do_import("x.csv", data)
        self.assertEqual(Customer.objects.filter(name=si.UNASSIGNED_CUSTOMER).count(), 1)

    def test_driver_pay_without_a_name_is_kept_without_a_driver(self):
        data = (HEADER + "01-02-2025,$0.00,-," + "$0.00," * 10 + '"$300.00",-,\r\n').encode()
        self.do_import("x.csv", data)
        e = Expense.objects.get()
        self.assertEqual((e.category, e.driver, e.notes), ("driver", None, "Imported: Pay → Unknown"))

    def test_names_that_only_share_a_word_are_not_flagged(self):
        r = self.preview("jan.csv", JAN_CSV)       # DRIVER ONE / DRIVER TWO are different people
        self.assertEqual(r.context["warnings"], [])

    def test_the_result_message_uses_proper_plurals(self):
        data = (HEADER + '01-02-2025,"$100.00",Solo Co,' + "$0.00," * 10 + '"$50.00",Solo Driver,\r\n').encode()
        msgs = " ".join(self.messages(self.do_import("x.csv", data)))
        self.assertIn("Created 1 customer and 1 driver with", msgs)

    def test_misspelled_names_are_warned_about_but_never_merged(self):
        names = ["NORTHWIND", "NORTHWNID", "OAKLAND HAUL", "Oakland Hual"]
        rows = "".join(f'01-0{i}-2025,"$100.00",{name},' + "$0.00," * 10 + "$0.00,-,\r\n" for i, name in enumerate(names, 1))
        r = self.preview("x.csv", (HEADER + rows).encode())
        text = " ".join(r.context["warnings"])
        self.assertIn("NORTHWIND", text)
        self.assertIn("looks like", text)
        self.assertIn("Oakland Hual", text)
        self.confirm(r)
        self.assertEqual(Customer.objects.filter(name__in=["NORTHWIND", "NORTHWNID"]).count(), 2)


class Warnings(ImportBase):
    def test_unreadable_negative_and_unknown_cells_are_reported_not_swallowed(self):
        header = "DATE,DEPOSIT,DEPOSIT FROM,FUEL,TOLL,MYSTERY\r\n"
        rows = ("01-02-2025,$0.00,-,TBD,$-5.00,$9.00\r\n"
                "13-45-2025,$0.00,-,$5.00,$0.00,\r\n"
                "01-03-2025,$0.00,-,$7.00,$0.00,\r\n")
        r = self.preview("x.csv", (header + rows).encode())
        text = "\n".join(r.context["warnings"])
        self.assertIn("'TBD' in FUEL is not an amount", text)
        self.assertIn("negative amount", text)
        self.assertIn("'13-45-2025' is not a date", text)
        self.assertIn("column 'MYSTERY' is not recognised", text)
        self.confirm(r)
        self.assertEqual(list(Expense.objects.values_list("amount", flat=True)), [Decimal("7.00")])


class Efficiency(ImportBase):
    def test_a_year_of_rows_needs_a_handful_of_queries(self):
        rows = "".join(
            f'{1 + i % 12:02d}-{1 + i % 27:02d}-2025,"${100 + i}.00",CUST{i % 9},$1.00,$2.00,$3.00,$0.00,$0.00,$0.00,$4.00,$5.00,$0.00,$0.00,"${50 + i}.00",DRV{i % 7},\r\n'
            for i in range(600))
        data = (HEADER + rows).encode()
        with CaptureQueriesContext(connection) as preview_q:
            r = self.preview("year.csv", data)
        self.assertLess(len(preview_q), 20, "preview must not query per row")
        with CaptureQueriesContext(connection) as confirm_q:
            self.confirm(r)
        self.assertLess(len(confirm_q), 60, "confirm must not query per row (600 rows)")
        self.assertEqual(Invoice.objects.count(), 600)
        self.assertEqual(Expense.objects.count(), 600 * 6)   # 5 cost cells + driver pay per row


class CommandLine(ImportBase):
    def run_command(self, name, data, *args):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / name
            path.write_bytes(data)
            out = io.StringIO()
            call_command("import_expenses", str(path), *args, stdout=out, stderr=io.StringIO())
            return out.getvalue()

    def test_command_imports_with_unknown_customers_instead_of_crashing(self):
        out = self.run_command("jan.csv", JAN_CSV)
        self.assertIn("Done: 12 expenses, 2 invoices", out)
        self.assertEqual(Invoice.objects.count(), 2)

    def test_command_reads_a_workbook_and_dry_run_changes_nothing(self):
        data = workbook_bytes({"JAN": [COLS, [date(2025, 1, 2), 100, "asl", 0, 0, 0, 0, 0, 0, 0, 5, 0, 0, 0, "-"]]})
        out = self.run_command("a.xlsx", data, "--dry-run")
        self.assertIn("Dry run: 1 expenses and 1 invoices", out)
        self.assertEqual((Expense.objects.count(), Invoice.objects.count()), (0, 0))
        self.run_command("a.xlsx", data)
        self.assertEqual((Expense.objects.count(), Invoice.objects.count()), (1, 1))

    def test_clear_only_removes_previously_imported_rows(self):
        manual = Expense.objects.create(date=date(2025, 1, 2), category="fuel", amount=Decimal("9.99"), notes="receipt")
        self.run_command("jan.csv", JAN_CSV)
        self.run_command("jan.csv", JAN_CSV, "--clear")
        self.assertTrue(Expense.objects.filter(pk=manual.pk).exists())
        self.assertEqual(Invoice.objects.count(), 2)
        self.assertEqual(Expense.objects.exclude(pk=manual.pk).count(), 12)


REAL = {
    "JAN": ROOT / "docs" / "Copy of 2025 STATMENTS - JAN.csv",
    "FEB": ROOT / "docs" / "Copy of 2025 STATMENTS - FEB.csv",
    "XLSX": ROOT / "docs" / "Copy of 2025 STATMENTS.xlsx",
}


def _num(value):
    if value is None:
        return Decimal("0")
    if isinstance(value, str):
        value = value.replace("$", "").replace(",", "").strip()
        return Decimal(value) if value not in ("", "-", "--") else Decimal("0")
    return Decimal(str(value))


def workbook_oracle(path):
    """month -> (deposits, spending), computed straight from the cells and independently of shipments.statement_import:
    DEPOSIT (col B) and IRP..CHASSIS plus PAY (cols D..N) of every row whose first cell is a date."""
    out = {}
    wb = openpyxl.load_workbook(path, data_only=True)
    for ws in wb:
        deposits = spending = Decimal("0")
        month = None
        for row in ws.iter_rows(values_only=True):
            if isinstance(row[0], datetime):
                month = row[0].month
                deposits += _num(row[1])
                spending += sum((_num(c) for c in row[3:14]), Decimal("0"))
        if month:
            out[month] = (deposits, spending)
    return out


class RealFiles(ImportBase):
    """Reconciles the importer with the owner's real statements.  The files are git-ignored (private business data) and the
    expected figures are derived from the cells themselves, so nothing private is stored in the repository; without the
    files these tests are skipped."""

    def need(self, key):
        if not REAL[key].exists():
            self.skipTest(f"{REAL[key].name} is not in docs/ (private data, not in the repository)")
        return REAL[key].read_bytes()

    def report(self, month):
        r = self.client.get(f"/reports/financial/monthly/?year=2025&month={month}")
        return r.context["revenue"], r.context["total_expenses"]

    def test_january_and_february_csv_match_the_workbook_arithmetic(self):
        self.need("XLSX")
        oracle = workbook_oracle(REAL["XLSX"])
        self.do_import(REAL["JAN"].name, self.need("JAN"))
        self.assertEqual(self.report(1), oracle[1])
        self.do_import(REAL["FEB"].name, self.need("FEB"))
        self.assertEqual(self.report(2), oracle[2])

    def test_whole_workbook_matches_month_by_month(self):
        data = self.need("XLSX")
        oracle = workbook_oracle(REAL["XLSX"])
        self.assertEqual(sorted(oracle), list(range(1, 13)))
        r = self.preview(REAL["XLSX"].name, data)
        self.assertEqual(len(r.context["months"]), 12)
        self.confirm(r)
        for month, expected in oracle.items():
            with self.subTest(month=month):
                self.assertEqual(self.report(month), expected)
        again = self.preview(REAL["XLSX"].name, data)
        self.assertEqual(again.context["plan"].total_new, 0, "re-importing the whole workbook adds nothing")
