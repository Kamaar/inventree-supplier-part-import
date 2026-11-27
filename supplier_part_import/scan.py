"""Create a part from a scanned supplier SKU (web or InvenTree app).

A label scanned in InvenTree whose 'P/N' barcode ('637-SMZ24') is not a supplier part yet:
with CREATE_ON_SCAN enabled the product is fetched from the supplier and imported with the
same rules as the import page, then the app opens the new part.

The import runs in the background worker (the supplier requests take 10-20 s, the app gives
up after ~20 s): the scan waits a few seconds for it, otherwise the user scans again.

The app cannot show a form for a plugin, so the import uses the proposed values:
the category of the mappings, otherwise the fallback category of SCAN_CATEGORY (created
when missing); an existing part with the same codes gets only the missing supplier parts
and parameters.
"""

import logging
import re

from . import importer

logger = logging.getLogger('inventree')

DEFAULT_SCAN_CATEGORY = 'To classify'

# Supplier of a SKU, from its format (only these are looked up: no request for other barcodes)
SKU_PATTERNS = (
    ('digikey', re.compile(r'^[A-Z0-9][A-Z0-9.,/+#()_-]*-ND$', re.IGNORECASE)),
    ('farnell', re.compile(r'^\d{6,8}$')),
    ('mouser', re.compile(r'^\d{2,4}-[A-Z0-9][A-Z0-9.,/+#()_-]*$', re.IGNORECASE)),
)


def supplier_for_sku(text: str, configured) -> str | None:
    """'637-SMZ24' -> 'mouser', '296-1234-ND' -> 'digikey', '1234567' -> 'farnell' (if configured)."""
    for key, pattern in SKU_PATTERNS:
        if key in configured and pattern.match(text):
            return key

    return None


def can_create_parts(user) -> bool:
    return bool(user and user.is_authenticated and (user.is_superuser or user.has_perm('part.add_part')))


def fallback_category():
    """The category of SCAN_CATEGORY ('To classify' by default; a path 'A/B' is allowed), created when missing."""
    from . import kintree

    path = importer.category_path_names(importer.setting('SCAN_CATEGORY') or DEFAULT_SCAN_CATEGORY)
    path = path or [DEFAULT_SCAN_CATEGORY]
    return kintree.find_category(path) or kintree.find_category(path, create=True)


def import_data(proposal: dict) -> dict:
    """The data of the import page, with the proposed values (no user editing)."""
    from part.models import PartCategory

    existing = proposal.get('existing_part')
    part = dict(proposal['part'])
    category = PartCategory.objects.filter(pk=part.get('category')).first() if part.get('category') else None

    if existing is None and category is None:
        # No mapped category (or only a pending one): the fallback category, never a new tree
        category = fallback_category()
        part['IPN'] = ''

    part['category'] = category.pk if category is not None else None
    part['new_category'] = ''

    return {
        'token': proposal['token'],
        'part': part,
        'manufacturer': proposal.get('manufacturer'),
        'mpn': proposal.get('mpn'),
        # Existing part: only the parameters it does not have yet
        'parameters': [{**p, 'include': True, 'overwrite': False} for p in proposal.get('parameters') or []],
        'supplier_parts': proposal.get('supplier_parts') or [],
        'existing_part': existing['pk'] if existing else None,
        'create_duplicate': False,
        'add_missing_parameters': True,
        'remember_category': False,
        'download_image': True,
        'attach_datasheet': True,
        'attach_declarations': bool(importer.setting('ATTACH_DECLARATIONS')),
    }


class ScanPending(Exception):
    """The import of the scanned code is still running in the worker: the user scans again."""


SCAN_TASK = 'supplier_part_import.scan.import_scanned_sku'
SCAN_WAIT = 12  # seconds the scan request waits for the import (the app gives up after ~20 s)
POLL_INTERVAL = 0.5


def scanned_code(barcode_data) -> str | None:
    text = str(barcode_data or '').strip()

    if not text or len(text) > 60 or any(ord(c) < 32 or c == ' ' for c in text):
        return None

    return text


def import_scanned_sku(text: str, supplier: str, user_pk=None) -> int | None:
    """Worker task: import the product of the scanned SKU; the pk of the part, None if not imported."""
    from company.models import SupplierPart
    from django.contrib.auth import get_user_model

    if SupplierPart.objects.filter(SKU__iexact=text).exists():
        return None  # a previous scan imported it already

    try:
        proposal = importer.preview(supplier, text)
    except Exception as exc:  # noqa: BLE001 - not found, quota, network: the scan is just not matched
        logger.info('SupplierPartImport: scan %s not imported (%s)', text, exc)
        return None

    primary = next((sp for sp in proposal['supplier_parts'] if sp['supplier'] == supplier), None)

    # Only the exact product: a supplier may return another one for a partial code
    if primary is None or str(primary['sku']).strip().lower() != text.lower():
        logger.info('SupplierPartImport: scan %s not imported (the supplier returned another product)', text)
        return None

    user = get_user_model().objects.filter(pk=user_pk).first() if user_pk else None
    result = importer.create(import_data(proposal), user=user)

    for warning in result['warnings']:
        logger.warning('SupplierPartImport: scan %s: %s', text, warning)

    return result['pk']


def task_state(task_id) -> tuple[bool, bool, object]:
    """(finished, success, result) of an offloaded task."""
    from django_q.models import Task

    task = Task.objects.filter(id=task_id).first()

    if task is None:
        return False, False, None

    return True, bool(task.success), task.result


def create_from_scan(barcode_data, user) -> tuple | None:
    """(part, message) when the scan is a supplier SKU which was imported, otherwise None.

    The import (several supplier requests, 10-20 s) runs in the background worker; the
    request waits SCAN_WAIT seconds for it, then raises ScanPending: the app shows the
    message and the user scans again (the next scan finds the part).
    """
    import time

    from company.models import SupplierPart
    from InvenTree.tasks import offload_task
    from part.models import Part

    if not importer.setting('CREATE_ON_SCAN') or not can_create_parts(user):
        return None

    text = scanned_code(barcode_data)

    if text is None:
        return None

    # Already in InvenTree: opened by the pricing plugin (SKU or customer part number lookup)
    if SupplierPart.objects.filter(SKU__iexact=text).exists() or Part.objects.filter(IPN__iexact=text).exists():
        return None

    api = importer.pricing_api()

    if api is None:
        return None

    supplier = supplier_for_sku(text, api.configured_suppliers())

    if supplier is None:
        return None

    name = api.SUPPLIER_NAMES.get(supplier, supplier)
    # Without a running worker the task runs here, synchronously (True is returned)
    task_id = offload_task(SCAN_TASK, text, supplier, getattr(user, 'pk', None))

    if task_id is False:
        raise RuntimeError('the import task could not be started')

    deadline = time.monotonic() + SCAN_WAIT

    while True:
        sp = SupplierPart.objects.filter(SKU__iexact=text).select_related('part', 'part__category').first()

        if sp is not None:
            part = sp.part
            message = f'Part imported from {name} {text}'

            if part.category is not None:
                message += f' (category {part.category.pathstring})'

            return part, message

        if task_id is True:
            return None  # ran synchronously, nothing imported

        finished, success, result = task_state(task_id)

        if finished:
            if not success:
                raise RuntimeError(str(result))

            return None  # not imported (not found, another product)

        if time.monotonic() >= deadline:
            raise ScanPending(f'{name} {text}: import in progress, scan again in a few seconds')

        time.sleep(POLL_INTERVAL)
