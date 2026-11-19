"""API views for the SupplierPartImport plugin, served below /plugin/supplier-part-import/."""

import yaml
from django.core.exceptions import ValidationError

from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from . import importer, kintree


class CanImportParts(permissions.BasePermission):
    """Authenticated users can search, part creators (or staff) can import."""

    def has_permission(self, request, view):
        user = request.user

        if not user or not user.is_authenticated:
            return False

        if request.method in permissions.SAFE_METHODS and not getattr(view, 'write_only', False):
            return True

        return user.is_staff or user.has_perm('part.add_part')


def error_response(exc, status=400):
    if isinstance(exc, ValidationError) and hasattr(exc, 'message_dict'):
        message = '; '.join(f'{k}: {", ".join(v)}' for k, v in exc.message_dict.items())
    else:
        message = str(exc)

    return Response({'error': message}, status=status)


def supplier_errors():
    """Exceptions raised by the supplier clients (empty tuple without the pricing plugin)."""
    api = importer.pricing_api()
    return (api.SupplierError,) if api else ()


class StatusView(APIView):
    """GET: plugin availability, configured suppliers and their remaining requests."""

    permission_classes = [CanImportParts]

    def get(self, request, *args, **kwargs):
        api = importer.pricing_api()
        kintree.ensure_defaults(importer.get_plugin())

        if api is None:
            return Response({
                'available': False,
                'error': 'The "Part Pricing Update" plugin is required: install and activate it',
            })

        return Response({
            'available': True,
            'suppliers': [
                {
                    'key': key,
                    'name': api.SUPPLIER_NAMES.get(key, key),
                    'remaining_requests': api.remaining_requests(key),
                    'company': importer.company_info(api.supplier_company(key)),
                }
                for key in api.configured_suppliers()
            ],
            'propose_ipn': bool(importer.setting('PROPOSE_IPN')),
        })


class SearchView(APIView):
    """GET ?supplier=digikey&q=NE555P: keyword search on one supplier (supplier=all: on every supplier)."""

    permission_classes = [CanImportParts]

    def get(self, request, *args, **kwargs):
        try:
            data = importer.search(request.query_params.get('supplier', ''), request.query_params.get('q', ''))
        except (importer.ImportError_, *supplier_errors()) as exc:
            return error_response(exc)

        return Response(data)


class PreviewView(APIView):
    """GET ?supplier=digikey&sku=296-NE555P-ND: proposed part, parameters and supplier parts."""

    permission_classes = [CanImportParts]

    def get(self, request, *args, **kwargs):
        try:
            data = importer.preview(
                request.query_params.get('supplier', ''),
                request.query_params.get('sku', ''),
                request.query_params.get('q', ''),
            )
        except (importer.ImportError_, *supplier_errors()) as exc:
            return error_response(exc)

        return Response(data)


class ProposalView(APIView):
    """GET ?token=...&category=12: parameters and IPN for the category chosen by the user."""

    permission_classes = [CanImportParts]

    def get(self, request, *args, **kwargs):
        try:
            data = importer.proposal_for_category(
                request.query_params.get('token', ''),
                request.query_params.get('category'),
                request.query_params.get('new_category', ''),
            )
        except importer.ImportError_ as exc:
            return error_response(exc)

        return Response(data)


class ImportView(APIView):
    """POST the edited preview: create the part (or add the supplier parts to an existing one)."""

    permission_classes = [CanImportParts]
    write_only = True

    def post(self, request, *args, **kwargs):
        try:
            result = importer.create(request.data, user=request.user)
        except (importer.ImportError_, ValidationError, *supplier_errors()) as exc:
            return error_response(exc)

        return Response(result, status=201)


class MappingsView(APIView):
    """GET: the mapping tables. POST: import Ki-nTree files ({'files': {name: yaml}, 'replace': bool})."""

    permission_classes = [permissions.IsAdminUser]

    def get(self, request, *args, **kwargs):
        from .models import CategoryMapping, ParameterScope, TemplateUnit

        kintree.ensure_defaults(importer.get_plugin())
        kintree.resolve_pending()

        return Response({
            'categories': [
                {
                    'pk': m.pk,
                    'supplier': m.supplier,
                    'supplier_category': m.supplier_category,
                    'category': m.category_id,
                    'category_path': '/'.join(m.path),
                    # Pending: the category does not exist yet, it is created on the first import
                    'pending': not m.category_id,
                    'function_filter': m.function_filter,
                }
                for m in CategoryMapping.objects.select_related('category')
            ],
            'scopes': [
                {'pk': s.pk, 'name': s.name, 'parents': s.parents, 'mappings': s.mappings}
                for s in ParameterScope.objects.all()
            ],
            'units': [{'pk': u.pk, 'name': u.name, 'units': u.units} for u in TemplateUnit.objects.all()],
        })

    def post(self, request, *args, **kwargs):
        files = request.data.get('files') or {}

        if not isinstance(files, dict) or not files:
            return error_response('No file received')

        try:
            summary = kintree.import_files(files, replace=bool(request.data.get('replace')))
        except (yaml.YAMLError, ValueError, TypeError, AttributeError) as exc:
            return error_response(f'Invalid file: {exc}')

        return Response(summary)


class DefaultMappingsView(APIView):
    """POST: load the default mappings shipped with the plugin (Ki-nTree defaults + Farnell names).

    Body: replace (delete the existing rows first, otherwise only the missing rows are added),
    create_categories (create the InvenTree categories which do not exist).
    """

    permission_classes = [permissions.IsAdminUser]

    def post(self, request, *args, **kwargs):
        summary = kintree.load_defaults(
            replace=bool(request.data.get('replace')),
            create_categories=bool(request.data.get('create_categories')),
        )
        return Response(summary)


class ScopeView(APIView):
    """PUT / DELETE one parameter scope; POST to MappingsView creates them from files."""

    permission_classes = [permissions.IsAdminUser]

    def put(self, request, pk, *args, **kwargs):
        from .models import ParameterScope

        scope = ParameterScope.objects.filter(pk=pk).first() if pk else None
        scope = scope or ParameterScope()
        scope.name = str(request.data.get('name') or scope.name).strip()
        scope.parents = [str(p) for p in request.data.get('parents') or []]
        scope.mappings = {str(k): [str(n) for n in v or []] for k, v in (request.data.get('mappings') or {}).items()}

        try:
            scope.full_clean()
            scope.save()
        except ValidationError as exc:
            return error_response(exc)

        return Response({'pk': scope.pk})

    def delete(self, request, pk, *args, **kwargs):
        from .models import ParameterScope

        ParameterScope.objects.filter(pk=pk).delete()
        return Response(status=204)


class CategoryMappingView(APIView):
    """PUT (pk 0 = new) / DELETE one category mapping."""

    permission_classes = [permissions.IsAdminUser]

    def put(self, request, pk, *args, **kwargs):
        from .models import CategoryMapping

        mapping = CategoryMapping.objects.filter(pk=pk).first() if pk else None
        mapping = mapping or CategoryMapping()
        mapping.supplier = str(request.data.get('supplier') or '').strip()
        mapping.supplier_category = str(request.data.get('supplier_category') or '').strip()
        mapping.category_id = request.data.get('category') or None

        if mapping.category_id:
            mapping.category_path = ''
        elif not mapping.category_path:
            return error_response('Select the category')

        try:
            mapping.full_clean()
            mapping.save()
        except ValidationError as exc:
            return error_response(exc)

        return Response({'pk': mapping.pk})

    def delete(self, request, pk, *args, **kwargs):
        from .models import CategoryMapping

        CategoryMapping.objects.filter(pk=pk).delete()
        return Response(status=204)


class UnitsView(APIView):
    """GET: check the units of all the part parameters. POST: convert them to the template units."""

    permission_classes = [permissions.IsAdminUser]

    def get(self, request, *args, **kwargs):
        from . import units

        return Response(units.plan(apply=False))

    def post(self, request, *args, **kwargs):
        from . import units

        return Response(units.plan(apply=True))
