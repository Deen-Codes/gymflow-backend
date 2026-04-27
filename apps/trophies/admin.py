from django.contrib import admin

from .models import ClientTrophyAward, Trophy


@admin.register(Trophy)
class TrophyAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "category", "rarity", "icon", "sort_order")
    list_filter = ("category", "rarity")
    search_fields = ("name", "code", "description")
    ordering = ("category", "sort_order", "id")


@admin.register(ClientTrophyAward)
class ClientTrophyAwardAdmin(admin.ModelAdmin):
    list_display = ("user", "trophy", "earned_at")
    list_filter = ("trophy__category", "trophy__rarity")
    search_fields = ("user__username", "trophy__name", "trophy__code")
    autocomplete_fields = ("user", "trophy")
    date_hierarchy = "earned_at"
    readonly_fields = ("earned_at",)
