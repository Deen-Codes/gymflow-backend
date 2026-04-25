from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("workouts", "0003_workoutplan_client_workoutplan_is_template_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="ExerciseCatalog",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(db_index=True, max_length=255)),
                ("muscle_group", models.CharField(blank=True, db_index=True, max_length=64)),
                ("equipment", models.CharField(blank=True, db_index=True, max_length=64)),
                ("instructions", models.TextField(blank=True)),
                ("video_url", models.URLField(blank=True)),
                ("image_url", models.URLField(blank=True)),
                (
                    "source",
                    models.CharField(
                        choices=[("curated", "Curated"), ("wger", "wger")],
                        default="curated",
                        max_length=16,
                    ),
                ),
                ("external_id", models.CharField(blank=True, db_index=True, max_length=64)),
                ("is_published", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["name"],
            },
        ),
        migrations.AddConstraint(
            model_name="exercisecatalog",
            constraint=models.UniqueConstraint(
                condition=models.Q(("external_id__gt", "")),
                fields=("source", "external_id"),
                name="unique_catalog_source_external_id",
            ),
        ),
        migrations.AddField(
            model_name="exerciselibraryitem",
            name="source_catalog_item",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="library_snapshots",
                to="workouts.exercisecatalog",
            ),
        ),
        migrations.AddField(
            model_name="exerciselibraryitem",
            name="muscle_group",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="exerciselibraryitem",
            name="equipment",
            field=models.CharField(blank=True, max_length=64),
        ),
    ]
