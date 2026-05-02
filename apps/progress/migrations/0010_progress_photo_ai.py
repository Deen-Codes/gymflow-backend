# PHOTO-COACHING (#106) — Claude Vision commentary fields on
# ProgressPhoto. ai_commentary stores the calmly-worded analysis;
# ai_analyzed_at lets us tell the iOS surface "you have a fresh
# read since the photo was taken" vs "still pending". Both nullable
# so older rows + opt-out cases stay valid.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("progress", "0009_progress_photos"),
    ]

    operations = [
        migrations.AddField(
            model_name="progressphoto",
            name="ai_commentary",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="progressphoto",
            name="ai_analyzed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
