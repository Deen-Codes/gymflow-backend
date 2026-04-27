"""System-required profile fields.

This is the registry of fields the app *itself* needs from every
client — separate from the trainer-configurable onboarding check-in
form. As the app grows and a feature needs a new field (date of
birth, height, gender, units preference, …), it gets added here.

How it works at runtime:

  1. On login, iOS calls `GET /api/users/me/required-actions/`.
  2. The response lists any system fields the user hasn't filled
     yet (e.g. existing users who signed up before `date_of_birth`
     was added) plus a flag for whether they still owe their trainer
     onboarding form.
  3. Both gates are enforced before MainTabView appears — the user
     fills the missing fields via a small generic form rendered by
     `ProfileSetupView` from the `input_type` hints.
  4. On submit, iOS POSTs `/api/users/me/profile-update/` with the
     answered fields, the backend writes them to the User /
     ClientProfile model, and the gate clears.

The schema is a list rather than a dict so order is preserved (we
ask for fields in the same order in the form).

Each entry is a dict:
  key:        attribute name on the target model
  target:     "user" or "client_profile" — which row gets updated
  label:      human-friendly label shown in the form
  input_type: hint for iOS to render the right control
              ("date", "number", "short_text", "yes_no")
  required:   whether the field is blocking. Currently always true
              — non-required fields don't belong here, they belong
              in /settings.
"""

SYSTEM_REQUIRED_FIELDS = [
    {
        "key":        "date_of_birth",
        "target":     "user",
        "label":      "Date of birth",
        "input_type": "date",
        "required":   True,
    },
]


def missing_required_fields_for(user):
    """Return the subset of SYSTEM_REQUIRED_FIELDS the user hasn't
    filled yet. Skips non-clients — trainers don't go through this
    gate."""
    if user.role != "client":
        return []

    profile = getattr(user, "client_profile", None)

    out = []
    for field in SYSTEM_REQUIRED_FIELDS:
        target_obj = user if field["target"] == "user" else profile
        if target_obj is None:
            # Client without a client_profile shouldn't happen but
            # defensively don't try to read attributes off None.
            continue
        value = getattr(target_obj, field["key"], None)
        if value in (None, ""):
            out.append({
                "key":        field["key"],
                "label":      field["label"],
                "input_type": field["input_type"],
            })
    return out


def apply_profile_update(user, payload):
    """Apply a dict of {field_key: value} to the user / client_profile.
    Returns the list of field keys actually applied. Silently ignores
    keys that aren't in the schema so callers can't smuggle arbitrary
    field updates through this endpoint."""
    profile = getattr(user, "client_profile", None)
    applied = []
    for field in SYSTEM_REQUIRED_FIELDS:
        key = field["key"]
        if key not in payload:
            continue
        target_obj = user if field["target"] == "user" else profile
        if target_obj is None:
            continue
        value = payload[key]
        # Coerce by input_type — iOS sends strings over JSON.
        if field["input_type"] == "date" and isinstance(value, str):
            from datetime import date
            try:
                y, m, d = value.split("-")
                value = date(int(y), int(m), int(d))
            except (ValueError, AttributeError):
                continue
        elif field["input_type"] == "number":
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
        elif field["input_type"] == "yes_no":
            value = bool(value)
        setattr(target_obj, key, value)
        applied.append(key)
    if any(field["target"] == "user" for field in SYSTEM_REQUIRED_FIELDS
           if field["key"] in applied):
        user.save()
    if profile is not None and any(
        field["target"] == "client_profile"
        for field in SYSTEM_REQUIRED_FIELDS
        if field["key"] in applied
    ):
        profile.save()
    return applied


def needs_onboarding(user):
    """True when the client hasn't yet submitted their trainer's
    onboarding check-in form. Returns False for trainers."""
    if user.role != "client":
        return False
    from apps.progress.models import CheckInSubmission
    return not CheckInSubmission.objects.filter(
        client=user,
        status="submitted",
        form__form_type="onboarding",
    ).exists()
