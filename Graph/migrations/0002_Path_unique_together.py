# Generated by Django 2.2.1 on 2019-08-19 17:47

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Graph', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='path',
            name='accession',
            field=models.CharField(max_length=1000),
        ),
        migrations.AlterUniqueTogether(
            name='path',
            unique_together={('graph', 'accession')},
        ),
    ]
