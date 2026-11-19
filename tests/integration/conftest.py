"""Integration tests on a real InvenTree (the CI uses the inventree/inventree:stable image).

Environment:
    INVENTREE_SRC                    path of src/backend/InvenTree
    INVENTREE_DB_ENGINE / _DB_NAME   a migrated database (sqlite is enough)
    INVENTREE_PLUGINS_ENABLED=True, INVENTREE_PLUGIN_TESTING=True, INVENTREE_PLUGIN_TESTING_SETUP=True

The Part Pricing Update plugin must be installed too. Every test runs in a transaction
which is rolled back: the database is left as it was.
"""

import os
import sys

import pytest

sys.path.insert(0, os.environ['INVENTREE_SRC'])
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'InvenTree.settings')

from InvenTree.wsgi import application  # noqa: F401 - loads Django and the plugin registry


@pytest.fixture(autouse=True)
def rollback():
    from django.db import transaction

    with transaction.atomic():
        yield
        transaction.set_rollback(True)


@pytest.fixture
def plugin():
    from supplier_part_import import importer

    p = importer.get_plugin()
    assert p is not None, 'The SupplierPartImport plugin is not loaded'
    return p


@pytest.fixture
def clean_mappings():
    """Start from empty mapping tables and without categories (fresh installation)."""
    from part.models import Part, PartCategory

    from supplier_part_import.models import CategoryMapping, ParameterScope, TemplateUnit

    CategoryMapping.objects.all().delete()
    ParameterScope.objects.all().delete()
    TemplateUnit.objects.all().delete()
    Part.objects.all().update(category=None)
    PartCategory.objects.all().delete()


def details(supplier, category=(), **parameters):
    """A product as returned by the pricing plugin clients (no request)."""
    from part_pricing_update.suppliers.base import ProductDetails

    return ProductDetails(supplier=supplier, sku=f'{supplier}-SKU', category=list(category), parameters=parameters)
