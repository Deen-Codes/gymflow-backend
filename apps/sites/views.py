"""
Phase 7 — PT site views.

Three surfaces:
  1. Editor (server-rendered)         — /dashboard/site/
  2. Editor JSON API (mutations)      — /api/sites/dashboard/...
  3. Public landing + signup          — /p/<slug>/  +  /p/<slug>/signup/

Auth model:
  • Editor + API: trainer (session)
  • Public site: anonymous (no auth required to view or signup)
"""
import secrets

from django.contrib import messages
from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST, require_http_methods

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.models import User, ClientProfile, TrainerProfile
from apps.users.dashboard_helpers import trainer_required_view, dashboard_context
from apps.progress.models import (
    CheckInForm,
    CheckInSubmission,
    CheckInAnswer,
    CheckInQuestion,
)

from .bootstrap import ensure_site, slug_from_email, _default_content
from .models import TrainerSite, SiteSection, PublicSignup, PricingPlan


# -------------------------------------------------------------------
# Editor (server-rendered)
# -------------------------------------------------------------------
@trainer_required_view
def trainer_site_page(request):
    """Site editor — renders the 3-column workspace.

    Outline list of sections on the left, live preview in the centre,
    properties panel on the right (shows the selected section's fields,
    or a "site overview" panel when nothing is selected)."""
    site = ensure_site(request.user.trainer_profile)
    sections = list(site.sections.all().order_by("order"))

    onboarding_form = CheckInForm.objects.filter(
        user=request.user, form_type=CheckInForm.ONBOARDING
    ).prefetch_related("questions__options").first()

    public_url = f"/p/{request.user.trainer_profile.slug}/" if request.user.trainer_profile.slug else ""

    pricing_plans = list(
        PricingPlan.objects
        .filter(trainer=request.user.trainer_profile, is_active=True)
        .order_by("sort_order", "id")
    )

    context = dashboard_context(request, "Site")
    context.update({
        "site": site,
        "site_sections": sections,
        "onboarding_form": onboarding_form,
        "section_type_labels": dict(SiteSection.SECTION_TYPE_CHOICES),
        "public_url": public_url,
        "pricing_plans": pricing_plans,
    })
    return render(request, "dashboard/dashboard_site.html", context)


# -------------------------------------------------------------------
# Editor JSON API — mutations only (initial data is server-rendered)
# -------------------------------------------------------------------
def _trainer_owns_section(trainer, section):
    return section.site.trainer_id == trainer.trainer_profile.id


def _trainer_owns_site(trainer, site):
    return site.trainer_id == trainer.trainer_profile.id


@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def site_section_update(request, section_id):
    """PATCH /api/sites/dashboard/sections/<id>/

    Body: {content?, is_visible?}
    """
    section = get_object_or_404(SiteSection, pk=section_id)
    if not _trainer_owns_section(request.user, section):
        return Response({"detail": "Not your site."}, status=status.HTTP_403_FORBIDDEN)

    if "content" in request.data:
        new_content = request.data["content"]
        if not isinstance(new_content, dict):
            return Response({"detail": "content must be a JSON object."}, status=400)
        section.content = new_content
    if "is_visible" in request.data:
        section.is_visible = bool(request.data["is_visible"])
    section.save()

    return Response({"ok": True, "section_id": section.id})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def site_section_create(request):
    """POST /api/sites/dashboard/sections/

    Body: {section_type, position?}
    Creates a new section of the given type, appended to the bottom by
    default. `position` (int, 0-based) inserts at that index instead.
    Returns the new section_id so the JS can update the outline + reload.
    """
    section_type = (request.data.get("section_type") or "").strip()
    valid_types = {t for t, _ in SiteSection.SECTION_TYPE_CHOICES}
    if section_type not in valid_types:
        return Response({"detail": f"Unknown section_type: {section_type}"}, status=400)

    site = ensure_site(request.user.trainer_profile)
    profile = request.user.trainer_profile

    with transaction.atomic():
        siblings = list(site.sections.order_by("order"))
        position = request.data.get("position")
        if position is None or not isinstance(position, int):
            position = len(siblings)
        position = max(0, min(position, len(siblings)))

        # Shift siblings down to make room.
        for i, sib in enumerate(siblings):
            target_order = i if i < position else i + 1
            if sib.order != target_order:
                sib.order = target_order
                sib.save(update_fields=["order"])

        new_section = SiteSection.objects.create(
            site=site,
            section_type=section_type,
            order=position,
            is_visible=True,
            is_required=False,  # added-via-library sections are never mandatory
            content=_default_content(section_type, profile),
        )

    return Response({
        "ok": True,
        "section_id": new_section.id,
    }, status=status.HTTP_201_CREATED)


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def site_section_delete(request, section_id):
    """DELETE /api/sites/dashboard/sections/<id>/delete/

    Mandatory sections (onboarding, footer) refuse with 403 — they can
    only be hidden via the eye icon.
    """
    section = get_object_or_404(SiteSection, pk=section_id)
    if not _trainer_owns_section(request.user, section):
        return Response({"detail": "Not your site."}, status=403)
    if section.is_required:
        return Response({"detail": "Mandatory section can't be deleted."}, status=403)

    site = section.site
    with transaction.atomic():
        section.delete()
        # Compact order so future inserts stay sane.
        for index, remaining in enumerate(site.sections.order_by("order")):
            if remaining.order != index:
                remaining.order = index
                remaining.save(update_fields=["order"])

    return Response(status=204)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def site_sections_reorder(request):
    """POST /api/sites/dashboard/sections/reorder/

    Body: {ordered_section_ids: [...]}
    """
    ids = request.data.get("ordered_section_ids") or []
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        return Response({"detail": "ordered_section_ids must be a list of ints."}, status=400)

    sections = list(SiteSection.objects.filter(pk__in=ids))
    if not sections:
        return Response({"detail": "No sections found."}, status=404)

    site_ids = {s.site_id for s in sections}
    if len(site_ids) != 1:
        return Response({"detail": "All sections must belong to one site."}, status=400)
    site = sections[0].site
    if not _trainer_owns_site(request.user, site):
        return Response({"detail": "Not your site."}, status=403)

    with transaction.atomic():
        for index, sid in enumerate(ids):
            SiteSection.objects.filter(pk=sid, site=site).update(order=index)

    return Response({"ok": True})


@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def site_update(request):
    """PATCH /api/sites/dashboard/site/

    Body: {is_published?, brand_color?, city?, country?}

    `city` + `country` actually live on TrainerProfile (not TrainerSite)
    because the /cities/<slug>/ directory page reads them via
    TrainerProfile — kept here as a single endpoint so the editor only
    has one save path.
    """
    site = ensure_site(request.user.trainer_profile)
    trainer_profile = request.user.trainer_profile
    site_dirty = False
    profile_dirty = False

    if "is_published" in request.data:
        site.is_published = bool(request.data["is_published"])
        site_dirty = True
    if "brand_color" in request.data:
        color = (request.data["brand_color"] or "").strip()
        if color and not (color.startswith("#") and len(color) in (4, 7)):
            return Response({"detail": "brand_color must be a hex like #c8ff00."}, status=400)
        site.brand_color = color
        site_dirty = True

    if "city" in request.data:
        city = (request.data.get("city") or "").strip()[:80]
        trainer_profile.city = city
        profile_dirty = True
    if "country" in request.data:
        country = (request.data.get("country") or "").strip()[:80]
        trainer_profile.country = country
        profile_dirty = True

    if site_dirty:
        site.save()
    if profile_dirty:
        trainer_profile.save(update_fields=["city", "country"])
    return Response({"ok": True})


# -------------------------------------------------------------------
# Public landing page
# -------------------------------------------------------------------
def public_site_page(request, slug):
    """GET /p/<slug>/ — public landing page for a published trainer.
    Returns 404 if the trainer's site is not published."""
    trainer_profile = get_object_or_404(TrainerProfile, slug=slug)
    site = ensure_site(trainer_profile)
    if not site.is_published:
        raise Http404("Site not published.")

    sections = list(site.sections.filter(is_visible=True).order_by("order"))
    onboarding_form = CheckInForm.objects.filter(
        user=trainer_profile.user, form_type=CheckInForm.ONBOARDING
    ).prefetch_related("questions__options").first()

    pricing_plans = list(
        PricingPlan.objects
        .filter(trainer=trainer_profile, is_active=True)
        .order_by("sort_order", "id")
    )

    # If the prospect clicked a Subscribe button, the chosen plan ID
    # comes through as ?plan=<id> — pre-select it so the trainer sees
    # which tier their new client picked.
    selected_plan = None
    plan_q = request.GET.get("plan")
    if plan_q:
        for p in pricing_plans:
            if str(p.id) == plan_q:
                selected_plan = p
                break

    return render(request, "public/trainer_site.html", {
        "trainer": trainer_profile,
        "site": site,
        "sections": sections,
        "onboarding_form": onboarding_form,
        "brand_color": site.brand_color or "#c8ff00",
        "pricing_plans": pricing_plans,
        "selected_plan": selected_plan,
        "seo": _seo_context(request, trainer_profile, site, pricing_plans),
    })


# -------------------------------------------------------------------
# SEO helpers (task #43 / M.3)
#
# Each public trainer page gets bespoke meta tags + schema.org
# markup. Cheap to add, compounds with every new trainer — Google
# rewards original local-business content and a trainer's bio is
# exactly that.
# -------------------------------------------------------------------


def _seo_context(request, trainer, site, pricing_plans):
    """Build a SEO dict the template injects into <head>."""
    name = trainer.business_name or trainer.user.first_name or trainer.user.username
    bio = (site.bio or "").strip() if hasattr(site, "bio") and site.bio else ""

    title = f"Train with {name} — Personal Trainer on GymFlow"
    # 155 chars caps the meta description for clean Google previews.
    description = (
        bio if bio
        else f"Online coaching with {name}. Programmes, nutrition, check-ins and progress tracking — all in one app."
    )
    if len(description) > 155:
        description = description[:152].rstrip() + "…"

    page_url = request.build_absolute_uri()
    og_image = request.build_absolute_uri(
        f"/p/{trainer.slug}/og.png"
    )

    # JSON-LD — LocalBusiness + Person + Offer for the cheapest tier.
    cheapest_plan = pricing_plans[0] if pricing_plans else None
    schema = {
        "@context": "https://schema.org",
        "@type": "LocalBusiness",
        "name": name,
        "description": description,
        "url": page_url,
        "image": og_image,
        "founder": {
            "@type": "Person",
            "name": trainer.user.get_full_name() or name,
        },
    }
    # M.2 — feed Google's local-business panel an address. Even with
    # only `addressLocality` (city) populated, Google can attach the
    # business to "near me" and "<city>" queries.
    if getattr(trainer, "city", "") or getattr(trainer, "country", ""):
        schema["address"] = {
            "@type": "PostalAddress",
            "addressLocality": trainer.city or "",
            "addressCountry": trainer.country or "",
        }
        if trainer.city:
            schema["areaServed"] = trainer.city
    if cheapest_plan:
        schema["makesOffer"] = {
            "@type": "Offer",
            "name": cheapest_plan.name,
            "price": str(cheapest_plan.price_pence / 100) if hasattr(cheapest_plan, "price_pence") else None,
            "priceCurrency": getattr(cheapest_plan, "currency", "GBP"),
        }

    import json
    return {
        "title": title,
        "description": description,
        "page_url": page_url,
        "og_image": og_image,
        "schema_json": json.dumps(schema, indent=None),
    }


def public_site_og_image(request, slug):
    """GET /p/<slug>/og.png — auto-generated 1200x630 social card.

    Pure-Pillow render: brand-colour background, trainer's first
    initial in a centred lime disc + their full name underneath
    + "GymFlow" wordmark. Works without any uploaded assets so
    every trainer gets a sharable preview the moment their site
    publishes.
    """
    from io import BytesIO
    from PIL import Image, ImageDraw, ImageFont
    from django.http import HttpResponse

    trainer = get_object_or_404(TrainerProfile, slug=slug)
    site = ensure_site(trainer)
    name = trainer.business_name or trainer.user.first_name or trainer.user.username
    initial = name[:1].upper()

    W, H = 1200, 630
    bg = (4, 7, 17)         # GymFlow deepest dark
    accent = (200, 255, 32) # lime
    img = Image.new("RGB", (W, H), bg)
    draw = ImageDraw.Draw(img)

    # Lime disc with initial
    disc_r = 130
    cx, cy = W // 2, H // 2 - 60
    draw.ellipse(
        (cx - disc_r, cy - disc_r, cx + disc_r, cy + disc_r),
        fill=accent,
    )
    # Try the system-default font — Pillow ships one. Fonts are
    # finicky on Render's slim image, so a fallback chain.
    def _load_font(size):
        for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",):
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                pass
        return ImageFont.load_default()

    font_initial = _load_font(140)
    font_name = _load_font(64)
    font_brand = _load_font(28)

    # Centre the initial in the disc
    bbox = draw.textbbox((0, 0), initial, font=font_initial)
    iw, ih = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text((cx - iw // 2, cy - ih // 2 - bbox[1]), initial, fill=bg, font=font_initial)

    # Trainer name below disc
    bbox = draw.textbbox((0, 0), name, font=font_name)
    nw = bbox[2] - bbox[0]
    draw.text((W // 2 - nw // 2, cy + disc_r + 28), name, fill=(255, 255, 255), font=font_name)

    # "GYMFLOW" wordmark, faint, bottom-right
    brand = "GYMFLOW"
    bbox = draw.textbbox((0, 0), brand, font=font_brand)
    bw = bbox[2] - bbox[0]
    draw.text((W - bw - 36, H - 50), brand, fill=accent, font=font_brand)

    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    response = HttpResponse(buf.getvalue(), content_type="image/png")
    # Cache 24h; the OG image only changes if the trainer renames.
    response["Cache-Control"] = "public, max-age=86400"
    return response


def public_sitemap(request):
    """GET /sitemap.xml — lists every published trainer's public
    page plus all city directory pages (M.2). Re-generated on each
    request (cheap) so newly published trainers appear immediately."""
    from django.http import HttpResponse

    base = request.build_absolute_uri("/").rstrip("/")
    urls = [base + "/", base + "/legal/privacy/", base + "/legal/terms/"]

    # Per-trainer landing pages
    for site in TrainerSite.objects.filter(is_published=True).select_related("trainer"):
        urls.append(f"{base}/p/{site.trainer.slug}/")

    # M.2 — city directory pages. One URL per distinct city that has
    # at least one published trainer. We expose the index too so
    # crawlers find every leaf page.
    from .city_pages import published_city_slugs
    if published_city_slugs():
        urls.append(f"{base}/cities/")
        for city_slug in published_city_slugs():
            urls.append(f"{base}/cities/{city_slug}/")

    body = ['<?xml version="1.0" encoding="UTF-8"?>']
    body.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
    for url in urls:
        body.append(f"  <url><loc>{url}</loc></url>")
    body.append("</urlset>")
    return HttpResponse("\n".join(body), content_type="application/xml")


# -------------------------------------------------------------------
# M.2 — Programmatic city directory pages.
#
# Two routes mounted at the top of the project:
#     /cities/                    — index of every city w/ ≥1 trainer
#     /cities/<city-slug>/        — leaf page listing that city's PTs
#
# Pure SEO play. We list the trainers, link out to their pages, and
# ship the page with a tight title + meta-description so Google can
# rank it for "personal trainer <city>" queries. Sitemap auto-includes
# every leaf URL.
# -------------------------------------------------------------------


def cities_index(request):
    """GET /cities/ — list of every city with at least one published
    trainer. Hidden from the nav; lives only as a discovery aid for
    crawlers + an internal-link target from the leaf city pages."""
    from .city_pages import cities_with_counts

    cities = cities_with_counts()
    base = request.build_absolute_uri("/").rstrip("/")
    seo = {
        "title": "Find a Personal Trainer by City — GymFlow",
        "description": (
            "Browse personal trainers and online coaches by city. "
            "GymFlow is the all-in-one platform trainers run their "
            "business on — programmes, nutrition, check-ins."
        ),
        "page_url": f"{base}/cities/",
    }
    return render(request, "public/cities_index.html", {
        "cities": cities,
        "seo": seo,
    })


def city_directory_page(request, city_slug):
    """GET /cities/<slug>/ — list every published trainer based in
    `city_slug`. 404 if no trainer claims that city."""
    from .city_pages import (
        trainers_in_city,
        display_name_for_slug,
        cities_with_counts,
    )

    trainers = trainers_in_city(city_slug)
    if not trainers:
        raise Http404("No trainers found for that city.")

    city_name = display_name_for_slug(city_slug)
    base = request.build_absolute_uri("/").rstrip("/")

    # Cards are pre-decorated so the template stays dumb.
    cards = []
    for tp in trainers:
        site = getattr(tp, "site", None)
        cards.append({
            "slug": tp.slug,
            "display_name": (
                tp.business_name
                or tp.user.first_name
                or tp.user.username
            ),
            "url": f"/p/{tp.slug}/",
            "brand_color": (site.brand_color if site and site.brand_color else "#c8ff00"),
        })

    # Internal links to neighbouring cities help Google understand the
    # set; we cap at 8 so the footer doesn't get noisy.
    other_cities = [
        c for c in cities_with_counts()
        if c["slug"] != city_slug
    ][:8]

    seo = {
        "title": f"Personal Trainers in {city_name} — GymFlow",
        "description": (
            f"{len(trainers)} personal trainer"
            f"{'s' if len(trainers) != 1 else ''} based in "
            f"{city_name}, all using GymFlow to deliver coaching, "
            "nutrition and check-ins."
        )[:155],
        "page_url": f"{base}/cities/{city_slug}/",
    }
    return render(request, "public/city_directory.html", {
        "city_name": city_name,
        "city_slug": city_slug,
        "cards": cards,
        "other_cities": other_cities,
        "seo": seo,
    })


def public_manage_subscription(request, slug):
    """GET /p/<slug>/manage/ — public form where a client can request
    a Stripe Customer Portal magic link to be emailed to them.

    Phase 7.7.4 — the actual POST submit goes to
    /p/<slug>/manage/send/ (handled by apps.payments.portal_views),
    which generates the portal session + sends the email.

    This view just renders the form. Always available regardless of
    whether the site is published — clients of an unpublished site
    still need to manage their subscription.
    """
    trainer_profile = get_object_or_404(TrainerProfile, slug=slug)
    site = ensure_site(trainer_profile)
    return render(request, "public/manage_subscription.html", {
        "trainer": trainer_profile,
        "brand_color": site.brand_color or "#c8ff00",
    })


@require_POST
@csrf_protect
def public_site_signup(request, slug):
    """POST /p/<slug>/signup/ — create a client account from the
    public onboarding form. Submits the answers as a CheckInSubmission
    against the trainer's onboarding form so they appear in the
    trainer's check-in feed."""
    trainer_profile = get_object_or_404(TrainerProfile, slug=slug)
    site = ensure_site(trainer_profile)
    if not site.is_published:
        raise Http404("Site not published.")

    onboarding_form = CheckInForm.objects.filter(
        user=trainer_profile.user, form_type=CheckInForm.ONBOARDING
    ).prefetch_related("questions__options").first()

    full_name = (request.POST.get("__full_name") or "").strip()[:255]
    email = (request.POST.get("__email") or "").strip()[:254]
    if not full_name or not email:
        messages.error(request, "Name and email are required.")
        return redirect("public-site-page", slug=slug)

    # Username from email — collision-safe (append numeric suffix).
    base_username = slug_from_email(email)
    username = base_username
    n = 1
    while User.objects.filter(username=username).exists():
        n += 1
        username = f"{base_username}{n}"
        if n > 999:
            username = f"{base_username}-{secrets.token_hex(3)}"
            break

    temp_password = secrets.token_urlsafe(10)

    raw_answers = {}
    if onboarding_form:
        for q in onboarding_form.questions.all():
            raw_answers[q.field_key or f"q{q.id}"] = request.POST.get(f"q_{q.id}", "")

    # Capture the chosen pricing tier (if any) so the trainer sees
    # which one the client picked. Phase 7.7.1 will exchange this for
    # a Stripe Checkout session before user creation.
    chosen_plan_id = (request.POST.get("__plan_id") or "").strip()
    chosen_plan = None
    if chosen_plan_id:
        chosen_plan = PricingPlan.objects.filter(
            pk=chosen_plan_id, trainer=trainer_profile, is_active=True,
        ).first()
    if chosen_plan:
        raw_answers["__chosen_plan"] = {
            "id": chosen_plan.id,
            "name": chosen_plan.name,
            "price_pennies": chosen_plan.price_pennies,
            "currency": chosen_plan.currency,
            "interval": chosen_plan.interval,
        }

    with transaction.atomic():
        client = User.objects.create_user(
            username=username,
            email=email,
            password=temp_password,
            role=User.CLIENT,
        )
        # Pull first/last from full name if possible.
        parts = full_name.split(maxsplit=1)
        client.first_name = parts[0][:150]
        if len(parts) > 1:
            client.last_name = parts[1][:150]
        client.save()

        ClientProfile.objects.create(user=client, trainer=trainer_profile)

        signup = PublicSignup.objects.create(
            site=site,
            client_user=client,
            full_name=full_name,
            email=email,
            raw_answers=raw_answers,
        )

        if onboarding_form:
            submission = CheckInSubmission.objects.create(
                form=onboarding_form,
                client=client,
                status=CheckInSubmission.STATUS_SUBMITTED,
                submitted_at=timezone.now(),
            )
            for q in onboarding_form.questions.all():
                raw = request.POST.get(f"q_{q.id}", "")
                if raw == "":
                    continue
                kwargs = {"submission": submission, "question": q}
                if q.question_type == CheckInQuestion.NUMBER:
                    try:
                        kwargs["value_number"] = float(raw)
                    except (TypeError, ValueError):
                        kwargs["value_text"] = str(raw)[:2000]
                elif q.question_type == CheckInQuestion.YES_NO:
                    kwargs["value_yes_no"] = (str(raw).lower() in ("yes", "true", "1", "on"))
                elif q.question_type == CheckInQuestion.DROPDOWN:
                    matched = q.options.filter(value__iexact=str(raw)).first()
                    if matched:
                        kwargs["value_option"] = matched
                    kwargs["value_text"] = str(raw)[:2000]
                else:
                    kwargs["value_text"] = str(raw)[:2000]
                CheckInAnswer.objects.create(**kwargs)

    return render(request, "public/signup_thanks.html", {
        "trainer": trainer_profile,
        "client_username": username,
        "temp_password": temp_password,
        "brand_color": site.brand_color or "#c8ff00",
    })
