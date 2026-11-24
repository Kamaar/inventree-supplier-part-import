"""Parameter values in the units of their template.

InvenTree shows the template units in their own column: a value like '25V'
on a template with units 'V' is shown as '25V V'. The values are stored as
numbers in the template units ('25', '-55~85', '±20'); very small or large
numbers keep an SI prefix without the unit ('1µ' on a template in A, shown as
'1µ A'; InvenTree reads it as 1µA). Templates without units keep the unit in the
value, without space ('22µF').
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
    dimensionless = units != '%' and is_dimensionless_unit(units)

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

        if dimensionless:
            # Same problem for 'ppm', 'ppb'...: the converter returns the fraction
            # ('50ppm' -> 5e-05) and labels it with the template units
            number = dimensionless_to_units(part, units)

            if number is None:
                return None

            converted.append(number)
            continue

        try:
            converted.append(with_si_prefix(convert_physical_value(part, units), units))
        except (ValidationError, ValueError, TypeError):
            return None

    return sign + separator.join(converted)


def with_si_prefix(number: float, units: str) -> str:
    """A number in the template units, with an SI prefix when it is very small or large.

    1e-06 in A -> '1µ', 0.0047 in F -> '4.7m'. A prefixed value is used only if the
    InvenTree converter reads it back as the same number: '5m' on a template in m
    would be read as 5 metres, so it stays '0.005'.
    """
    from InvenTree.conversion import convert_physical_value

    for candidate in rules.si_prefixed(number):
        try:
            read_back = convert_physical_value(candidate, units)
        except (ValidationError, ValueError, TypeError):
            continue

        if abs(read_back - number) <= abs(number) * 1e-9:
            return candidate

    return rules.format_number(number)


def _registry():
    from InvenTree.conversion import get_unit_registry

    return get_unit_registry()


def is_dimensionless_unit(units: str, registry=None) -> bool:
    """True for units which are a pure number ('ppm', 'ppb', 'percent')."""
    try:
        registry = registry or _registry()
        return bool(registry.Quantity(1, units).dimensionless)
    except Exception:  # noqa: BLE001 - any pint parse error: not a known unit
        return False


def dimensionless_to_units(part: str, units: str, registry=None) -> str | None:
    """'50ppm' -> '50' in 'ppm', '0.005%' -> '50' in 'ppm'; a plain number is already in the units.

    None if the part is not a dimensionless quantity.
    """
    text = part.strip().replace(',', '.')

    try:
        return rules.format_number(float(text))
    except ValueError:
        pass

    try:
        registry = registry or _registry()
        quantity = registry.Quantity(text)

        if not quantity.dimensionless:
            return None

        return rules.format_number(float(quantity.to(units).magnitude))
    except Exception:  # noqa: BLE001 - any pint parse / conversion error: not convertible
        return None


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
