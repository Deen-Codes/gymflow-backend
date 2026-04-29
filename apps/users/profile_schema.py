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
        "key":        "full_name",
        "target":     "user",
        "label":      "Your name",
        "input_type": "short_text",
        "required":   True,
    },
    {
        "key":        "date_of_birth",
        "target":     "user",
        "label":      "Date of birth",
        "input_type": "date",
        "required":   True,
    },
]


def _split_full_name(value):
    """Split a free-text name into (first_name, last_name).

    Rule (per product spec): first whitespace-separated token is the
    first name, the remainder is the last name. So "Mary Anne Smith"
    becomes ("Mary", "Anne Smith"). Handles single-token names by
    leaving last_name empty. Trims aggressively because users will
    type leading/trailing whitespace.
    """
    parts = (value or "").strip().split(None, 1)
    if not parts:
        return ("", "")
    if len(parts) == 1:
        return (parts[0][:150], "")
    first, rest = parts
    return (first[:150], rest[:150])


# Routing map for the trainer onboarding form's system-managed
# questions. When `CheckInQuestion.system_field_key` matches a key
# here, the answer is automatically written to the named attribute
# on the User or ClientProfile row. Same payload for both surfaces
# (onboarding form + ProfileSetupView) — single source of truth.
SYSTEM_FIELD_TARGETS = {
    "date_of_birth":   ("user",           "date_of_birth"),
    # When trainers add a "goal weight" question to onboarding with
    # system_field_key="goal_weight_kg", the answer flows into
    # ClientProfile.goal_weight_kg automatically.
    "goal_weight_kg":  ("client_profile", "goal_weight_kg"),
}


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
        # `full_name` doesn't map to a single column — it's a
        # composite captured in `user.first_name` (+ optional
        # `user.last_name`). Treat it as filled when first_name is
        # populated, since a single-word legal name is fine and
        # last_name being empty isn't a blocker.
        if field["key"] == "full_name":
            value = (user.first_name or "").strip()
        else:
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

        # `full_name` is a virtual field that splits onto
        # user.first_name + user.last_name. Done up here so the
        # generic setattr path below skips it cleanly. The client's
        # answer overrides whatever the trainer set at provisioning
        # — that's the spec ("whatever is filled out on the
        # onboarding form will override whatever PT may have put
        # in").
        if key == "full_name":
            first, last = _split_full_name(payload[key])
            if first:
                user.first_name = first
                user.last_name = last
                applied.append(key)
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

    # Persist user when any user-targeted field (including the
    # virtual full_name) was applied.
    user_targeted = {field["key"] for field in SYSTEM_REQUIRED_FIELDS
                     if field["target"] == "user"}
    if any(k in user_targeted for k in applied):
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


def apply_system_field_from_answer(user, system_field_key, answer_kwargs):
    """Called from the form-submit handler whenever a CheckInAnswer is
    saved against a question with `system_field_key` set. Writes the
    answer's value to the corresponding User/ClientProfile attribute.

    The mapping in SYSTEM_FIELD_TARGETS tells us which row + column to
    update; the value comes from `answer_kwargs` (the dict that just
    got persisted onto CheckInAnswer), keyed by which value_X column
    holds the user's answer. We pick the right column based on what's
    in the kwargs — no need to switch on question_type again.

    `full_name` is a special composite — splits onto user.first_name
    + user.last_name. Handled inline below because SYSTEM_FIELD_TARGETS
    can only describe single-attr writes.
    """
    # Composite: "full_name" → first_name + last_name on User. The
    # client's answer here overrides whatever the trainer set at
    # provisioning, per the product spec.
    if system_field_key == "full_name":
        raw = answer_kwargs.get("value_text") or ""
        first, last = _split_full_name(raw)
        if not first:
            return
        user.first_name = first
        user.last_name = last
        user.save(update_fields=["first_name", "last_name"])
        return

    target_info = SYSTEM_FIELD_TARGETS.get(system_field_key)
    if target_info is None:
        return
    target_name, attr = target_info
    if target_name == "user":
        target_obj = user
    elif target_name == "client_profile":
        target_obj = getattr(user, "client_profile", None)
    else:
        return
    if target_obj is None:
        return

    # Pull the value out of whichever value_X column the submit
    # handler populated. Order matters — try the most specific first.
    value = None
    if "value_date" in answer_kwargs:
        value = answer_kwargs["value_date"]
    elif "value_number" in answer_kwargs:
        value = answer_kwargs["value_number"]
    elif "value_text" in answer_kwargs and answer_kwargs["value_text"]:
        value = answer_kwargs["value_text"]
    elif "value_yes_no" in answer_kwargs:
        value = answer_kwargs["value_yes_no"]
    if value is None:
        return

    setattr(target_obj, attr, value)
    target_obj.save(update_fields=[attr])
