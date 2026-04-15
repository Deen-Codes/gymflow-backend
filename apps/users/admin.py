from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User, TrainerProfile, ClientProfile


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
    list_display = ("user", "business_name", "slug")


@admin.register(ClientProfile)
class ClientProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "trainer")
