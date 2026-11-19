"""Parameter values in the units of their template.

InvenTree shows the template units in their own column: a value like '25V'
on a template with units 'V' is shown as '25V V'. The values are stored as
numbers in the template units ('25', '-55~85', '±20'); templates without
units keep the unit in the value, without space ('22µF').
"""

from django.core.exceptions import ValidationError

from . import rules


def to_template_units(value: str, units: str) -> str | None:
    """Convert a value to the template units (None if it cannot be converted)."""
    from InvenTree.conversion import convert_physical_value

    value = (value or '').strip()

    if rules.is_placeholder_value(value):
        return None

    units = (units or '').strip()

    if not units:
        return rules.compact_quantity(value)

    sign, parts, separator = rules.split_quantity(rules.normalize_unit_text(value))
    converted = []

    for part in parts:
        if units == '%':
            # The converter reads '%' as a fraction ('20%' -> 0.2): only the number is kept
            number = part.replace('%', '').strip().replace(',', '.')

            try:
                float(number)
            except ValueError:
                return None

            converted.append(number)
            continue

        try:
            converted.append(rules.format_number(convert_physical_value(part, units)))
        except (ValidationError, ValueError, TypeError):
            return None

    return sign + separator.join(converted)


def plan(apply: bool = False, sample_size: int = 200) -> dict:
    """Check (and optionally fix) the values of all the part parameters.

    Returns the number of values to change / not convertible, and samples of both.
    """
    from django.contrib.contenttypes.models import ContentType

    from common.models import Parameter
    from part.models import Part

    content_type = ContentType.objects.get_for_model(Part)
    parameters = Parameter.objects.filter(model_type=content_type).select_related('template')
    changes, failures = [], []

    for parameter in parameters.iterator():
        template = parameter.template

        if template.checkbox:
            continue

        # '-' and similar are 'no value': neither converted nor reported
        if rules.is_placeholder_value(parameter.data):
            continue

        new = to_template_units(parameter.data, template.units)

        if new is None:
            if template.units and parameter.data:
                failures.append((parameter, template))
            continue

        if new != parameter.data:
            changes.append((parameter, template, new))

    if apply:
        for parameter, _template, new in changes:
            parameter.data = new
            parameter.save()

    # Names of the parts shown in both samples
    shown = [p for p, *_ in changes[:sample_size]] + [p for p, _ in failures[:sample_size]]
    names = dict(Part.objects.filter(pk__in={p.model_id for p in shown}).values_list('pk', 'name'))

    def row(parameter, template, new=None):
        return {
            'part': parameter.model_id,
            'part_name': names.get(parameter.model_id, ''),
            'template': template.name,
            'units': template.units,
            'old': parameter.data,
            'new': new,
        }

    return {
        'applied': apply,
        'checked': parameters.count(),
        'changes': len(changes),
        'failures': len(failures),
        'change_samples': [row(p, t, n) for p, t, n in changes[:sample_size]],
        'failure_samples': [row(p, t) for p, t in failures[:sample_size]],
    }
