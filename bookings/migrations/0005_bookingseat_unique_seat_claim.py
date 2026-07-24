from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bookings", "0004_bookingseat_and_more"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="bookingseat",
            constraint=models.UniqueConstraint(
                fields=("seat",),
                name="unique_seat_claim",
            ),
        ),
    ]
