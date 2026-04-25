"""URL routing for the public PT landing pages.

Mounted at /p/<slug>/ in the project urls so we keep a clean
namespace until subdomain routing lands in Phase 7.5."""
from django.urls import path

from . import views as v

urlpatterns = [
    path("<slug:slug>/",         v.public_site_page,   name="public-site-page"),
    path("<slug:slug>/signup/",  v.public_site_signup, name="public-site-signup"),
]
