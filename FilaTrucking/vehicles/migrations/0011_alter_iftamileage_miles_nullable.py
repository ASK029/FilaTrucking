from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0010_alter_iftamileage_vehicle_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='iftamileage',
            name='miles',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True, verbose_name='Miles Driven'),
        ),
    ]