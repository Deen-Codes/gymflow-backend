"""Polish pass on trophy icons.

A couple of the SF Symbol names in the original seed don't render
cleanly on every iOS version (numbered circles >50 in particular were
added in newer SF Symbol releases). Swap them for icons that are
guaranteed to render clearly across iOS 17+.
"""
from django.db import migrations


ICON_UPDATES = {
    "hundred_workouts":         "trophy.fill",        # 100.circle.fill blank on some iOS
    "twofifty_workouts":        "trophy.fill",
    "fivehundred_workouts":     "trophy.circle.fill",
    "thousand_workouts":        "crown.fill",
    "triple_digit":             "scalemass.fill",     # 100.circle was rendering blank
    "double_triple":            "crown.fill",
    "hundred_reps_exercise":    "repeat.circle.fill",
    "twelve_in_month":          "12.square",
    "twenty_in_month":          "20.square",
    "thirty_in_month":          "30.square",
    "five_in_week":             "5.circle.fill",
    "three_in_week":            "3.circle.fill",
}


def apply_icons(apps, schema_editor):
    Trophy = apps.get_model("trophies", "Trophy")
    for code, icon in ICON_UPDATES.items():
        Trophy.objects.filter(code=code).update(icon=icon)


def noop_reverse(apps, schema_editor):
    # Icons are presentation-only; no need to roll back to the
    # previous values on `migrate <prev>`.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("trophies", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(apply_icons, reverse_code=noop_reverse),
    ]
