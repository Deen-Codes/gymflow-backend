"""Phase 35 — index CheckInQuestion.field_key.

The dashboard's progress charts filter answers by question.field_key
(e.g. WEIGHT_FIELD_KEYS = ("current_weight", "daily_weight",
"weekly_weight")) on every render. Without an index this scans every
question row in the trainer's account.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("progress", "0004_routine_form_type_and_assignment"),
    ]

    operations = [
        migrations.AlterField(
            model_name="checkinquestion",
            name="field_key",
            field=models.CharField(blank=True, db_index=True, max_length=100),
        ),
    ]
