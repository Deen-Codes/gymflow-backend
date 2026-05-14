from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.utils import timezone
from .models import User, TrainerProfile, ClientProfile, Changelog, CoachingTip, BugReport


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


# REPORT-A-BUG (May 2026) — triage surface. List view shows the most
# recent first with status + a short body preview; detail page lets
# Deen flip status and read the full submission.
@admin.register(BugReport)
class BugReportAdmin(admin.ModelAdmin):
    list_display    = ("id", "_short", "user", "app_version", "status", "created_at")
    list_filter     = ("status", "app_version")
    search_fields   = ("what_happened", "expected", "user__username", "user__email")
    readonly_fields = (
        "user", "what_happened", "expected",
        "app_version", "app_build", "os_version", "device_model",
        "recent_actions", "screenshot_base64",
        "created_at",
    )
    fieldsets = (
        ("Submission", {
            "fields": ("user", "created_at", "status"),
        }),
        ("Report", {
            "fields": ("what_happened", "expected", "recent_actions"),
        }),
        ("Environment", {
            "fields": ("app_version", "app_build", "os_version", "device_model"),
        }),
        ("Screenshot", {
            "classes": ("collapse",),
            "fields": ("screenshot_base64",),
            "description": "Base64-encoded — copy/paste into a viewer to inspect.",
        }),
    )

    def _short(self, obj):
        return (obj.what_happened or "")[:60]
    _short.short_description = "Summary"
