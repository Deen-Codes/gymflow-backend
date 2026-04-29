from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.utils import timezone
from .models import User, TrainerProfile, ClientProfile, Changelog, CoachingTip


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ("GymFlow", {"fields": ("role",)}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("GymFlow", {"fields": ("role",)}),
    )
    list_display = ("username", "email", "role", "is_staff", "is_superuser")


@admin.register(TrainerProfile)
class TrainerProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "business_name", "slug", "city", "country")
    list_filter = ("city", "country")
    search_fields = ("user__username", "business_name", "city")
    fieldsets = (
        (None, {"fields": ("user", "business_name", "slug")}),
        ("Location (powers /cities/<slug>/ directory)", {
            "fields": ("city", "country"),
        }),
        ("Stripe Connect", {"fields": ("stripe_user_id",)}),
    )


@admin.register(ClientProfile)
class ClientProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "trainer")


# ----------------------------------------------------------------------
# Hub content (task #37) — admin-driven Changelog + Coaching Tips so
# we can post hub updates without redeploying. Both have a "Publish
# now" bulk action that flips `published=True` and stamps
# `published_at` to now in one click.
# ----------------------------------------------------------------------


@admin.action(description="Publish selected (set published=True + stamp published_at)")
def _publish_now(modeladmin, request, queryset):
    queryset.update(published=True, published_at=timezone.now())


@admin.action(description="Unpublish selected (hide from hub)")
def _unpublish(modeladmin, request, queryset):
    queryset.update(published=False)


@admin.register(Changelog)
class ChangelogAdmin(admin.ModelAdmin):
    list_display = ("title", "audience", "published", "published_at", "updated_at")
    list_filter = ("published", "audience")
    search_fields = ("title", "body")
    actions = [_publish_now, _unpublish]
    fieldsets = (
        (None, {"fields": ("title", "body")}),
        ("Audience + CTA", {"fields": ("audience", "cta_url", "cta_label")}),
        ("Publishing", {"fields": ("published", "published_at")}),
    )
    readonly_fields = ()


@admin.register(CoachingTip)
class CoachingTipAdmin(admin.ModelAdmin):
    list_display = ("icon", "title", "category", "published", "published_at", "updated_at")
    list_filter = ("published", "category")
    search_fields = ("title", "body")
    actions = [_publish_now, _unpublish]
    fieldsets = (
        (None, {"fields": ("icon", "title", "body")}),
        ("Categorisation", {"fields": ("category",)}),
        ("Publishing", {"fields": ("published", "published_at")}),
    )
