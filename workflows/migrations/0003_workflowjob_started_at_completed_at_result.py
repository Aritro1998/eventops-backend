from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workflows", "0002_workflowjob_is_email_sent"),
    ]

    operations = [
        migrations.AddField(
            model_name="workflowjob",
            name="started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="workflowjob",
            name="completed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="workflowjob",
            name="result",
            field=models.JSONField(blank=True, null=True),
        ),
    ]
