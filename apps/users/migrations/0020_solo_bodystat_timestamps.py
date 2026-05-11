"""HK-AUTOSYNC-TIMESTAMPS — per-field timestamps on SoloProfile so
the Apple Health smart sync can do proper recency-based source-of-
truth resolution instead of guessing."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0019_setup_progress_flags"),
    ]

    operations = [
        migrations.AddField(
            model_name="soloprofile",
            name="bodyweight_updated_at",
            field=models.DateTimeField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name="soloprofile",
            name="height_updated_at",
            field=models.DateTimeField(null=True, blank=True),
        ),
    ]
