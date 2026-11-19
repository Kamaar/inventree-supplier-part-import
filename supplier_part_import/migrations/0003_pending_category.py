from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    """Pending category mappings: the category is created on the first import."""

    dependencies = [
        ('part', '0001_initial'),
        ('supplier_part_import', '0002_function_filter'),
    ]

    operations = [
        migrations.AlterField(
            model_name='categorymapping',
            name='category',
            field=models.ForeignKey(
                blank=True,
                help_text='Empty for a pending mapping: the category is created on the first import',
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='+',
                to='part.partcategory',
                verbose_name='InvenTree category',
            ),
        ),
        # db_default: rows written by a process still running the previous version stay valid
        migrations.AddField(
            model_name='categorymapping',
            name='category_path',
            field=models.CharField(
                blank=True,
                db_default='',
                default='',
                help_text='Path of a category which does not exist yet (e.g. "RF/Shield")',
                max_length=250,
                verbose_name='Pending category',
            ),
        ),
    ]
