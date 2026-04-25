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
from django.contrib.auth.decorators import login_required
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
from apps.users.dashboard_helpers import trainer_required, dashboard_context
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
@login_required
def trainer_site_page(request):
    """Site editor — renders the 3-column workspace.

    Outline list of sections on the left, live preview in the centre,
    properties panel on the right (shows the selected section's fields,
    or a "site overview" panel when nothing is selected)."""
    if not trainer_required(request):
        return redirect("landing-page")

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

    Body: {is_published?, brand_color?}
    """
    site = ensure_site(request.user.trainer_profile)
    if "is_published" in request.data:
        site.is_published = bool(request.data["is_published"])
    if "brand_color" in request.data:
        color = (request.data["brand_color"] or "").strip()
        if color and not (color.startswith("#") and len(color) in (4, 7)):
            return Response({"detail": "brand_color must be a hex like #c8ff00."}, status=400)
        site.brand_color = color
    site.save()
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
