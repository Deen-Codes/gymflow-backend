from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0003_clientprofile_assigned_nutrition_plan"),
    ]

    operations = [
        migrations.AddField(
            model_name="trainerprofile",
            name="stripe_user_id",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
    ]
