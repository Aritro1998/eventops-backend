from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0005_event_is_archived'),
    ]

    operations = [
        migrations.RunSQL("CREATE EXTENSION IF NOT EXISTS pg_trgm;"),
    ]