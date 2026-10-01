from django.db import migrations, models


def blanks_to_null(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    User.objects.filter(phone_number='').update(phone_number=None)


def null_to_blanks(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    User.objects.filter(phone_number__isnull=True).update(phone_number='')


class Migration(migrations.Migration):

    dependencies = [('accounts', '0005_order_pop_submitted_at_order_proof_of_payment')]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='phone_number',
            field=models.CharField(max_length=20, unique=True, null=True, blank=True),
        ),
        migrations.RunPython(blanks_to_null, null_to_blanks),
    ]
