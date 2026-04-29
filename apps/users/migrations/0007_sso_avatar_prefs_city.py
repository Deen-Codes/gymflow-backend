# Hand-written migration covering every field added to apps.users.models
# since 0006:
#
#   • User.apple_sub        (L.1.1.1 / task #44 — Apple SSO)
#   • User.google_sub       (L.1.1.1 / task #44 — Google SSO)
#   • User.avatar_base64    (P.1.1 / task #30 — profile avatar)
#   • User.notification_prefs (P.1.1 / task #30 — notification toggles)
#   • TrainerProfile.city   (M.2 / task #42 — city directory pages)
#   • TrainerProfile.country (M.2 / task #42 — for future i18n filtering)
#
# All fields are nullable / blank-defaulted so the migration is safe to
# apply against the live DB without any data backfill.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0006_changelog_coachingtip_magiclogintoken"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="apple_sub",
            field=models.CharField(
                blank=True, db_index=True, max_length=255, null=True, unique=True,
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="google_sub",
            field=models.CharField(
                blank=True, db_index=True, max_length=255, null=True, unique=True,
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="avatar_base64",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="user",
            name="notification_prefs",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="trainerprofile",
            name="city",
            field=models.CharField(
                blank=True, db_index=True, default="", max_length=80,
            ),
        ),
        migrations.AddField(
            model_name="trainerprofile",
            name="country",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
    ]
