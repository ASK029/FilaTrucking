"""Import monthly statements from the command line: the same reader/planner the Import Data page uses (QA-050)."""
from django.core.management.base import BaseCommand, CommandError

from shipments import statement_import as si


class Command(BaseCommand):
    help = "Import a statement CSV or Excel workbook (deposits, expenses, driver pay), e.g. '2025 STATMENTS - JAN.csv'"

    def add_arguments(self, parser):
        parser.add_argument("file", type=str, help="Path to a .csv or .xlsx file (an .xlsx imports every month sheet)")
        parser.add_argument(
            "--clear",
            action="store_true",
            help="First delete what an earlier import created in the months covered by the file "
                 "(expenses noted 'Imported…' and paid invoices without line items; hand-entered data is kept)",
        )
        parser.add_argument("--dry-run", action="store_true", help="Show what would be imported and change nothing")

    def handle(self, *args, **options):
        path = options["file"]
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except FileNotFoundError:
            raise CommandError(f'File "{path}" not found')
        try:
            parsed = si.parse_sheets(si.read_sheets(data, path))
        except si.StatementImportError as exc:
            raise CommandError(str(exc))
        if not parsed.rows:
            raise CommandError("No importable rows found.")

        for sheet in parsed.sheets:
            if sheet.status == "skipped":
                self.stdout.write(f"  skipped sheet '{sheet.name}': {sheet.reason}")

        if options["clear"] and not options["dry_run"]:
            expenses, invoices = si.clear_imported(parsed.rows)
            self.stdout.write(f"  Cleared {expenses} imported expense rows, {invoices} imported invoices")

        plan = si.build_plan(parsed.rows)
        for month in plan.months.values():
            self.stdout.write(
                f"  {month.label:<16} deposits {month.deposits_count:>3} (${month.deposits_total:,.2f})  "
                f"expenses {month.expenses_count:>3} (${month.expenses_total:,.2f})  "
                f"driver pay {month.pay_count:>3} (${month.pay_total:,.2f})  skipped {month.duplicates}"
            )
        for name in plan.new_customers.values():
            self.stdout.write(f"  + new customer: {name}")
        for name in plan.new_drivers.values():
            self.stdout.write(f"  + new driver: {name}")
        for warning in parsed.warnings + plan.warnings:
            self.stderr.write(f"  warning: {warning}")

        if options["dry_run"]:
            self.stdout.write(self.style.NOTICE(f"Dry run: {len(plan.expenses)} expenses and {len(plan.invoices)} invoices would be created."))
            return
        result = si.apply_plan(plan)
        self.stdout.write(self.style.SUCCESS(
            f"Done: {result.expenses} expenses, {result.invoices} invoices created; {result.duplicates} already imported entries skipped."
        ))
