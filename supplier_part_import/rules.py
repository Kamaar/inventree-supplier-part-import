"""Import rules, mostly ported from Ki-nTree (independent from Django).

- parameter mapping by category, with the 'parent' inheritance of
  Ki-nTree's supplier_parameters.yaml
- parameter value clean-up (Ki-nTree common/part_tools.clean_parameter_value)
- IPN proposal: prefix + mounting digit + '-' + progressive number
  (type prefix, learned from the IPNs of the category, e.g. 'PE06', + mounting digit + '-' + NNNN)
- RoHS / REACH parameters (Italian values) and attachment URLs
"""

import re

# ---------------------------------------------------------------- parameters


def parameter_map(scopes: dict, category_path: list[str]) -> dict[str, str]:
    """Return {supplier parameter name: template name} for an InvenTree category.

    Same lookup as Ki-nTree (config_interface.load_category_parameters):
    the most specific scope wins ('Capacitors/Ceramic', then 'Ceramic',
    then 'Capacitors'), and its 'parent' scopes are added first.

    Arguments:
        scopes: {scope name: {'parents': [...], 'mappings': {template: [supplier names]}}}
        category_path: InvenTree category names, from the root
    """
    result: dict[str, str] = {}

    def add(scope_name, seen):
        if scope_name in seen or scope_name not in scopes:
            return

        seen.add(scope_name)
        scope = scopes[scope_name]

        for parent in scope.get('parents') or []:
            add(parent, seen)

        for template, supplier_names in (scope.get('mappings') or {}).items():
            for name in supplier_names or []:
                result[name] = template

    path = [c for c in category_path if c]

    # From the deepest category: its full path ('Capacitors/Ceramic'), then its name
    for i in range(len(path), 0, -1):
        for candidate in ('/'.join(path[:i]), path[i - 1]):
            if candidate in scopes:
                add(candidate, set())
                return result

    return result


MPN_PARAMETER = 'Manufacturer Part Number'


def with_derived_parameters(parameters: dict[str, str], mpn: str = '') -> dict[str, str]:
    """Add the parameters the Ki-nTree mappings expect but the suppliers do not report as such.

    - 'Manufacturer Part Number': several scopes map 'Value' to it (Ki-nTree pseudo-parameter)
    - Farnell 'X Min' / 'X Max' pairs (temperature, supply voltage) as one DigiKey-like
      range 'X' = 'min ~ max', so the range mappings and the clean-up apply
    """
    result = dict(parameters)

    if mpn and MPN_PARAMETER not in result:
        result[MPN_PARAMETER] = mpn

    for name in list(parameters):
        if not name.endswith(' Min'):
            continue

        base = name[: -len(' Min')]
        low, high = parameters.get(name), parameters.get(f'{base} Max')

        if low and high and base not in result:
            result[base] = f'{low} ~ {high}'

    return result


def clean_parameter_value(category: str, name: str, value: str) -> str:
    """Clean-up a parameter value (Ki-nTree common/part_tools.clean_parameter_value)."""
    category = (category or '').lower()
    name = (name or '').lower()
    value = str(value or '').strip()

    is_dimension = any(word in name for word in ('size', 'height', 'pitch', 'outline'))

    # Package: keep the value before the space
    # (not for dimensions like 'Package Height': Ki-nTree kept the inches there)
    if 'package' in name and not is_dimension:
        space_split = value.split()

        if len(space_split) > 1:
            value = space_split[0].replace(',', '')

    # Sizes: only the metric dimensions
    if is_dimension:
        metric = re.findall('[.0-9]*mm', value)

        if len(metric) == 1:
            value = '⌀' + metric[0] if 'dia' in value.lower() else metric[0]
        elif len(metric) == 2:
            value = metric[0].replace('mm', '') + 'x' + metric[1]
        elif len(metric) == 3:
            value = metric[0].replace('mm', '') + 'x' + metric[1].replace('mm', '') + 'x' + metric[2]

    # Power: keep the ratio (1/4W)
    if 'power' in name:
        ratio = re.findall('[0-9]/[0-9]*W', value)

        if ratio:
            value = ratio[0]

    # ESR, DCR, RDS ('Ohm' as Ki-nTree, also lowercase for Farnell '0.02ohm')
    if 'esr' in name or 'dcr' in name or 'rds' in name:
        value = re.sub('ohms?', 'R', value.replace('Max', '').replace(' ', ''), flags=re.IGNORECASE)

    # Counts: Farnell '2Contacts', '8Pins' -> '2', '8'
    if 'number of' in name or 'no. of' in name:
        count = re.match(r'\s*(\d+)', value)

        if count:
            value = count[1]

    # Tolerances: Farnell '± 10%' -> '±10%' (DigiKey format)
    value = value.replace('± ', '±')

    # Resistors: '10 kOhms' -> '10K', '100 Ohms' -> '100R' (Farnell: '10kohm', '100ohm')
    if 'resistor' in category and 'resistance' in name:
        compact = re.fullmatch(r'([\d.]+)\s*([kMG]?)ohms?', value, re.IGNORECASE)

        if compact:
            value = compact[1] + (compact[2].upper() if compact[2] else 'R')

        space_split = value.split()

        if len(space_split) > 1:
            resistance, unit = space_split[0], space_split[1]

            if unit in ('kOhms', 'MOhms', 'GOhms'):
                unit = unit.replace('Ohms', '').upper()
            else:
                unit = unit.replace('Ohms', 'R')

            value = resistance + unit

    # Ranges: '-55°C ~ 125°C' -> '-55~125°C'
    separator = '~'

    if separator in value:
        space_split = value.split()

        if len(space_split) > 2:
            first_value, second_value = space_split[0], space_split[2]
            unit = first_value.replace(re.findall('[-.0-9]*', first_value)[0], '')

            if unit:
                value = first_value.replace(unit, '') + separator + second_value

    # Remove the parenthesis sections
    if '(' in value:
        parenthesis = re.findall(r'\(.*\)', value)

        if parenthesis:
            for item in parenthesis:
                value = value.replace(item, '')

            value = value.replace(' ', '')

    if '@' in value:
        value = value.replace(' ', '')

    return value.strip()


def map_parameters(
    supplier_parameters: dict[str, str],
    mapping: dict[str, str],
    category: str = '',
    ignored: set[str] | None = None,
    import_unmapped: bool = True,
) -> list[dict]:
    """Translate the supplier parameters into InvenTree parameters.

    Returns a list of {'template', 'value', 'source', 'mapped'}:
    - mapped parameters use the template name and the cleaned value
    - the other ones (if import_unmapped) keep the supplier name and value
    - the first value wins when several supplier names map to the same template
    """
    ignored = {i.lower() for i in ignored or set()}
    result = {}

    for source, value in supplier_parameters.items():
        if not value:
            continue

        template = mapping.get(source)

        # The ignored list only applies to the parameters without a mapping
        if not template and source.lower() in ignored:
            continue

        if template:
            # Ki-nTree chooses the clean-up by the supplier name ('Resistance'); the template name
            # ('Package Size') is added for the suppliers whose names differ (Farnell 'Power Inductor Case')
            cleaned = clean_parameter_value(category, f'{template} {source}', value)

            if cleaned and template not in result:
                result[template] = {'template': template, 'value': cleaned, 'source': source, 'mapped': True}
        elif import_unmapped and source not in result and source != MPN_PARAMETER:
            result[source] = {'template': source, 'value': str(value).strip(), 'source': source, 'mapped': False}

    # Mapped parameters first, in the order of the supplier
    return sorted(result.values(), key=lambda p: not p['mapped'])


# ---------------------------------------------------------------- supplier categories


def legacy_category_match(name: str, path: list[str]) -> bool:
    """Match an old DigiKey API v3 category name against a v4 category path.

    v3 names join the levels with ' - ' ('Transistors - FETs, MOSFETs - Single'),
    v4 returns the levels ('Discrete Semiconductor Products', 'Transistors',
    'FETs, MOSFETs', 'Single FETs, MOSFETs'). The name matches when each part
    is contained, in order, in the path levels (several parts may be in the
    same level: 'Voltage Regulators - Linear, ...').
    """
    parts = [p.strip().lower() for p in name.split(' - ') if p.strip()]

    if len(parts) < 2:
        return False

    levels = [level.lower() for level in path]
    index = 0

    for part in parts:
        while index < len(levels) and part not in levels[index]:
            index += 1

        if index == len(levels):
            return False

    return True


# ---------------------------------------------------------------- function filter (Ki-nTree '__' subcategories)

FUNCTION_FILTER_KEY = '__'
FUNCTION_TEMPLATE = 'Function Type'


def function_values(supplier_parameters: dict[str, str], mapping: dict[str, str]) -> list[str]:
    """Values of the supplier parameters mapped to 'Function Type' (e.g. 'FET Type' = 'N-Channel')."""
    return [
        str(value)
        for name, value in supplier_parameters.items()
        if value and mapping.get(name) == FUNCTION_TEMPLATE
    ]


def function_match(subcategory: str, values: list[str]) -> bool:
    """True if the subcategory name matches one of the values (Ki-nTree with CATEGORY_MATCH_RATIO_LIMIT 100).

    fuzz.partial_ratio = 100 means that the shorter text is contained in the
    longer one: 'N-Channel' matches 'N-Channel FET', 'Schottky' matches 'Schottky'.
    """
    def normalize(text):
        # 'N Channel' (Farnell) == 'N-Channel' (DigiKey)
        return ' '.join(re.sub(r'[-_]', ' ', text.lower()).split())

    name = normalize(subcategory)

    for value in values:
        value = normalize(value)

        if not value or not name:
            continue

        shorter, longer = sorted((name, value), key=len)

        if shorter in longer:
            return True

    return False


# ---------------------------------------------------------------- RoHS / REACH

ROHS_STATUS_TEMPLATE = 'Stato RoHS'
REACH_TEMPLATE = 'REACH'
ROHS_CHECKBOX_TEMPLATE = 'RoHS'

COMPLIANCE_TEMPLATES = {
    ROHS_STATUS_TEMPLATE: 'Conformità RoHS (direttiva 2011/65/UE e 2015/863)',
    REACH_TEMPLATE: 'Conformità REACH (regolamento CE 1907/2006, sostanze SVHC)',
}


def compliance_parameters(rohs: str, reach: str) -> list[dict]:
    """The 'Stato RoHS', 'REACH' and 'RoHS' (checkbox) parameters, as written by conformita.py."""
    result = []

    if rohs:
        result.append({'template': ROHS_STATUS_TEMPLATE, 'value': rohs, 'source': 'RoHS', 'mapped': True})

        checkbox = 'True' if rohs.startswith('Conforme') else 'False' if rohs == 'Non conforme' else ''

        if checkbox:
            result.append({'template': ROHS_CHECKBOX_TEMPLATE, 'value': checkbox, 'source': 'RoHS', 'mapped': True})

    if reach:
        result.append({'template': REACH_TEMPLATE, 'value': reach, 'source': 'REACH', 'mapped': True})

    return result


DECLARATION_COMMENT = 'Dichiarazione RoHS/REACH'
DATASHEET_COMMENT = 'Datasheet'


def normalize_url(url: str) -> str:
    """Make a supplier URL acceptable for an InvenTree link attachment (conformita.py / import_anagrafica.py).

    - spaces are encoded, '//' and 'httpss://' fixed
    - '&region=' is moved first: InvenTree reads '&reg' as the HTML entity '®'
    """
    url = (url or '').strip().replace(' ', '%20')

    if url.startswith('//'):
        url = 'https:' + url

    if url.startswith('httpss://'):
        url = 'https://' + url[len('httpss://'):]

    if '&region=' in url and '?' in url:
        base, query = url.split('?', 1)
        parts = query.split('&')
        url = base + '?' + '&'.join(sorted(parts, key=lambda p: not p.startswith('region=')))

    return url if url.lower().startswith('http') else ''


# ---------------------------------------------------------------- IPN

# Mounting digit of the IPN (Italian names of the mounting types)
MOUNTING = {'0': '', '1': 'SMD', '2': 'THD', '3': 'da pannello', '4': 'da pannello a crimpare',
            '5': 'volante a crimpare', '6': 'volante'}

IPN_RE = re.compile(r'^(?P<prefix>[A-Z]+\d{2})(?P<mounting>\d)-(?P<number>\d+)$')


def mounting_digit(parameters: dict[str, str]) -> str | None:
    """Mounting digit from the supplier 'Mounting Type' (None if unknown)."""
    value = ''

    for name, text in parameters.items():
        if 'mounting' in name.lower() or 'montaggio' in name.lower():
            value = str(text).lower()
            break

    if not value:
        return None

    if 'surface' in value or 'smd' in value or 'smt' in value:
        return '1'

    if 'through' in value or 'thd' in value or 'tht' in value:
        return '2'

    if 'panel' in value or 'pannello' in value:
        return '3'

    if 'free hanging' in value or 'cable' in value or 'in-line' in value:
        return '6'

    return None


def ipn_prefix(category_ipns: list[str], mounting: str | None) -> tuple[str, str] | None:
    """Choose (type prefix, mounting digit) from the IPNs of the parts in the category.

    The most common type prefix ('PE06') of the category is used; the mounting
    digit comes from the supplier data when it exists in the category,
    otherwise the most common one.
    """
    counts: dict[tuple[str, str], int] = {}

    for ipn in category_ipns:
        match = IPN_RE.match((ipn or '').strip())

        if match:
            key = (match['prefix'], match['mounting'])
            counts[key] = counts.get(key, 0) + 1

    if not counts:
        return None

    prefix_counts: dict[str, int] = {}

    for (prefix, _), n in counts.items():
        prefix_counts[prefix] = prefix_counts.get(prefix, 0) + n

    prefix = max(prefix_counts, key=lambda p: (prefix_counts[p], p))

    if mounting is not None and (prefix, mounting) in counts:
        return prefix, mounting

    if mounting is not None:
        # A new mounting for this type: allowed, the digit comes from the supplier data
        return prefix, mounting

    digit = max((m for (p, m) in counts if p == prefix), key=lambda m: counts[(prefix, m)])
    return prefix, digit


def next_ipn(prefix: str, mounting: str, all_ipns: list[str], digits: int = 4) -> str:
    """First free progressive number after the highest one used with this prefix and mounting."""
    stem = f'{prefix}{mounting}-'
    highest = 0

    for ipn in all_ipns:
        ipn = (ipn or '').strip()

        if ipn.startswith(stem) and ipn[len(stem):].isdigit():
            highest = max(highest, int(ipn[len(stem):]))

    return f'{stem}{str(highest + 1).zfill(digits)}'


# ---------------------------------------------------------------- units

# '22 µF' -> '22µF' (only a single number followed by a unit)
_SPACED_QUANTITY = re.compile(r'^([-+±]?\d+(?:[.,]\d+)?)\s+([A-Za-zµΩ°%][A-Za-zµΩ°%/]*)$')


def compact_quantity(value: str) -> str:
    """Remove the space between number and unit ('22 µF' -> '22µF'); other values unchanged."""
    match = _SPACED_QUANTITY.match((value or '').strip())
    return f'{match[1]}{match[2]}' if match else (value or '').strip()


PLACEHOLDER_VALUES = ('', '-', '--', '---', '—', '?', 'n/a', 'na', 'none', 'tbd')


def is_placeholder_value(value: str) -> bool:
    """'-' and similar mean 'no value': never converted (the converter reads '-' as -1)."""
    return (value or '').strip().lower() in PLACEHOLDER_VALUES


def split_quantity(value: str) -> tuple[str, list[str], str]:
    """Split a value into (sign, parts, separator) for the unit conversion.

    '±20%' -> ('±', ['20%'], ''); '⌀10.00mm' -> ('⌀', ['10.00mm'], '');
    '-55~85°C' and '-55°C to 125°C' -> ('', ['-55', '85°C'], '~');
    '2.00x1.25mm' -> ('', ['2.00', '1.25mm'], 'x'); '25V' -> ('', ['25V'], '').
    """
    text = (value or '').strip()
    sign = ''

    for prefix in ('±', '⌀'):
        if text.startswith(prefix):
            sign, text = prefix, text[len(prefix):].strip()
            break

    # Ranges written with 'to' ('-55℃ to 125℃')
    text = re.sub(r'\s+to\s+', '~', text)

    for separator in ('~', 'x'):
        # 'x' only between numbers (dimensions), not inside units or words
        pattern = r'\s*~\s*' if separator == '~' else r'(?<=[\d.])\s*x\s*(?=[\d.])'
        parts = re.split(pattern, text)

        if len(parts) > 1:
            return sign, [p.strip() for p in parts], separator

    return sign, [text], ''


def normalize_unit_text(value: str) -> str:
    """Spellings the InvenTree converter does not know.

    'mOhm' / '10 kOhms' -> 'mohm' / '10 kohm'; '℃' -> '°C'; '277 VDC' -> '277 V';
    '0.236\\"' (Ki-nTree escapes the inch sign for KiCad) -> '0.236"'.
    """
    value = re.sub(r'ohms?', 'ohm', value, flags=re.IGNORECASE)
    value = value.replace('℃', '°C').replace('\\"', '"')
    return re.sub(r'(?<=[\d\s])VDC\b', 'V', value)


def format_number(number: float) -> str:
    """Round the float noise of the conversion (21999.999999999996 -> '22000').

    10 significant digits, always as a plain decimal number: '{:g}' alone switches to
    exponent notation below 1e-4 and from 1e10 (1µA in A -> '1e-06', expected '0.000001').
    """
    from decimal import Decimal

    text = f'{number:.10g}'

    if 'e' not in text:
        return text

    text = format(Decimal(text), 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


SI_PREFIXES = {-15: ('f',), -12: ('p',), -9: ('n',), -6: ('µ', 'u'), -3: ('m',), 3: ('k',), 6: ('M',), 9: ('G',), 12: ('T',)}


def si_prefixed(number: float) -> list[str]:
    """Candidates with an SI prefix and no unit: 1e-06 -> ['1µ', '1u'], 4700 -> ['4.7k'].

    Empty for 0 and for values between 1 and 1000, which stay plain numbers.
    """
    import math

    if not number or not math.isfinite(number) or 1 <= abs(number) < 1000:
        return []

    exponent = max(-15, min(12, math.floor(math.log10(abs(number)) / 3) * 3))
    mantissa = format_number(number / 10**exponent)

    # Rounding may give '1000' (999.99999999999 m): use the next prefix
    if abs(float(mantissa)) >= 1000 and exponent < 12:
        exponent += 3
        mantissa = format_number(number / 10**exponent)

    return [f'{mantissa}{prefix}' for prefix in SI_PREFIXES.get(exponent, ())]


# ---------------------------------------------------------------- misc


def norm_code(code: str) -> str:
    """Normalize a part number for comparisons ('ERA-8AEB470V' == 'ERA8AEB470V')."""
    return re.sub(r'[^A-Z0-9]', '', str(code or '').upper())


def code_regex(code: str) -> str | None:
    """Database regex matching a code written with any separator and case.

    '885012107027' also matches '8850-1210-7027' and ' 885012107027 ';
    'ERA-8AEB470V' also matches 'ERA8AEB470V'. Codes shorter than 4 characters
    are ignored (too generic). Use with the case insensitive '__iregex' lookup.
    """
    normalized = norm_code(code)

    if len(normalized) < 4:
        return None

    separator = '[^A-Za-z0-9]*'
    return '^' + separator + separator.join(re.escape(c) for c in normalized) + separator + '$'


def preferred_result(results: list, mpn: str):
    """Pick the supplier packaging to use as alternate supplier part: cut tape / single units first."""
    exact = [r for r in results if norm_code(r.mpn) == norm_code(mpn)]

    if not exact:
        return None

    def rank(result):
        packaging = (result.packaging or '').lower()

        if 'cut tape' in packaging or 'nastro tagliato' in packaging:
            return 0
        if 'digi-reel' in packaging or 'mousereel' in packaging:
            return 2
        if 'reel' in packaging or 'bobina' in packaging:
            return 3
        return 1

    return min(exact, key=rank)
