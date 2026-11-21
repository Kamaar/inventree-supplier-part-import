"""Import of the Ki-nTree configuration files into the mapping tables.

Files (from ~/kintree/user on the PC where Ki-nTree runs):
- <supplier>_categories.yaml   InvenTree category -> subcategory -> [supplier categories]
- supplier_parameters.yaml     scope -> {template: [supplier names], parent: [scopes]}
- parameters.yaml              template -> units
- categories.yaml              InvenTree category tree (used only to create the missing categories)

The plugin ships the Ki-nTree default files (folder 'defaults'): they are loaded
automatically on the first use, when the mapping tables are empty.
"""

from pathlib import Path

import yaml

DEFAULTS_DIR = Path(__file__).parent / 'defaults'

SUPPLIER_FILES = {
    'digikey_categories.yaml': 'digikey',
    'mouser_categories.yaml': 'mouser',
    'element14_categories.yaml': 'farnell',
    'farnell_categories.yaml': 'farnell',
}


def find_category(path: list[str], create: bool = False):
    """InvenTree category from its path of names (None if it does not exist).

    create: create the missing categories of the path
    """
    from part.models import PartCategory

    # Case insensitive: a path typed by the user ('rf/shield') must not duplicate 'RF/Shield'
    category = PartCategory.objects.filter(pathstring__iexact='/'.join(path)).first()

    if category is not None or not create:
        return category

    parent = find_category(path[:-1], create=True) if len(path) > 1 else None
    return PartCategory.objects.create(name=path[-1], parent=parent)


def category_tree(data: dict, path=()) -> list[list[str]]:
    """Paths of the categories.yaml tree ({Category: {Sub: null}})."""
    paths = []

    for name, value in (data or {}).items():
        current = [*path, str(name)]
        paths.append(current)

        if isinstance(value, dict):
            paths += category_tree(value, current)

    return paths


def category_entries(data: dict, path=()) -> list[tuple[list[str], str]]:
    """Flatten {Category: {Sub: [supplier names]}} into [(InvenTree path, supplier name)]."""
    entries = []

    for name, value in (data or {}).items():
        current = [*path, str(name)]

        if isinstance(value, dict):
            entries += category_entries(value, current)
        elif isinstance(value, list):
            entries += [(current, str(item)) for item in value if item]
        elif value:
            entries.append((current, str(value)))

    return entries


def import_files(
    files: dict[str, str], replace: bool = False, only_missing: bool = False, create_categories: bool = False
) -> dict:
    """Import the given Ki-nTree files ({file name: YAML text}).

    Arguments:
        replace: delete the existing rows of the imported tables first
        only_missing: keep the existing scopes and units as they are (they may have been edited)
        create_categories: create the InvenTree categories which do not exist
            (from categories.yaml and from the category mappings)
    """
    from django.db import transaction

    from . import rules
    from .models import CategoryMapping, ParameterScope, TemplateUnit

    summary = {
        'categories': 0,
        'categories_missing': [],
        'categories_created': 0,
        'categories_pending': 0,
        'scopes': 0,
        'units': 0,
        'ignored_files': [],
    }

    def category_for(path):
        if not create_categories:
            return find_category(path)

        missing = sum(1 for i in range(1, len(path) + 1) if find_category(path[:i]) is None)
        summary['categories_created'] += missing
        return find_category(path, create=True)

    with transaction.atomic():
        for filename, text in files.items():
            name = filename.replace('\\', '/').split('/')[-1].lower()
            data = yaml.safe_load(text) or {}

            if name in SUPPLIER_FILES:
                supplier = SUPPLIER_FILES[name]

                if replace:
                    CategoryMapping.objects.filter(supplier=supplier).delete()

                for path, supplier_category in category_entries(data):
                    # '__N-Channel FET': subcategory chosen by the 'Function Type' parameter
                    function_filter = path[-1].startswith(rules.FUNCTION_FILTER_KEY)
                    path = [*path[:-1], path[-1].removeprefix(rules.FUNCTION_FILTER_KEY)]
                    category = category_for(path)

                    if category is None:
                        # Pending mapping: the category is created on the first import which uses it
                        summary['categories_missing'].append('/'.join(path))
                        summary['categories_pending'] += 1
                        CategoryMapping.objects.update_or_create(
                            supplier=supplier,
                            supplier_category=supplier_category,
                            category=None,
                            category_path='/'.join(path)[:250],
                            defaults={'function_filter': function_filter},
                        )
                        continue

                    CategoryMapping.objects.update_or_create(
                        supplier=supplier,
                        supplier_category=supplier_category,
                        category=category,
                        defaults={'function_filter': function_filter},
                    )
                    summary['categories'] += 1

            elif name.startswith('supplier_parameters') and name.endswith(('.yaml', '.yml')):
                if replace:
                    ParameterScope.objects.all().delete()

                for scope, content in data.items():
                    if only_missing and ParameterScope.objects.filter(name=str(scope)).exists():
                        continue

                    content = dict(content or {})
                    parents = content.pop('parent', None) or []
                    mappings = {str(k): [str(n) for n in (v or [])] for k, v in content.items()}
                    ParameterScope.objects.update_or_create(
                        name=str(scope), defaults={'parents': [str(p) for p in parents], 'mappings': mappings}
                    )
                    summary['scopes'] += 1

            elif name == 'parameters.yaml':
                if replace:
                    TemplateUnit.objects.all().delete()

                for template, units in data.items():
                    if only_missing and TemplateUnit.objects.filter(name=str(template)[:100]).exists():
                        continue

                    TemplateUnit.objects.update_or_create(
                        name=str(template)[:100], defaults={'units': fix_encoding(str(units or ''))[:25]}
                    )
                    summary['units'] += 1

            elif name == 'categories.yaml':
                # Only the tree is used, to create the categories (not the IPN 'CODES')
                if create_categories:
                    for path in category_tree(data.get('CATEGORIES') or {}):
                        category_for(path)

            else:
                summary['ignored_files'].append(filename)

    resolve_pending()
    summary['categories_missing'] = sorted(set(summary['categories_missing']))
    return summary


def resolve_pending() -> int:
    """Link the pending mappings whose category exists now (e.g. created by hand)."""
    from .models import CategoryMapping

    linked = 0

    for mapping in CategoryMapping.objects.filter(category__isnull=True):
        category = find_category(mapping.path)

        if category is None:
            continue

        # The same mapping may already exist for the category
        if CategoryMapping.objects.filter(
            supplier=mapping.supplier, supplier_category=mapping.supplier_category, category=category
        ).exists():
            mapping.delete()
        else:
            mapping.category = category
            mapping.category_path = ''
            mapping.save()

        linked += 1

    return linked


def default_files() -> dict[str, str]:
    """The Ki-nTree default files shipped with the plugin ({file name: YAML text})."""
    # categories.yaml first: the categories must exist before the category mappings
    names = sorted(p.name for p in DEFAULTS_DIR.glob('*.yaml'))
    names.sort(key=lambda n: n != 'categories.yaml')
    return {name: (DEFAULTS_DIR / name).read_text(encoding='utf-8') for name in names}


def load_defaults(replace: bool = False, create_categories: bool = False) -> dict:
    """Load the default mappings. Without replace, the existing (edited) rows are kept."""
    return import_files(default_files(), replace=replace, only_missing=not replace, create_categories=create_categories)


def ensure_defaults(plugin) -> dict | None:
    """First use: load the default mappings when the tables are empty (only once).

    An existing configuration (e.g. imported from Ki-nTree) is never changed, and the
    categories are not created: the missing ones can be created from the settings page.
    """
    from .models import CategoryMapping, ParameterScope, TemplateUnit

    if plugin is None or plugin.get_setting('DEFAULT_MAPPINGS_CHECKED'):
        return None

    summary = None

    if not (CategoryMapping.objects.exists() or ParameterScope.objects.exists() or TemplateUnit.objects.exists()):
        summary = load_defaults()

    plugin.set_setting('DEFAULT_MAPPINGS_CHECKED', True)
    return summary


def fix_encoding(text: str) -> str:
    """Ki-nTree wrote some units as UTF-8 read as Latin-1 ('mÎ©' for 'mΩ')."""
    try:
        return text.encode('latin-1').decode('utf-8')
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text
