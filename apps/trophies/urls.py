from django.urls import path

from .mobile_views import trophies_for_me

urlpatterns = [
    path("me/", trophies_for_me, name="trophies-me"),
]
