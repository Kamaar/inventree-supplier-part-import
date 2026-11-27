"""Search, preview and creation of parts from the supplier data.

All the supplier requests go through part_pricing_update.api, so they are
counted in the daily quota of the PartPricingUpdate plugin.
"""

import logging
import pickle
import uuid
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from . import rules
from .units import to_template_units

logger = logging.getLogger('inventree')

PLUGIN_SLUG = 'supplier-part-import'
PREVIEW_TIMEOUT = 60 * 60  # seconds a preview is kept for the import
PREVIEW_EXPIRED = 'The preview has expired: search the product again'


class ImportError_(Exception):
    """Error shown to the user (bad input, missing dependency)."""


def save_preview(data: dict) -> str:
    """Keep the data of a preview for the import; returns its token.

    Stored in the database, not in the Django cache: without Redis (e.g. the .deb
    installation) every gunicorn worker has its own memory cache, and the import
    may be served by a worker which never saw the preview.
    """
    from django.utils import timezone

    from .models import PreviewSession

    # The expired previews are removed here: no scheduled task is needed
    PreviewSession.objects.filter(created__lt=timezone.now() - timedelta(seconds=PREVIEW_TIMEOUT)).delete()

    token = uuid.uuid4().hex
    PreviewSession.objects.create(token=token, data=pickle.dumps(data, protocol=pickle.HIGHEST_PROTOCOL))
    return token


def load_preview(token) -> dict:
    """Data saved by save_preview (ImportError_ if missing or expired)."""
    from django.utils import timezone

    from .models import PreviewSession

    session = PreviewSession.objects.filter(
        token=str(token or ''), created__gte=timezone.now() - timedelta(seconds=PREVIEW_TIMEOUT)
    ).first()

    if session is None:
        raise ImportError_(PREVIEW_EXPIRED)

    try:
        # Written only by save_preview: never data received from the client
        return pickle.loads(bytes(session.data))
    except Exception as exc:  # e.g. a class of the pricing plugin changed after an update
        logger.warning('SupplierPartImport: preview %s not readable (%s)', token, exc)
        raise ImportError_(PREVIEW_EXPIRED) from exc


def pricing_api():
    """The public API of the PartPricingUpdate plugin, if it is installed and active."""
    try:
        from part_pricing_update import api
    except ImportError:
        return None

    return api if api.is_available() else None


def require_pricing_api():
    api = pricing_api()

    if api is None:
        raise ImportError_(
            'The "Part Pricing Update" plugin is required: install and activate it '
            '(it provides the supplier API credentials and the daily request limits)'
        )

    return api


def get_plugin():
    from plugin import registry

    return registry.get_plugin(PLUGIN_SLUG)


def setting(name):
    plugin = get_plugin()
    return plugin.get_setting(name) if plugin else None


def setting_list(name) -> list[str]:
    return [v.strip() for v in str(setting(name) or '').replace('\n', ',').split(',') if v.strip()]


# ---------------------------------------------------------------- search


ALL_SUPPLIERS = 'all'


def search(supplier: str, term: str) -> dict:
    """Keyword search on one supplier (or on all the configured ones).

    Returns {'results': [...], 'messages': [...]}: the messages explain empty
    results (nothing found, product listed but not sold, quota, errors), so the
    user always knows what happened.
    """
    api = require_pricing_api()
    keys = api.configured_suppliers() if supplier == ALL_SUPPLIERS else [supplier]
    found = []
    messages = []

    for key in keys:
        name = api.SUPPLIER_NAMES.get(key, key)

        try:
            results = api.get_client(key).search(term, limit=20 if len(keys) == 1 else 10)
        except api.SupplierError as exc:
            messages.append(f'{name}: {exc}')
            continue

        sold = [r for r in results if r.sku]
        not_sold = sorted({r.mpn for r in results if not r.sku and r.mpn})

        if not_sold:
            messages.append(f'{name}: {", ".join(not_sold)} in the catalog but not sold by {name}')
        elif not sold:
            messages.append(f'{name}: no results for "{term}"')

        found += [(key, name, r) for r in sold]

    existing = existing_by_code(
        [r.mpn for _, _, r in found] + [r.sku for _, _, r in found] + [a for _, _, r in found for a in aliases(r)]
    )

    def first(*codes):
        for code in codes:
            parts = existing.get(rules.norm_code(code))

            if parts:
                return parts[0]

        return None

    return {
        'results': [
            {
                'supplier': key,
                'supplier_name': name,
                'sku': r.sku,
                'mpn': r.mpn,
                'manufacturer': r.manufacturer,
                'description': r.description,
                'packaging': r.packaging,
                'image_url': r.image_url,
                'product_url': r.product_url,
                'status': r.status,
                'discontinued': api.is_discontinued(r.status),
                'stock': r.stock,
                'unit_price': r.unit_price,
                'currency': r.currency,
                'existing': first(r.sku, r.mpn, *aliases(r)),
            }
            for key, name, r in found
        ],
        'messages': messages,
    }


MATCH_PRIORITY = {'MPN': 0, 'SKU': 1, 'name': 2, 'old name': 3, 'searched code': 4}


def aliases(product) -> list[str]:
    """Other names of a search result / product (e.g. the old MPN of a renamed product).

    Reported by the pricing plugin since 0.4.7 (DigiKey 'OtherNames').
    """
    return list(getattr(product, 'aliases', None) or [])


def existing_by_code(codes: list[str]) -> dict[str, list[dict]]:
    """InvenTree parts already using one of these codes: {normalized code: [candidates]}.

    The codes are compared ignoring separators and case ('8850-1210-7027' ==
    '885012107027'), on:
    - the MPN of the manufacturer parts
    - the SKU of the supplier parts of any supplier (e.g. a Würth part whose SKU is the MPN)
    - the part name (often the MPN)
    """
    from company.models import ManufacturerPart, SupplierPart
    from part.models import Part

    patterns = {rules.norm_code(c): rules.code_regex(c) for c in codes if c}
    patterns = {code: regex for code, regex in patterns.items() if regex}
    found: dict[str, dict[int, tuple]] = {}

    def add(code_value, part, match):
        code = rules.norm_code(code_value)

        if code in patterns and part is not None:
            current = found.setdefault(code, {}).get(part.pk)

            if current is None or MATCH_PRIORITY[match] < MATCH_PRIORITY[current[1]]:
                found[code][part.pk] = (part, match)

    if not patterns:
        return {}

    def any_of(field):
        query = Q()

        for regex in patterns.values():
            query |= Q(**{f'{field}__iregex': regex})

        return query

    for mp in ManufacturerPart.objects.filter(any_of('MPN')).select_related('part'):
        add(mp.MPN, mp.part, 'MPN')

    for sp in SupplierPart.objects.filter(any_of('SKU')).select_related('part'):
        add(sp.SKU, sp.part, 'SKU')

    for part in Part.objects.filter(any_of('name')):
        add(part.name, part, 'name')

    return {
        code: [
            existing_part_info(part, match)
            for part, match in sorted(parts.values(), key=lambda pm: (MATCH_PRIORITY[pm[1]], pm[0].pk))
        ]
        for code, parts in found.items()
    }


def existing_part_info(part, match: str) -> dict:
    """Summary of an existing part, with its suppliers and parameters (to propose only the missing ones)."""
    from django.contrib.contenttypes.models import ContentType

    from common.models import Parameter
    from company.models import SupplierPart

    supplier_parts = SupplierPart.objects.filter(part=part).select_related('supplier')
    parameters = Parameter.objects.filter(
        model_type=ContentType.objects.get_for_model(part.__class__), model_id=part.pk
    ).select_related('template')

    return {
        'pk': part.pk,
        'name': part.name,
        'IPN': part.IPN or '',
        'url': f'/web/part/{part.pk}/',
        'match': match,
        'active': part.active,
        # The parameters of an existing part are mapped with its own category
        'category': part.category_id,
        'category_path': part.category.pathstring if part.category else '',
        # Template name (lowercase) -> current value
        'parameters': {p.template.name.lower(): p.data for p in parameters},
        'supplier_parts': [
            {'company': sp.supplier_id, 'supplier': sp.supplier.name, 'sku': sp.SKU} for sp in supplier_parts
        ],
    }


def existing_candidates(codes: list[str], old_names: list[str] = (), searched: str = '') -> list[dict]:
    """All the parts matching any of the codes, best match first, without duplicates.

    old_names: other names of the product (a renamed product: the part may still use
    the old MPN). searched: the code typed by the user, the weakest match (a keyword
    search may return other products).
    """
    candidates = {}
    labels = {rules.norm_code(c): 'searched code' for c in [searched] if c}
    labels.update({rules.norm_code(c): 'old name' for c in old_names if c})
    labels.update({rules.norm_code(c): None for c in codes if c})

    for code, parts in existing_by_code(list(codes) + list(old_names) + [searched]).items():
        for info in parts:
            if labels.get(code):
                info = {**info, 'match': labels[code]}

            current = candidates.get(info['pk'])

            if current is None or MATCH_PRIORITY[info['match']] < MATCH_PRIORITY[current['match']]:
                candidates[info['pk']] = info

    return sorted(candidates.values(), key=lambda c: (MATCH_PRIORITY[c['match']], c['pk']))


# ---------------------------------------------------------------- preview


def parameter_sources_order(primary: str) -> list[str]:
    """Suppliers to use for parameters / category, best first (default DigiKey, Farnell, Mouser)."""
    order = [s.lower() for s in setting_list('PARAMETER_SOURCES')] or ['digikey', 'farnell', 'mouser']

    if primary not in order:
        order.append(primary)

    return order


def preview(supplier: str, sku: str, searched: str = '') -> dict:
    """Collect everything needed to create the part, and propose the values.

    Requests: the product, the same MPN on the better parameter source,
    the alternate suppliers, the DigiKey declarations. Everything is kept in
    the database for the import (save_preview), so the import itself sends no request.
    """
    api = require_pricing_api()
    configured = api.configured_suppliers()
    warnings = []

    primary = api.get_client(supplier).details(sku)
    sources = {supplier: primary}
    clients = {}

    def client(key):
        if key not in clients:
            clients[key] = api.get_client(key)
        return clients[key]

    def from_other_supplier(key):
        """Details of the same MPN on another supplier (None if not sold there)."""
        if key in sources:
            return sources[key]

        try:
            found = rules.preferred_result(client(key).find_by_mpn(primary.mpn), primary.mpn)
            sources[key] = client(key).details(found.sku) if found else None
        except api.QuotaExceededError as exc:
            warnings.append(str(exc))
            sources[key] = None
        except api.SupplierError as exc:
            warnings.append(f'{api.SUPPLIER_NAMES.get(key, key)}: {exc}')
            sources[key] = None

        return sources[key]

    # Parameters and category from the best source selling the same MPN
    parameter_source = primary

    for key in parameter_sources_order(supplier):
        if key == supplier:
            break

        if key in configured and primary.mpn:
            other = from_other_supplier(key)

            if other and other.parameters:
                parameter_source = other
                break

    # Alternate suppliers
    if setting('CREATE_ALTERNATES') and primary.mpn:
        for key in configured:
            if key != supplier:
                from_other_supplier(key)

    # Farnell answers in the language of its store (it.farnell.com: 'Capacità'): the
    # parameters are read from an English store, so that they match the mappings
    parameter_details = english_details(api, parameter_source, warnings)

    category = propose_category([parameter_details, primary] + [d for d in sources.values() if d])
    # Proposed by a mapping: otherwise the user is asked to remember the category chosen
    mapped = category is not None

    if category is None:
        # No mapping: the category named as the supplier one (created by the import if missing)
        category = supplier_category_fallback([parameter_details, primary])

    proposal = category_proposal(sources, supplier, parameter_details, category)

    declarations = []

    if setting('ATTACH_DECLARATIONS') and sources.get('digikey'):
        try:
            declarations = client('digikey').environmental_documents(sources['digikey'].sku)
        except api.SupplierError as exc:
            warnings.append(f'DigiKey declarations: {exc}')

    supplier_parts = [
        {
            'supplier': key,
            'supplier_name': api.SUPPLIER_NAMES.get(key, key),
            'company': company_info(api.supplier_company(key)),
            'sku': d.sku,
            'packaging': d.packaging,
            'product_url': d.product_url,
            'status': d.quote.status if d.quote else '',
            'discontinued': api.is_discontinued(d.quote.status if d.quote else ''),
            'stock': d.quote.stock if d.quote else None,
            'unit_price': str(d.quote.price_breaks[0].price) if d.quote and d.quote.price_breaks else '',
            'currency': d.quote.currency if d.quote else '',
            'primary': key == supplier,
            # Suppliers queried only for the parameters are not proposed when the alternates are off
            'include': key == supplier or (
                bool(setting('CREATE_ALTERNATES')) and not api.is_discontinued(d.quote.status if d.quote else '')
            ),
        }
        for key, d in sources.items()
        if d
    ]

    # Parts already using the MPN or one of the SKUs: the user can update them instead of creating a duplicate
    existing_codes = {
        'codes': [primary.mpn] + [d.sku for d in sources.values() if d],
        'old_names': [a for d in sources.values() if d for a in aliases(d)],
        'searched': searched,
    }
    existing_parts = existing_candidates(**existing_codes)

    token = save_preview({
        'sources': sources,
        'declarations': declarations,
        'primary': supplier,
        'parameter_source': parameter_source.supplier,
        'parameter_details': parameter_details,
        'existing_codes': existing_codes,
    })

    return {
        'token': token,
        'part': {
            'name': primary.mpn or primary.sku,
            # The Mouser descriptions are generic: the parameter source one first
            'description': (parameter_source.description or primary.description)[:250],
            'IPN': proposal['IPN'],
            'revision': setting('DEFAULT_REVISION') or '',
            'category': proposal['category'],
            'category_path': proposal['category_path'],
            'new_category': proposal['new_category'],
            'image_url': primary.image_url or parameter_source.image_url,
            'datasheet_url': rules.normalize_url(primary.datasheet_url or parameter_source.datasheet_url),
        },
        'manufacturer': primary.manufacturer or parameter_source.manufacturer,
        'mpn': primary.mpn,
        'parameter_source': parameter_source.supplier,
        'supplier_category': parameter_source.category,
        'category_proposed': mapped,
        'parameters': proposal['parameters'],
        'supplier_parts': supplier_parts,
        'declarations': [{**d, 'url': rules.normalize_url(d['url'])} for d in declarations if rules.normalize_url(d['url'])],
        'existing_part': existing_parts[0] if existing_parts else None,
        'existing_parts': existing_parts,
        'warnings': warnings,
    }


def english_details(api, details, warnings: list):
    """Farnell details from the store set in FARNELL_PARAMETER_STORE (one request), for the parameters."""
    store = (setting('FARNELL_PARAMETER_STORE') or '').strip()

    if details.supplier != 'farnell' or not store:
        return details

    try:
        client = api.get_client('farnell')

        if client.store == store:
            return details

        client.store = store
        english = client.details(details.sku)
    except api.SupplierError as exc:
        warnings.append(f'Farnell {store}: {exc} (parameters in the language of the price store)')
        return details

    # Only the parameters: prices, stock and links stay those of the price store
    details.parameters = english.parameters
    return details


def category_proposal(sources: dict, primary: str, details, category) -> dict:
    """Parameters and IPN for a category (no supplier request: data already fetched).

    category: PartCategory, PendingCategory (created by the import) or None.
    """
    details = details or sources[primary]
    category_path = category_names(category)
    pending = isinstance(category, PendingCategory)
    # The IPN of a new category follows the scheme of its closest existing parent
    ipn_category = category.existing_ancestor() if pending else category

    parameters = rules.map_parameters(
        rules.with_derived_parameters(details.parameters, details.mpn),
        rules.parameter_map(load_scopes(), category_path),
        category_path[0] if category_path else '',
        ignored=set(setting_list('IGNORED_PARAMETERS')),
        import_unmapped=bool(setting('IMPORT_UNMAPPED_PARAMETERS')),
    )

    # RoHS / REACH: first value in the order of conformita.py (DigiKey, Farnell, Mouser)
    by_priority = [sources.get(k) for k in ('digikey', 'farnell', 'mouser')] + [sources[primary]]
    rohs = next((d.rohs for d in by_priority if d and d.rohs), '')
    reach = next((d.reach for d in by_priority if d and d.reach), '')
    parameters = [p for p in parameters if p['template'] not in rules.COMPLIANCE_TEMPLATES] + \
        rules.compliance_parameters(rohs, reach)

    return {
        'category': None if pending or category is None else category.pk,
        'category_path': ' / '.join(category_path),
        # Category to create on import ('RF/Shield'), when the mapped one does not exist yet
        'new_category': category.pathstring if pending else '',
        'IPN': propose_ipn(ipn_category, details.parameters) if setting('PROPOSE_IPN') else '',
        'parameters': [preview_parameter(p) for p in parameters],
    }


def proposal_for_category(token: str, category_pk, new_category: str = '') -> dict:
    """Recompute parameters and IPN after the user changed the category.

    new_category: path of a category to create ('RF/Shield'), instead of category_pk
    """
    from part.models import PartCategory

    from . import kintree

    cached = load_preview(token)

    if new_category and not category_pk:
        path = category_path_names(new_category)

        if not path:
            raise ImportError_('Enter the path of the new category')

        # A path typed by the user may be an existing category
        category = kintree.find_category(path) or PendingCategory(path)
    else:
        category = PartCategory.objects.filter(pk=category_pk).first() if category_pk else None
    details = cached.get('parameter_details') or cached['sources'].get(cached['parameter_source'])
    return category_proposal(cached['sources'], cached['primary'], details, category)


def company_info(company):
    return {'pk': company.pk, 'name': company.name} if company else None


def load_scopes() -> dict:
    from .models import ParameterScope

    return {s.name: {'parents': s.parents or [], 'mappings': s.mappings or {}} for s in ParameterScope.objects.all()}


def preview_parameter(parameter: dict) -> dict:
    """A parameter as it will be saved: value in the units of the existing template."""
    from common.models import ParameterTemplate

    template = ParameterTemplate.objects.filter(name__iexact=parameter['template']).first()
    units = template.units if template else ''
    value = parameter['value']

    if template and not template.checkbox:
        value = to_template_units(value, units) or value

    return {**parameter, 'value': value, 'units': units, 'template_exists': template is not None, 'include': True}


def template_exists(name: str) -> bool:
    from common.models import ParameterTemplate

    return ParameterTemplate.objects.filter(name__iexact=name).exists()


class PendingCategory:
    """A proposed category which does not exist yet: created only when the import is confirmed."""

    def __init__(self, path):
        self.path = [str(name) for name in path if str(name).strip()]
        self.name = self.path[-1] if self.path else ''

    @property
    def pathstring(self) -> str:
        return '/'.join(self.path)

    def existing_ancestor(self):
        """The deepest existing category of the path (None if not even the first one exists)."""
        from . import kintree

        for size in range(len(self.path) - 1, 0, -1):
            category = kintree.find_category(self.path[:size])

            if category is not None:
                return category

        return None

    def __eq__(self, other):
        return isinstance(other, PendingCategory) and other.path == self.path

    def __repr__(self):
        return f'PendingCategory({self.pathstring!r})'


def category_path_names(path) -> list[str]:
    """'Semiconductors / TRIAC' -> ['Semiconductors', 'TRIAC'] (empty names removed)."""
    return [name.strip()[:100] for name in str(path or '').split('/') if name.strip()]


def supplier_category_fallback(details_list):
    """Category from the supplier category, when no mapping matches it.

    The supplier name of the deepest level ('TRIACs') is used: the existing category
    with that name, otherwise a new one (PendingCategory, created by the import) below
    the existing category named as one of the upper supplier levels, or at the root.
    Returns a PartCategory, a PendingCategory or None (no supplier category).
    """
    from part.models import PartCategory

    def unique(name):
        matches = list(PartCategory.objects.filter(name__iexact=name)[:2])
        return matches[0] if len(matches) == 1 else None

    for details in details_list:
        if not details or not details.category:
            continue

        # A '/' in a supplier name would be read as a level of the path
        names = [str(n).replace('/', '-').strip()[:100] for n in details.category if str(n).strip()]

        if not names:
            continue

        leaf = names[-1]
        existing = unique(leaf)

        if existing is not None:
            return existing

        for name in reversed(names[:-1]):
            parent = unique(name)

            if parent is not None:
                return PendingCategory([c.name for c in parent.get_ancestors(include_self=True)] + [leaf])

        return PendingCategory([leaf])

    return None


def category_names(category) -> list[str]:
    """Names of a category (existing or pending), from the root."""
    if category is None:
        return []

    if isinstance(category, PendingCategory):
        return list(category.path)

    return [c.name for c in category.get_ancestors(include_self=True)]


def mapping_target(mapping):
    """Category of a mapping: the existing one, or a PendingCategory.

    A pending mapping whose category exists now (created by hand) is linked to it.
    """
    from . import kintree

    if mapping.category_id:
        return mapping.category

    category = kintree.find_category(mapping.path)

    if category is None:
        return PendingCategory(mapping.path)

    kintree.resolve_pending()
    return category


def parent_category(category):
    """Parent of an existing or pending category (itself at the root)."""
    from . import kintree

    if isinstance(category, PendingCategory):
        if len(category.path) < 2:
            return category

        return kintree.find_category(category.path[:-1]) or PendingCategory(category.path[:-1])

    return category.parent or category


def propose_category(details_list):
    """Category from the mappings of the supplier categories (deepest level first).

    Mappings 'filtered by function' (Ki-nTree '__' subcategories) are chosen by
    the 'Function Type' parameter; if none matches, their parent category is used.
    Returns a PartCategory, a PendingCategory (mapped, but not created yet) or None.
    """
    from .models import CategoryMapping

    mappings = list(CategoryMapping.objects.select_related('category'))
    scopes = None

    for details in details_list:
        if not details:
            continue

        candidates = [m for m in mappings if m.supplier in ('', details.supplier)]

        # Exact names first (deepest level first), then the old DigiKey v3 names
        levels = [
            [m for m in candidates if m.supplier_category.lower() == name.lower()]
            for name in reversed(details.category)
        ]
        levels.append(sorted(
            (m for m in candidates if rules.legacy_category_match(m.supplier_category, details.category)),
            key=lambda m: -len(m.supplier_category),
        ))

        for matching in levels:
            if matching:
                # Only the mappings of the most specific supplier category
                matching = [m for m in matching if m.supplier_category == matching[0].supplier_category]

            plain = [m for m in matching if not m.function_filter]

            if plain:
                return mapping_target(plain[0])

            if matching:
                scopes = scopes if scopes is not None else load_scopes()

                for mapping in matching:
                    path = mapping.path
                    values = rules.function_values(
                        rules.with_derived_parameters(details.parameters), rules.parameter_map(scopes, path)
                    )

                    if path and rules.function_match(path[-1], values):
                        return mapping_target(mapping)

                # No function match: the common parent category (as Ki-nTree)
                return parent_category(mapping_target(matching[0]))

    return None


def propose_ipn(category, parameters: dict) -> str:
    """Next free IPN for the category (scheme: type prefix + mounting digit + '-' + NNNN, e.g. PE061-0012)."""
    from part.models import Part

    if category is None:
        return ''

    digits = int(setting('IPN_DIGITS') or 4)
    mounting = rules.mounting_digit(parameters)

    # IPNs of the category and its subcategories; if there are none, of the parent categories
    for node in [category] + list(category.get_ancestors(ascending=True)):
        ipns = list(
            Part.objects.filter(category__in=node.get_descendants(include_self=True))
            .exclude(IPN__isnull=True)
            .exclude(IPN='')
            .values_list('IPN', flat=True)
        )
        prefix = rules.ipn_prefix(ipns, mounting)

        if prefix:
            stem = f'{prefix[0]}{prefix[1]}-'
            used = Part.objects.filter(IPN__startswith=stem).values_list('IPN', flat=True)
            return rules.next_ipn(prefix[0], prefix[1], list(used), digits)

    return ''


# ---------------------------------------------------------------- import


def create(data: dict, user=None) -> dict:
    """Create (or complete) the part from a preview edited by the user."""
    from company.models import ManufacturerPart, SupplierPart
    from part.models import Part, PartCategory

    api = require_pricing_api()
    cached = load_preview(data.get('token'))
    sources = cached['sources']
    warnings = []
    form = data.get('part') or {}
    existing_pk = data.get('existing_part')

    selected = [sp for sp in data.get('supplier_parts') or [] if sp.get('include') and sources.get(sp.get('supplier'))]

    # Supplier parts are optional: an existing part can get only parameters and attachments,
    # a new part can be created using the supplier data only as information
    primary = sources[selected[0]['supplier']] if selected else sources[cached['primary']]
    mpn = data.get('mpn') or primary.mpn

    with transaction.atomic():
        if existing_pk:
            part = Part.objects.get(pk=existing_pk)
            created = False
        else:
            # Never create a duplicate by mistake: the user must ask for it explicitly
            codes = cached.get('existing_codes') or {}
            duplicates = existing_candidates(
                [mpn, primary.sku] + [sources[sp['supplier']].sku for sp in selected] + codes.get('codes', []),
                codes.get('old_names', []),
                codes.get('searched', ''),
            )

            if duplicates and not data.get('create_duplicate'):
                names = ', '.join(f'{d["IPN"] or "-"} {d["name"]} ({d["match"]})' for d in duplicates[:5])
                raise ImportError_(f'The product is already in InvenTree: {names}. Update that part instead')

            category = PartCategory.objects.filter(pk=form.get('category')).first() if form.get('category') else None

            if category is None and form.get('new_category'):
                # Category proposed by a pending mapping: created now, with the missing parents
                from . import kintree

                path = category_path_names(form['new_category'])
                existed = kintree.find_category(path) if path else None
                category = existed or (kintree.find_category(path, create=True) if path else None)

                if category is not None and existed is None:
                    warnings.append(f'Category "{category.pathstring}" created')
                    kintree.resolve_pending()

            if category is None:
                raise ImportError_('Select the category')

            ipn = (form.get('IPN') or '').strip() or None

            if ipn and Part.objects.filter(IPN=ipn).exists():
                raise ImportError_(f'IPN {ipn} is already used')

            part = Part(
                name=(form.get('name') or mpn)[:100],
                description=(form.get('description') or '')[:250],
                IPN=ipn,
                revision=(form.get('revision') or '')[:100] or None,
                category=category,
                component=True,
                purchaseable=True,
                active=True,
            )
            part.full_clean()
            part.save()
            created = True

            if data.get('remember_category'):
                remember_category(sources.get(cached.get('parameter_source')), category)

        manufacturer_part = None

        if mpn:
            manufacturer_part = next(
                (m for m in ManufacturerPart.objects.filter(part=part) if rules.norm_code(m.MPN) == rules.norm_code(mpn)),
                None,
            )

            if manufacturer_part is None:
                manufacturer_part = ManufacturerPart.objects.create(
                    part=part,
                    manufacturer=get_manufacturer(data.get('manufacturer') or primary.manufacturer),
                    MPN=mpn,
                    description=(primary.description or '')[:250],
                    link=primary.product_url or '',
                )

        supplier_parts = []

        for item in selected:
            details = sources[item['supplier']]
            company = api.supplier_company(item['supplier'])

            if company is None:
                warnings.append(f'{item["supplier"]}: no supplier company in InvenTree')
                continue

            if SupplierPart.objects.filter(supplier=company, SKU__iexact=details.sku).exists():
                warnings.append(f'{company.name} {details.sku}: supplier part already exists')
                continue

            sp = SupplierPart.objects.create(
                part=part,
                supplier=company,
                manufacturer_part=manufacturer_part,
                SKU=details.sku,
                link=details.product_url or '',
                description=(details.description or '')[:250],
                packaging=(details.packaging or '')[:50],
            )
            supplier_parts.append(sp)

        if created:
            warnings += save_parameters(part, data.get('parameters') or [])
        elif data.get('add_missing_parameters'):
            # Existing part: the parameters it does not have yet, and the ones the user chose to replace
            warnings += save_parameters(part, data.get('parameters') or [], only_missing=True)

        attachments = []

        if data.get('attach_datasheet', True):
            url = rules.normalize_url(form.get('datasheet_url') or '')

            if url and attach_link(part, url, rules.DATASHEET_COMMENT, unique_comment=True):
                attachments.append('Datasheet')

        if data.get('attach_declarations', True):
            for doc in cached.get('declarations') or []:
                url = rules.normalize_url(doc['url'])

                if url and attach_link(part, url, f'{rules.DECLARATION_COMMENT} - {doc["title"]}'[:250]):
                    attachments.append(doc['title'])

    # Prices, stock and history: through the pricing plugin (outside the transaction:
    # the pricing update of the part is scheduled by InvenTree)
    for sp in supplier_parts:
        details = sources[api.supplier_key_for_company(sp.supplier)]

        if details.quote:
            try:
                api.apply_quote(sp, details.quote)
            except Exception as exc:  # pragma: no cover - logged, not fatal
                logger.exception('SupplierPartImport: prices of %s', sp)
                warnings.append(f'{sp.SKU}: prices not saved ({exc})')

    if data.get('download_image', True) and form.get('image_url') and (created or not part.image):
        # The image chosen in the form first, then the ones of the other suppliers
        candidates = [('', form['image_url'])] + [
            (api.SUPPLIER_NAMES.get(key, key), details.image_url)
            for key, details in sources.items()
            if details and details.image_url
        ]
        warnings += save_image(part, candidates)

    return {
        'pk': part.pk,
        'created': created,
        'url': f'/web/part/{part.pk}/',
        'supplier_parts': [f'{sp.supplier.name} {sp.SKU}' for sp in supplier_parts],
        'attachments': attachments,
        'warnings': warnings,
    }


def remember_category(details, category):
    """Map the supplier category of the product to the category chosen by the user."""
    from .models import CategoryMapping

    if details is None or not details.category:
        return

    supplier_category = details.category[-1][:250]
    existing = CategoryMapping.objects.filter(supplier=details.supplier, supplier_category=supplier_category)

    # Filtered mappings (several subcategories) are managed in the settings, not replaced
    if existing.filter(function_filter=True).exists():
        return

    existing.exclude(category=category).delete()
    CategoryMapping.objects.get_or_create(
        supplier=details.supplier, supplier_category=supplier_category, category=category
    )


def get_manufacturer(name: str):
    """Manufacturer company by name (case insensitive), created if missing."""
    from company.models import Company

    name = (name or '').strip() or 'Unknown'
    company = Company.objects.filter(name__iexact=name).first()

    if company is None:
        company = Company.objects.create(name=name, is_manufacturer=True, is_supplier=False, is_customer=False)
    elif not company.is_manufacturer:
        company.is_manufacturer = True
        company.save()

    return company


def save_parameters(part, parameters: list[dict], only_missing: bool = False) -> list[str]:
    """Create the selected parameters; templates are created when missing.

    Arguments:
        only_missing: skip the templates the part already has, unless the item asks to
            replace the value ('overwrite': e.g. a '-' placeholder)

    Returns the warnings; the replaced values are reported too.
    """
    from django.contrib.contenttypes.models import ContentType

    from common.models import Parameter, ParameterTemplate

    from .models import TemplateUnit

    warnings = []
    content_type = ContentType.objects.get_for_model(part.__class__)
    units = {u.name.lower(): u.units for u in TemplateUnit.objects.all()}

    for item in parameters:
        name = (item.get('template') or '').strip()
        value = str(item.get('value') or '').strip()

        if not item.get('include', True) or not name or not value:
            continue

        template = ParameterTemplate.objects.filter(name__iexact=name).first()

        existing = (
            Parameter.objects.filter(model_type=content_type, model_id=part.pk, template=template).first()
            if template else None
        )

        if existing is not None and not item.get('overwrite'):
            if not only_missing:
                warnings.append(f'Parameter "{name}" already set: not changed')
            continue

        if template is None:
            template = ParameterTemplate.objects.create(
                name=name[:100],
                units=units.get(name.lower(), '') or '',
                description=rules.COMPLIANCE_TEMPLATES.get(name, '')[:250],
                checkbox=name == rules.ROHS_CHECKBOX_TEMPLATE,
            )

        # Number in the template units ('25V' with units 'V' -> '25'): InvenTree shows the units apart
        converted = to_template_units(value, template.units)

        if converted is None and template.units:
            warnings.append(f'Parameter "{name}" = "{value}": not convertible to {template.units}, saved as is')

        if existing is not None:
            if existing.data == (converted or value):
                continue

            parameter = existing
            old_value = parameter.data
            parameter.data = converted or value
        else:
            parameter = Parameter(model_type=content_type, model_id=part.pk, template=template, data=converted or value)
            old_value = None

        try:
            with transaction.atomic():
                parameter.full_clean()
                parameter.save()

            if old_value is not None:
                warnings.append(f'Parameter "{name}": "{old_value}" replaced with "{parameter.data}"')
        except ValidationError as exc:
            # e.g. a value not convertible to the template units
            message = '; '.join(m for msgs in exc.message_dict.values() for m in msgs) if hasattr(exc, 'message_dict') else str(exc)
            warnings.append(f'Parameter "{name}" = "{value}" not saved: {message}')

    return warnings


def attach_link(part, url: str, comment: str, unique_comment: bool = False) -> bool:
    """Add a link attachment, unless the same link (or comment) is already there."""
    from common.models import Attachment

    existing = Attachment.objects.filter(model_type='part', model_id=part.pk)

    if existing.filter(link=url).exists():
        return False

    if unique_comment and existing.filter(comment__iexact=comment).exists():
        return False

    part.create_attachment(link=url, comment=comment)
    return True


IMAGE_TIMEOUT = 10  # seconds for each download attempt


def _is_timeout(exc: Exception) -> bool:
    import requests

    return isinstance(exc, requests.exceptions.Timeout) or 'timed out' in str(exc).lower() or 'timeout' in str(exc).lower()


def download_image(url: str):
    """The image at url; a timeout is retried once (the supplier CDNs are sometimes slow)."""
    from InvenTree.helpers_model import download_image_from_url

    try:
        return download_image_from_url(url, timeout=IMAGE_TIMEOUT)
    except Exception as exc:  # only a timeout is retried
        if not _is_timeout(exc):
            raise

        logger.info('SupplierPartImport: image %s timed out, retrying', url)
        return download_image_from_url(url, timeout=IMAGE_TIMEOUT)


def save_image(part, candidates) -> list[str]:
    """Download the product image (failures are only warnings).

    candidates: an URL, or a list of (supplier name, URL) tried in order: the image of
    the form first, then the ones of the other suppliers when a download fails.
    """
    import io

    from django.core.files.base import ContentFile

    if isinstance(candidates, str):
        candidates = [('', candidates)]

    tried, errors = set(), []

    for supplier, url in candidates:
        if not url or url in tried:
            continue

        tried.add(url)

        try:
            image = download_image(url)
            fmt = (image.format or 'PNG').lower()
            buffer = io.BytesIO()
            image.save(buffer, format=image.format or 'PNG')
            part.image.save(f'part_{part.pk}_image.{fmt}', ContentFile(buffer.getvalue()), save=True)
        except Exception as exc:  # noqa: BLE001 - network / image format errors: only a warning
            errors.append(f'{supplier or "selected image"}: {exc}')
            continue

        if errors:
            return [f'Image taken from {supplier} ({"; ".join(errors)})']

        return []

    return [f'Image not downloaded: {"; ".join(errors)}'] if errors else []
