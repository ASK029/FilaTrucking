"""Rows created by the historical-statement importer for the PAY column were stored with category TRUCK although
they are driver pay (QA-037). Statements now group by category, so without this they would inflate the "Truck" column
and the driver-pay section would stay empty. Totals do not change, only which bucket the amounts are shown in.
Reversible."""
from django.db import migrations

PREFIX = "Imported: Pay"


def to_driver(apps, schema_editor):
    Expense = apps.get_model("shipments", "Expense")
    Expense.objects.filter(category="truck", notes__startswith=PREFIX).update(category="driver")


def to_truck(apps, schema_editor):
    Expense = apps.get_model("shipments", "Expense")
    Expense.objects.filter(category="driver", notes__startswith=PREFIX).update(category="truck")


class Migration(migrations.Migration):

    dependencies = [
        ("shipments", "0013_alter_expense_receipt"),
    ]

    operations = [
        migrations.RunPython(to_driver, to_truck),
    ]
