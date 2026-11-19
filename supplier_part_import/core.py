"""Search parts on Mouser, DigiKey and Farnell and create them in InvenTree with the Ki-nTree rules."""

from django.utils.translation import gettext_lazy as _

from plugin import InvenTreePlugin
from plugin.mixins import AppMixin, SettingsMixin, UrlsMixin, UserInterfaceMixin

from . import PLUGIN_DATE, PLUGIN_VERSION

DEFAULT_IGNORED = (
    'isCanonical, hazardous, productTraceability, rohsCompliant, rohsPhthalatesCompliant, '
    'SVHC, Sostanze estremamente preoccupanti (SVHC), Base Product Number'
)


class SupplierPartImport(AppMixin, SettingsMixin, UrlsMixin, UserInterfaceMixin, InvenTreePlugin):
    """SupplierPartImport - create parts from the supplier data.

    Requires the PartPricingUpdate plugin (supplier credentials, daily request
    limits, prices and price history).
    """

    TITLE = 'Supplier Part Import'
    NAME = 'SupplierPartImport'
    SLUG = 'supplier-part-import'
    DESCRIPTION = 'Search parts on Mouser, DigiKey and Farnell and create them with the Ki-nTree rules'
    VERSION = PLUGIN_VERSION
    PUBLISH_DATE = PLUGIN_DATE
    WEBSITE = 'https://github.com/Kamaar/inventree-supplier-part-import'

    AUTHOR = 'Simone Amadori'
    LICENSE = 'GPL-3.0-or-later'

    MIN_VERSION = '1.0.0'
    # AppMixin plugin: not loaded on a new major InvenTree version until it has been checked
    # (migrations and core models may change, e.g. the pricing changes planned for 2.0)
    MAX_VERSION = '1.99.99'

    ADMIN_SOURCE = 'Settings.js:renderImportSettings'

    SETTINGS = {
        'PROPOSE_IPN': {
            'name': _('Propose IPN'),
            'description': _(
                'Propose the next free IPN of the category (prefix + mounting + "-" + number, '
                'from the IPNs already used in the category)'
            ),
            'validator': bool,
            'default': True,
        },
        'IPN_DIGITS': {
            'name': _('IPN digits'),
            'description': _('Digits of the progressive number of the IPN'),
            'validator': int,
            'default': 4,
        },
        'DEFAULT_REVISION': {
            'name': _('Default revision'),
            'description': _('Revision of the new parts'),
            'default': 'A',
        },
        'PARAMETER_SOURCES': {
            'name': _('Parameter sources'),
            'description': _(
                'Suppliers used for parameters and category, best first. The Mouser API reports '
                'almost no technical parameter: the same MPN is searched on the suppliers before it'
            ),
            'default': 'digikey, farnell, mouser',
        },
        'FARNELL_PARAMETER_STORE': {
            'name': _('Farnell store for the parameters'),
            'description': _(
                'Farnell answers in the language of the store: the parameters are read from this '
                'English store (one request), prices and stock from the store of the pricing plugin. '
                'Empty: use the pricing store'
            ),
            'default': 'uk.farnell.com',
        },
        'IMPORT_UNMAPPED_PARAMETERS': {
            'name': _('Import all the parameters'),
            'description': _('Also import the supplier parameters without a mapping, with the supplier name'),
            'validator': bool,
            'default': True,
        },
        'IGNORED_PARAMETERS': {
            'name': _('Ignored parameters'),
            'description': _('Supplier parameters never imported (comma separated)'),
            'default': DEFAULT_IGNORED,
        },
        'CREATE_ALTERNATES': {
            'name': _('Alternate suppliers'),
            'description': _('Search the same MPN on the other suppliers and propose their supplier parts'),
            'validator': bool,
            'default': True,
        },
        'ATTACH_DECLARATIONS': {
            'name': _('RoHS / REACH declarations'),
            'description': _('Attach the manufacturer declarations reported by DigiKey (one request)'),
            'validator': bool,
            'default': True,
        },
        # Set on the first use: the default mappings are loaded only once, into empty tables
        'DEFAULT_MAPPINGS_CHECKED': {
            'name': _('Default mappings checked'),
            'validator': bool,
            'default': False,
            'hidden': True,
        },
    }

    def setup_urls(self):
        """API endpoints, served below /plugin/supplier-part-import/."""
        from django.urls import path

        from . import views

        return [
            path('status/', views.StatusView.as_view(), name='status'),
            path('search/', views.SearchView.as_view(), name='search'),
            path('preview/', views.PreviewView.as_view(), name='preview'),
            path('proposal/', views.ProposalView.as_view(), name='proposal'),
            path('import/', views.ImportView.as_view(), name='import'),
            path('mappings/', views.MappingsView.as_view(), name='mappings'),
            path('mappings/defaults/', views.DefaultMappingsView.as_view(), name='default-mappings'),
            path('units/', views.UnitsView.as_view(), name='units'),
            path('mappings/scope/<int:pk>/', views.ScopeView.as_view(), name='scope'),
            path('mappings/category/<int:pk>/', views.CategoryMappingView.as_view(), name='category-mapping'),
        ]

    def get_ui_routes(self, request, context, **kwargs):
        """The import page: /web/plugin/supplier-part-import/import"""
        return [
            {
                'key': 'supplier-part-import',
                'title': _('Supplier Part Import'),
                'source': self.plugin_static_file('ImportPage.js:renderImportPage'),
                'options': {'path': 'import'},
            }
        ]

    def get_ui_navigation_items(self, request, context, **kwargs):
        """Link to the import page in the navigation menu."""
        if not request.user or not (request.user.is_staff or request.user.has_perm('part.add_part')):
            return []

        return [
            {
                'key': 'supplier-part-import',
                'title': _('Import from supplier'),
                'icon': 'ti:file-import:outline',
                'options': {'url': f'plugin/{self.SLUG}/import'},
            }
        ]
