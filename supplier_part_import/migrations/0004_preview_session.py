from django.db import migrations, models


class Migration(migrations.Migration):
    """Previews kept in the database instead of the cache (shared by every server process)."""

    dependencies = [
        ('supplier_part_import', '0003_pending_category'),
    ]

    operations = [
        migrations.CreateModel(
            name='PreviewSession',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('token', models.CharField(max_length=32, unique=True, verbose_name='Token')),
                ('created', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='Created')),
                ('data', models.BinaryField(verbose_name='Data')),
            ],
            options={
                'verbose_name': 'Preview Session',
                'verbose_name_plural': 'Preview Sessions',
            },
        ),
    ]
