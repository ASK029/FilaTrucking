"""Import of the monthly "statement" sheets (QA-050).

One module serves the Import Data page and the ``import_expenses`` command.  Flow:

    read_sheets(upload)  ->  parse_sheets(...)  ->  build_plan(rows)  ->  apply_plan(plan)
       bytes -> cell grids     header detection +      reads the database,      one transaction,
                               cell parsing: JSON-     decides what is new,     bulk_create
                               friendly row dicts      what is a duplicate

The statement layout is *wide*: one row per day/transaction with a column per cost category
(``DATE, DEPOSIT, DEPOSIT FROM, IRP, PARKING, ON SITE, TRUCK, CHECK CHARGE, INSURANS, TOLL, FUEL, OTHER, CHASSIS,
PAY, PAY TO``).  The legacy long layout (``DATE, Category, Amount, Description``) is still understood.

Nothing here prints or stores the uploaded file: the page keeps only the parsed rows in the session between the
preview and the confirmation, and ``build_plan`` is run again at confirmation time against the database as it is then.
"""
from __future__ import annotations

import csv
import io
import re
import uuid
import zipfile
from calendar import monthrange
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from difflib import SequenceMatcher
from itertools import islice

from django.db import transaction

from customers.models import Customer
from drivers.models import Driver
from shipments.models import Expense, ExpenseCategory, Invoice, InvoiceStatus

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_SHEETS = 40
MAX_SCAN_ROWS = 20000        # rows looked at per sheet (empty trailing rows included)
MAX_DATA_ROWS = 5000         # dated rows kept per sheet
MAX_UNZIPPED_BYTES = 100 * 1024 * 1024
HEADER_SCAN_ROWS = 10
SIMILAR_NAME_RATIO = 0.85   # NORTHWIND/NORTHWNID = 0.89 and OAKLAND HAUL/OAKLAND HUAL = 0.92 are typos; DRIVER ONE/DRIVER TWO = 0.80 is not
MAX_WARNINGS = 200
UNASSIGNED_CUSTOMER = "Unassigned deposits"
PLACEHOLDER_PHONE = "+10000000000"
NOTE_PREFIX = "Imported"

TWO_PLACES = Decimal("0.01")
ZERO = Decimal("0")
MAX_AMOUNT = Decimal("1000000000")   # Expense/Invoice use max_digits=12, decimal_places=2

# Column header (normalised) -> (ExpenseCategory, label used in the expense note).  The labels are the ones the old
# command wrote, so rows imported before this module existed are still recognised as duplicates.
COST_COLUMNS = {
    "IRP": (ExpenseCategory.IRP, "IRP"),
    "PARKING": (ExpenseCategory.PARKING, "PARKING"),
    "ON SITE": (ExpenseCategory.MAINTENANCE, "On Site"),
    "ONSITE": (ExpenseCategory.MAINTENANCE, "On Site"),
    "TRUCK": (ExpenseCategory.TRUCK, "TRUCK"),
    "CHECK CHARGE": (ExpenseCategory.CHECK_CHARGE, "CHECK CHARGE"),
    "CHECKCHARGE": (ExpenseCategory.CHECK_CHARGE, "CHECK CHARGE"),
    "INSURANS": (ExpenseCategory.INSURANCE, "Insurance"),
    "INSURANCE": (ExpenseCategory.INSURANCE, "Insurance"),
    "TOLL": (ExpenseCategory.TOLL, "Toll"),
    "TOLLS": (ExpenseCategory.TOLL, "Toll"),
    "FUEL": (ExpenseCategory.FUEL, "Fuel"),
    "OTHER": (ExpenseCategory.OTHER, "OTHER"),
    "PARTS": (ExpenseCategory.OTHER, "PARTS"),     # the FEB 2025 sheet calls the OTHER column "PARTS"
    "CHASSIS": (ExpenseCategory.CHASSIS, "CHASSIS"),
}
OTHER_ROLES = {
    "DATE": "date",
    "DEPOSIT": "deposit", "DEPOSITS": "deposit",
    "DEPOSIT FROM": "deposit_from", "FROM": "deposit_from", "CUSTOMER": "deposit_from",
    "PAY": "pay", "DRIVER PAY": "pay",
    "PAY TO": "pay_to", "PAYTO": "pay_to", "DRIVER": "pay_to",
    "CATEGORY": "category", "AMOUNT": "amount", "DESCRIPTION": "description",
}
# Words in the DATE column that mark summary rows at the bottom of a sheet: never an error.
SUMMARY_LABELS = {"DATE", "GROSS", "TOTAL", "TOTAL SPENDING", "PROFIT", "NET", "SUBTOTAL", "SUM", "TOTALS"}
NAME_PLACEHOLDERS = {"", "-", "--", "---", "n/a", "na", "none", "null", "unknown"}
JUNK_HEADER = re.compile(r"^COLUMN \d+$")
DATE_FORMATS = ("%m-%d-%Y", "%m/%d/%Y", "%Y-%m-%d", "%m-%d-%y", "%m/%d/%y", "%d.%m.%Y")
MONEY_RE = re.compile(r"^\(?-?\$?\s*-?\d[\d,]*(\.\d+)?\)?$|^\(?-?\$?\s*-?\.\d+\)?$")


class StatementImportError(Exception):
    """A problem with the uploaded file as a whole; the message is safe to show to the user."""


# ---------------------------------------------------------------------------------------------------------------
# 1. Reading the file
# ---------------------------------------------------------------------------------------------------------------

@dataclass
class RawSheet:
    name: str
    rows: list          # list of tuples/lists of cell values
    truncated: bool = False


def read_sheets(data: bytes, filename: str) -> list[RawSheet]:
    """Turn the uploaded bytes into cell grids: one sheet for a CSV, one per worksheet for a workbook."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise StatementImportError(f"The file is too large ({len(data) // 1024 // 1024} MB). The limit is {MAX_UPLOAD_BYTES // 1024 // 1024} MB.")
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if ext == "csv":
        return [_read_csv(data, filename)]
    if ext == "xlsx":
        return _read_xlsx(data)
    if ext in ("xls", "xlsm", "xlsb"):
        raise StatementImportError(f"'.{ext}' files are not supported. In Excel use File > Save As > Excel Workbook (.xlsx) or CSV.")
    raise StatementImportError("Only .csv and .xlsx files are supported.")


def _read_csv(data: bytes, filename: str) -> RawSheet:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")
    rows = []
    truncated = False
    try:
        for i, row in enumerate(csv.reader(io.StringIO(text))):
            if i >= MAX_SCAN_ROWS:
                truncated = True
                break
            rows.append(row)
    except csv.Error as exc:
        raise StatementImportError(f"The CSV file could not be read: {exc}") from exc
    name = re.sub(r"\.csv$", "", filename, flags=re.I)
    return RawSheet(name=name, rows=rows, truncated=truncated)


def _read_xlsx(data: bytes) -> list[RawSheet]:
    try:
        import openpyxl
    except ImportError as exc:   # pragma: no cover - dependency is pinned in requirements.txt
        raise StatementImportError("Excel files need the 'openpyxl' package, which is not installed.") from exc
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:   # a .xlsx is a zip: refuse "zip bombs" before parsing
            if sum(i.file_size for i in zf.infolist()) > MAX_UNZIPPED_BYTES:
                raise StatementImportError("The workbook is too large when unpacked.")
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except StatementImportError:
        raise
    except Exception as exc:
        raise StatementImportError("This does not look like a valid .xlsx workbook (it could not be opened).") from exc
    sheets = []
    try:
        names = wb.sheetnames
        if len(names) > MAX_SHEETS:
            raise StatementImportError(f"The workbook has {len(names)} sheets; the limit is {MAX_SHEETS}.")
        for name in names:
            ws = wb[name]
            try:
                ws.reset_dimensions()      # some exporters write a wrong <dimension>; scan what is really there
            except Exception:              # pragma: no cover
                pass
            rows, truncated = [], False
            for i, row in enumerate(islice(ws.iter_rows(values_only=True), MAX_SCAN_ROWS + 1)):
                if i >= MAX_SCAN_ROWS:
                    truncated = True
                    break
                rows.append(row)
            sheets.append(RawSheet(name=name, rows=rows, truncated=truncated))
    finally:
        wb.close()
    return sheets


# ---------------------------------------------------------------------------------------------------------------
# 2. Cell parsing
# ---------------------------------------------------------------------------------------------------------------

def norm_header(value) -> str:
    return re.sub(r"[^A-Z0-9]+", " ", str(value if value is not None else "").upper()).strip()


def parse_date(value):
    """date | None.  Accepts date/datetime cells, US-style text, ISO text and Excel serial numbers."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if 20000 < value < 80000:       # an Excel serial number in a cell that was not formatted as a date
            return (datetime(1899, 12, 30) + timedelta(days=float(value))).date()
        return None
    text = str(value or "").strip()
    if not text:
        return None
    iso = re.match(r"^(\d{4}-\d{2}-\d{2})[ T]", text)    # '2025-01-02 00:00:00' / '2025-01-02T00:00:00'
    if iso:
        text = iso.group(1)
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_money(value):
    """Decimal (0 for blank / '-' / '--') or None when the cell holds something that is not an amount."""
    if value is None or isinstance(value, bool):
        return ZERO if value is None else None
    if isinstance(value, (int, float, Decimal)):
        try:
            amount = Decimal(str(value))
        except InvalidOperation:
            return None
    else:
        text = str(value).strip()
        if text in ("", "-", "--", "---", "$-", "$ -"):
            return ZERO
        if not MONEY_RE.match(text):
            return None
        negative = text.startswith("(") or "-" in text
        digits = re.sub(r"[^\d.]", "", text)
        try:
            amount = Decimal(digits)
        except InvalidOperation:
            return None
        amount = -amount if negative else amount
    if not amount.is_finite():
        return None
    return amount.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def clean_name(value):
    """Trimmed display name, or None for the placeholders the sheets use for "nobody" ('-', '--', blank ...)."""
    text = re.sub(r"\s+", " ", str(value if value is not None else "")).strip()
    return None if text.lower() in NAME_PLACEHOLDERS else text[:100]


def name_key(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().casefold()


def display_name(name: str) -> str:
    """'delta' -> 'Delta', but 'ABC' and 'Oakland Haul' keep the way they were written."""
    return name.title() if name == name.lower() else name


def _blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


# ---------------------------------------------------------------------------------------------------------------
# 3. Sheets -> rows
# ---------------------------------------------------------------------------------------------------------------

@dataclass
class SheetInfo:
    name: str
    status: str            # "read" or "skipped"
    reason: str = ""
    rows: int = 0          # dated rows found
    header_row: int = 0    # 1-based


@dataclass
class ParseResult:
    rows: list = field(default_factory=list)        # JSON-friendly dicts, see _parse_sheet
    sheets: list = field(default_factory=list)      # list[SheetInfo]
    warnings: list = field(default_factory=list)

    def warn(self, message: str):
        if len(self.warnings) < MAX_WARNINGS:
            self.warnings.append(message)
        elif len(self.warnings) == MAX_WARNINGS:
            self.warnings.append("... more warnings were left out.")


def parse_sheets(sheets: list[RawSheet]) -> ParseResult:
    result = ParseResult()
    for sheet in sheets:
        _parse_sheet(sheet, result)
    return result


def _find_header(rows):
    """(index, {column index: role info}) of the first row that has DATE plus something importable, else None."""
    for idx, row in enumerate(islice(rows, HEADER_SCAN_ROWS)):
        roles, seen = {}, set()
        for col, cell in enumerate(row):
            key = norm_header(cell)
            if not key or key in seen:
                continue
            seen.add(key)
            if key in COST_COLUMNS:
                roles[col] = ("cost", key)
            elif key in OTHER_ROLES:
                roles[col] = (OTHER_ROLES[key], key)
        kinds = {role for role, _ in roles.values()}
        if "date" in kinds and (kinds & {"cost", "deposit", "pay"} or {"category", "amount"} <= kinds):
            return idx, roles, row
    return None


def _parse_sheet(sheet: RawSheet, result: ParseResult):
    found = _find_header(sheet.rows)
    if not found:
        result.sheets.append(SheetInfo(sheet.name, "skipped", "no header row with DATE and amount columns (not a transaction sheet)"))
        return
    header_idx, roles, header = found
    by_role = {}
    for col, (role, key) in roles.items():
        by_role.setdefault(role, col)
    cost_cols = {col: COST_COLUMNS[key] + (key,) for col, (role, key) in roles.items() if role == "cost"}
    recognised = set(roles)
    unknown_data = Counter()
    kept = 0
    if sheet.truncated:
        result.warn(f"Sheet '{sheet.name}': only the first {MAX_SCAN_ROWS} rows were read.")

    for idx in range(header_idx + 1, len(sheet.rows)):
        row = sheet.rows[idx]
        line = idx + 1
        date_cell = row[by_role["date"]] if by_role["date"] < len(row) else None
        if _blank(date_cell):
            continue                                    # blank / totals area: nothing to import
        when = parse_date(date_cell)
        if when is None:
            label = norm_header(date_cell)
            if label not in SUMMARY_LABELS and not label.startswith("TOTAL"):
                result.warn(f"Sheet '{sheet.name}' row {line}: '{str(date_cell).strip()[:30]}' is not a date, row skipped.")
            continue
        if not 2000 <= when.year <= 2100:
            result.warn(f"Sheet '{sheet.name}' row {line}: date {when.isoformat()} looks wrong, row skipped.")
            continue
        if kept >= MAX_DATA_ROWS:
            result.warn(f"Sheet '{sheet.name}': more than {MAX_DATA_ROWS} rows, the rest was left out.")
            break

        def cell(role):
            col = by_role.get(role)
            return row[col] if col is not None and col < len(row) else None

        def money(value, what):
            amount = parse_money(value)
            if amount is None:
                result.warn(f"Sheet '{sheet.name}' row {line}: '{str(value).strip()[:30]}' in {what} is not an amount, ignored.")
                return ZERO
            if amount < 0:
                result.warn(f"Sheet '{sheet.name}' row {line}: negative amount {amount} in {what} ignored.")
                return ZERO
            if amount >= MAX_AMOUNT:
                result.warn(f"Sheet '{sheet.name}' row {line}: amount {amount} in {what} is too large, ignored.")
                return ZERO
            return amount

        entry = {"sheet": sheet.name, "line": line, "date": when.isoformat(), "costs": []}

        if "deposit" in by_role:
            amount = money(cell("deposit"), "DEPOSIT")
            if amount > 0:
                entry["deposit"] = str(amount)
                name = clean_name(cell("deposit_from"))
                if name:
                    entry["deposit_from"] = name
        for col, (category, label, key) in cost_cols.items():
            amount = money(row[col] if col < len(row) else None, key)
            if amount > 0:
                entry["costs"].append([category, label, str(amount)])
        if "pay" in by_role:
            amount = money(cell("pay"), "PAY")
            if amount > 0:
                entry["pay"] = str(amount)
                name = clean_name(cell("pay_to"))
                if name:
                    entry["pay_to"] = name
        if "amount" in by_role and "category" in by_role:        # legacy long layout
            amount = money(cell("amount"), "Amount")
            if amount > 0:
                category_text = str(cell("category") or "").strip()
                category, label = _legacy_category(category_text)
                description = str(cell("description") or "").strip()
                entry["costs"].append([category, (description or label)[:200], str(amount)])

        for col, value in enumerate(row):          # numbers in columns we do not know: say so instead of dropping silently
            if col in recognised or _blank(value) or col >= len(header):
                continue
            title = str(header[col] if col < len(header) else "").strip()
            if title and not JUNK_HEADER.match(norm_header(title)):
                unknown_data[title] += 1

        kept += 1
        if len(entry) > 4 or entry["costs"]:           # a dated row with nothing to import is not an error
            result.rows.append(entry)

    for title, count in unknown_data.items():
        result.warn(f"Sheet '{sheet.name}': column '{title}' is not recognised and was ignored ({count} cells with data).")
    result.sheets.append(SheetInfo(sheet.name, "read", rows=kept, header_row=header_idx + 1))


def _legacy_category(text: str):
    key = norm_header(text)
    if key in COST_COLUMNS:
        return COST_COLUMNS[key]
    for value, label in ExpenseCategory.choices:
        if key in (norm_header(value), norm_header(label)):
            return value, label
    return ExpenseCategory.OTHER, "OTHER"


# ---------------------------------------------------------------------------------------------------------------
# 4. Rows -> plan (reads the database, writes nothing)
# ---------------------------------------------------------------------------------------------------------------

@dataclass
class MonthSummary:
    year: int
    month: int
    sheets: list = field(default_factory=list)
    rows: int = 0
    deposits_count: int = 0
    deposits_total: Decimal = ZERO
    expenses_count: int = 0
    expenses_total: Decimal = ZERO
    pay_count: int = 0
    pay_total: Decimal = ZERO
    duplicates: int = 0

    @property
    def label(self) -> str:
        return date(self.year, self.month, 1).strftime("%B %Y")

    @property
    def spending_total(self) -> Decimal:
        return self.expenses_total + self.pay_total


@dataclass
class _Expense:
    date: date
    category: str
    amount: Decimal
    driver_key: str | None
    note: str


@dataclass
class _Invoice:
    date: date
    amount: Decimal
    customer_key: str


@dataclass
class Plan:
    months: "OrderedDict" = field(default_factory=OrderedDict)
    expenses: list = field(default_factory=list)          # _Expense still to be created
    invoices: list = field(default_factory=list)          # _Invoice still to be created
    new_customers: "OrderedDict" = field(default_factory=OrderedDict)   # key -> display name
    new_drivers: "OrderedDict" = field(default_factory=OrderedDict)
    customers: dict = field(default_factory=dict)         # key -> existing Customer
    drivers: dict = field(default_factory=dict)           # key -> existing Driver
    duplicates: int = 0
    warnings: list = field(default_factory=list)
    rows_in_file: int = 0

    @property
    def total_new(self) -> int:
        return len(self.expenses) + len(self.invoices)

    @property
    def totals(self) -> dict:
        months = self.months.values()
        return {
            "deposits_count": sum(m.deposits_count for m in months), "deposits_total": sum((m.deposits_total for m in months), ZERO),
            "expenses_count": sum(m.expenses_count for m in months), "expenses_total": sum((m.expenses_total for m in months), ZERO),
            "pay_count": sum(m.pay_count for m in months), "pay_total": sum((m.pay_total for m in months), ZERO),
        }

    @property
    def period_label(self) -> str:
        keys = list(self.months)
        if not keys:
            return ""
        first, last = self.months[keys[0]], self.months[keys[-1]]
        return first.label if first is last else f"{first.label} to {last.label}"


def _month_range(rows):
    dates = [date.fromisoformat(r["date"]) for r in rows]
    first, last = min(dates), max(dates)
    return first.replace(day=1), last.replace(day=monthrange(last.year, last.month)[1])


def _similar_name_warnings(file_names: dict, known: dict, kind: str):
    """Names that are new but look like another name (typos like NORTHWIND / NORTHWNID): warn, never merge."""
    warnings, seen = [], set()
    candidates = {**known, **file_names}
    for key, shown in file_names.items():
        if key in known:
            continue
        for other_key, other_shown in candidates.items():
            if other_key == key or frozenset((key, other_key)) in seen:
                continue
            if SequenceMatcher(None, key, other_key).ratio() >= SIMILAR_NAME_RATIO:
                seen.add(frozenset((key, other_key)))
                warnings.append(f"{kind} '{shown}' will be created as a new record but looks like '{other_shown}': same one spelled differently?")
    return warnings


def build_plan(rows: list[dict]) -> Plan:
    plan = Plan(rows_in_file=len(rows))
    if not rows:
        return plan

    customers_by_key, drivers_by_key = {}, {}
    all_customers = list(Customer.objects.all())
    for c in all_customers:
        customers_by_key.setdefault(name_key(c.name), c)
    for c in all_customers:                                # abbreviations only fill gaps: a name always wins
        customers_by_key.setdefault(name_key(c.abbreviation), c)
    for d in Driver.objects.all():
        drivers_by_key.setdefault(name_key(d.name), d)
    plan.customers, plan.drivers = customers_by_key, drivers_by_key

    first_day, last_day = _month_range(rows)
    seen_customers, seen_drivers = OrderedDict(), OrderedDict()

    pending_expenses, pending_invoices = [], []
    for row in rows:
        when = date.fromisoformat(row["date"])
        month = plan.months.setdefault((when.year, when.month), MonthSummary(when.year, when.month))
        if row["sheet"] not in month.sheets:
            month.sheets.append(row["sheet"])
        month.rows += 1

        if "deposit" in row:
            shown = row.get("deposit_from") or UNASSIGNED_CUSTOMER
            key = name_key(shown)
            seen_customers.setdefault(key, display_name(shown))
            pending_invoices.append((_Invoice(when, Decimal(row["deposit"]), key), month))
        for category, label, amount in row["costs"]:
            pending_expenses.append((_Expense(when, category, Decimal(amount), None, f"{NOTE_PREFIX}: {label}"), month, False))
        if "pay" in row:
            shown = row.get("pay_to")
            key = name_key(shown) if shown else None
            if key:
                seen_drivers.setdefault(key, display_name(shown))
            pending_expenses.append((_Expense(when, ExpenseCategory.DRIVER, Decimal(row["pay"]), key, ""), month, True))

    # what already exists as an *imported* row, to make re-uploading the same file harmless
    existing_expenses = Counter(
        (d, cat, amt, (driver or "").casefold(), notes)
        for d, cat, amt, driver, notes in Expense.objects.filter(
            date__range=(first_day, last_day), notes__startswith=NOTE_PREFIX
        ).values_list("date", "category", "amount", "driver__name", "notes")
    )
    existing_invoices = Counter(
        Invoice.objects.filter(status=InvoiceStatus.PAID, invoice_date__range=(first_day, last_day), line_items__isnull=True)
        .values_list("invoice_date", "customer_id", "total_amount")
    )

    for exp, month, is_pay in pending_expenses:
        if is_pay:
            driver = drivers_by_key.get(exp.driver_key) if exp.driver_key else None
            shown = driver.name if driver else (seen_drivers.get(exp.driver_key) if exp.driver_key else None)
            exp.note = f"{NOTE_PREFIX}: Pay → {shown or 'Unknown'}"
            fingerprint_driver = (shown or "").casefold()
        else:
            fingerprint_driver = ""
        fingerprint = (exp.date, exp.category, exp.amount, fingerprint_driver, exp.note)
        if existing_expenses[fingerprint] > 0:
            existing_expenses[fingerprint] -= 1
            month.duplicates += 1
            plan.duplicates += 1
            continue
        plan.expenses.append(exp)
        if is_pay:
            month.pay_count += 1
            month.pay_total += exp.amount
        else:
            month.expenses_count += 1
            month.expenses_total += exp.amount

    for inv, month in pending_invoices:
        customer = customers_by_key.get(inv.customer_key)
        if customer is not None:
            fingerprint = (inv.date, customer.pk, inv.amount)
            if existing_invoices[fingerprint] > 0:
                existing_invoices[fingerprint] -= 1
                month.duplicates += 1
                plan.duplicates += 1
                continue
        plan.invoices.append(inv)
        month.deposits_count += 1
        month.deposits_total += inv.amount

    used_customers = {inv.customer_key for inv in plan.invoices}
    used_drivers = {exp.driver_key for exp in plan.expenses if exp.driver_key}
    for key, shown in seen_customers.items():
        if key in used_customers and key not in customers_by_key:
            plan.new_customers[key] = shown
    for key, shown in seen_drivers.items():
        if key in used_drivers and key not in drivers_by_key:
            plan.new_drivers[key] = shown

    plan.months = OrderedDict(sorted(plan.months.items()))
    known_customers = {k: c.name for k, c in customers_by_key.items()}
    known_drivers = {k: d.name for k, d in drivers_by_key.items()}
    plan.warnings += _similar_name_warnings(
        {k: v for k, v in seen_customers.items() if k != name_key(UNASSIGNED_CUSTOMER)}, known_customers, "Customer")
    plan.warnings += _similar_name_warnings(seen_drivers, known_drivers, "Driver")
    return plan


# ---------------------------------------------------------------------------------------------------------------
# 5. Plan -> database
# ---------------------------------------------------------------------------------------------------------------

@dataclass
class Result:
    expenses: int
    invoices: int
    customers: int
    drivers: int
    duplicates: int


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "customer"


def apply_plan(plan: Plan) -> Result:
    """Write everything or nothing.  Missing customers and drivers are created with placeholder contact details
    (flagged so they are easy to find and complete)."""
    with transaction.atomic():
        customers = dict(plan.customers)
        drivers = dict(plan.drivers)
        for key, name in plan.new_customers.items():
            customers[key] = Customer.objects.create(
                name=name[:100], abbreviation=name.upper()[:20], phone_number=PLACEHOLDER_PHONE, address="N/A",
                email=f"{_slug(name)}@import.local", notes="Created by the statement import: please complete the contact details.",
            )
        for key, name in plan.new_drivers.items():
            drivers[key] = Driver.objects.create(
                name=name[:100], phone_number=PLACEHOLDER_PHONE, license_number=f"IMP-{uuid.uuid4().hex[:12].upper()}",
            )
        Expense.objects.bulk_create(
            [Expense(date=e.date, category=e.category, amount=e.amount, driver=drivers.get(e.driver_key) if e.driver_key else None, notes=e.note)
             for e in plan.expenses],
            batch_size=500,
        )
        Invoice.objects.bulk_create(
            [Invoice(customer=customers[i.customer_key], invoice_date=i.date, status=InvoiceStatus.PAID, paid_at=i.date, total_amount=i.amount)
             for i in plan.invoices],
            batch_size=500,
        )
    return Result(len(plan.expenses), len(plan.invoices), len(plan.new_customers), len(plan.new_drivers), plan.duplicates)


def clear_imported(rows: list[dict]):
    """Delete what an earlier import created in the months covered by *rows* (never hand-entered data): expenses
    whose notes start with 'Imported' and paid invoices without line items.  Returns (expenses, invoices)."""
    if not rows:
        return 0, 0
    first_day, last_day = _month_range(rows)
    with transaction.atomic():
        expenses, _ = Expense.objects.filter(date__range=(first_day, last_day), notes__startswith=NOTE_PREFIX).delete()
        invoices, _ = Invoice.objects.filter(
            status=InvoiceStatus.PAID, invoice_date__range=(first_day, last_day), line_items__isnull=True
        ).delete()
    return expenses, invoices


def import_bytes(data: bytes, filename: str) -> tuple[ParseResult, Plan]:
    """Convenience for callers that want the whole read -> parse -> plan chain."""
    parsed = parse_sheets(read_sheets(data, filename))
    return parsed, build_plan(parsed.rows)
