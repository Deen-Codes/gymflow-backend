from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nutrition", "0003_foodlibraryitem_metadata"),
    ]

    operations = [
        migrations.AddField(
            model_name="nutritionplan",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True, null=True),
        ),
    ]
