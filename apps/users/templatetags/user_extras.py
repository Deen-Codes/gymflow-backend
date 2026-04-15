from django import template

register = template.Library()


@register.filter
def get_item(obj, key):
    try:
        return obj.get(key)
    except AttributeError:
        try:
            return obj[key]
        except (KeyError, TypeError):
            return None


@register.filter
def get_field(form, field_name):
    try:
        return form[field_name]
    except Exception:
        return ""
