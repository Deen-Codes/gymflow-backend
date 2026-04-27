"""User.date_of_birth + ClientProfile.goal_weight_kg.

Both nullable — most existing rows won't have these set, and we never
want to require them. Powers the "Birthday Workout" and "Goal Weight
Reached" trophies, plus future birthday-aware UX.
"""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0004_trainerprofile_stripe_user_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="date_of_birth",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="clientprofile",
            name="goal_weight_kg",
            field=models.DecimalField(
                blank=True, decimal_places=1, max_digits=5, null=True,
            ),
        ),
    ]
