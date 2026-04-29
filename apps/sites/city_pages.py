"""
M.2 — Programmatic city directory pages.

The URL surface:
    /cities/                  — index page listing every city with at
                                least one published trainer
    /cities/<city-slug>/      — leaf page listing trainers in that city

These pages are pure SEO plays: low-content, server-rendered, fully
crawlable. They exist so a search like "personal trainer london"
lands on a relevant GymFlow page rather than a competitor.

Design notes:
    • The slug is derived from the trainer-typed `city` string at read
      time (slugify) — no second column to keep in sync.
    • A page only exists if at least one *published* trainer claims that
      city. Empty cities never appear in the sitemap.
    • We intentionally do not paginate yet. When a city has more than
      ~50 trainers, swap to a paginated view. Today every city has 0–1.
"""
from collections import defaultdict
from typing import Iterable

from django.utils.text import slugify

from apps.users.models import TrainerProfile

from .models import TrainerSite


# --------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------
def _published_trainers_qs():
    """Trainers whose site is published AND have a non-empty city."""
    return (
        TrainerProfile.objects
        .filter(site__is_published=True)
        .exclude(city="")
        .select_related("user", "site")
    )


def published_city_slugs() -> list[str]:
    """All distinct city slugs that have at least one published trainer.
    Used by the sitemap. Sorted for deterministic output."""
    seen: set[str] = set()
    for tp in _published_trainers_qs().only("city"):
        s = slugify(tp.city)
        if s:
            seen.add(s)
    return sorted(seen)


def cities_with_counts() -> list[dict]:
    """For the index page. Returns [{slug, name, count}, …] sorted by
    name. `name` is the most-recently-typed casing of the city (first
    one we see in the queryset is fine — they're nearly always the
    same)."""
    buckets: dict[str, dict] = {}
    for tp in _published_trainers_qs().only("city"):
        s = slugify(tp.city)
        if not s:
            continue
        if s not in buckets:
            buckets[s] = {"slug": s, "name": tp.city.strip(), "count": 0}
        buckets[s]["count"] += 1
    return sorted(buckets.values(), key=lambda b: b["name"].lower())


def trainers_in_city(city_slug: str) -> list[TrainerProfile]:
    """All published trainers whose slugified city matches `city_slug`.
    Slugify-on-read means the trainer can type "London" or "london"
    or "LONDON" and they all collide — desirable behaviour."""
    matched = []
    for tp in _published_trainers_qs():
        if slugify(tp.city) == city_slug:
            matched.append(tp)
    return matched


def display_name_for_slug(city_slug: str) -> str:
    """Best-effort de-slug for the page heading. We use the casing the
    first matching trainer typed; if no trainer matches yet, fall back
    to a Title Case form of the slug."""
    for tp in _published_trainers_qs().only("city"):
        if slugify(tp.city) == city_slug:
            return tp.city.strip()
    return city_slug.replace("-", " ").title()
