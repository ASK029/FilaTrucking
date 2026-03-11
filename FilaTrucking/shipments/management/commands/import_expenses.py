import csv
from datetime import datetime
from decimal import Decimal
from django.core.management.base import BaseCommand
from shipments.models import Expense, ExpenseCategory

class Command(BaseCommand):
    help = 'Import expenses from CSV file'

    def add_arguments(self, parser):
        parser.add_argument('csv_file', type=str, help='Path to the CSV file')

    def handle(self, *args, **options):
        csv_file_path = options['csv_file']
        
        # Mapping logic from PRD
        mapping = {
            'IRP': ExpenseCategory.OTHER,
            'Parking': ExpenseCategory.OTHER,
            'On Site': ExpenseCategory.OTHER,
            'Truck (purchase)': ExpenseCategory.OTHER,
            'Check Charge': ExpenseCategory.OTHER,
            'Insurance': ExpenseCategory.INSURANCE,
            'Toll': ExpenseCategory.TOLLS,
            'Fuel': ExpenseCategory.FUEL,
            'Chassis': ExpenseCategory.OTHER,
            'Pay': ExpenseCategory.DRIVER_PAY,
            'Pay To': ExpenseCategory.DRIVER_PAY,
        }

        try:
            with open(csv_file_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                count = 0
                for row in reader:
                    # Logic to parse Date and Amount
                    # This depends on the CSV structure. 
                    # Assuming basic columns: Date, Description/Category, Amount
                    
                    # For 2025_STATMENTS_-_JAN.csv (hypothetically)
                    date_str = row.get('Date')
                    if not date_str:
                        continue
                        
                    try:
                        date = datetime.strptime(date_str, '%m/%d/%Y').date()
                    except ValueError:
                        try:
                            date = datetime.strptime(date_str, '%Y-%m-%d').date()
                        except ValueError:
                            self.stderr.write(f"Could not parse date: {date_str}")
                            continue

                    for col, category in mapping.items():
                        amount_str = row.get(col)
                        if amount_str and amount_str.strip():
                            try:
                                # Clean amount string (remove $, commas)
                                clean_amount = amount_str.replace('$', '').replace(',', '').strip()
                                amount = Decimal(clean_amount)
                                if amount == 0:
                                    continue
                                    
                                Expense.objects.create(
                                    date=date,
                                    category=category,
                                    amount=amount,
                                    notes=f"Imported from {col}"
                                )
                                count += 1
                            except Exception as e:
                                self.stderr.write(f"Error parsing amount for {col}: {amount_str} - {str(e)}")

                self.stdout.write(self.style.SUCCESS(f'Successfully imported {count} expenses'))

        except FileNotFoundError:
            self.stderr.write(self.style.ERROR(f'File "{csv_file_path}" not found'))
        except Exception as e:
            self.stderr.write(self.style.ERROR(f'An error occurred: {str(e)}'))
