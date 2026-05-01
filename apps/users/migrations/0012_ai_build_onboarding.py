# AI-BUILD-ONBOARDING — capture training_days + session_minutes +
# avoidances during the cinematic AI workout build onboarding so
# the AI has the context it needs to schedule + tailor.
#
# All three fields default to "no answer yet" sentinels (empty
# list, zero, empty list) so existing users aren't broken — the
# AI build flow only prompts for fields that are still empty.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0011_phase_a_mutations"),
    ]

    operations = [
        migrations.AddField(
            model_name="soloprofile",
            name="training_days",
            field=models.JSONField(default=list, blank=True),
        ),
        migrations.AddField(
            model_name="soloprofile",
            name="session_minutes",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="soloprofile",
            name="avoidances",
            field=models.JSONField(default=list, blank=True),
        ),
    ]
