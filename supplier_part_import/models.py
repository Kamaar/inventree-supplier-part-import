"""Mapping tables of the SupplierPartImport plugin (imported from Ki-nTree, editable)."""

from django.db import models
from django.utils.translation import gettext_lazy as _


class PluginPermissionMixin:
    """Every authenticated user can read, only staff users can modify."""

    @classmethod
    def check_user_permission(cls, user, permission: str) -> bool:
        if not user or not user.is_authenticated:
            return False

        return permission == 'view' or user.is_staff


class CategoryMapping(PluginPermissionMixin, models.Model):
    """Supplier category -> InvenTree category (Ki-nTree <supplier>_categories.yaml)."""

    class Meta:
        app_label = 'supplier_part_import'
        verbose_name = _('Category Mapping')
        verbose_name_plural = _('Category Mappings')
        ordering = ['supplier_category']
        # The same supplier category can lead to several subcategories, chosen by function
        unique_together = [('supplier', 'supplier_category', 'category')]

    supplier = models.CharField(
        max_length=20,
        blank=True,
        default='',
        verbose_name=_('Supplier'),
        help_text=_('mouser, digikey, farnell; empty for every supplier'),
    )

    supplier_category = models.CharField(
        max_length=250,
        verbose_name=_('Supplier category'),
        help_text=_('Category name used by the supplier (e.g. "Ceramic Capacitors")'),
    )

    category = models.ForeignKey(
        'part.PartCategory',
        on_delete=models.CASCADE,
        related_name='+',
        null=True,
        blank=True,
        verbose_name=_('InvenTree category'),
        help_text=_('Empty for a pending mapping: the category is created on the first import'),
    )

    category_path = models.CharField(
        max_length=250,
        blank=True,
        default='',
        db_default='',
        verbose_name=_('Pending category'),
        help_text=_('Path of a category which does not exist yet (e.g. "RF/Shield")'),
    )

    function_filter = models.BooleanField(
        default=False,
        verbose_name=_('Filter by function'),
        help_text=_(
            'Use this category only if its name matches the "Function Type" parameter '
            '(Ki-nTree subcategories with the "__" prefix, e.g. N-Channel FET)'
        ),
    )

    @property
    def path(self) -> list[str]:
        """Names of the target category, from the root ('RF', 'Shield')."""
        if self.category_id:
            return self.category.pathstring.split('/')

        return [name for name in self.category_path.split('/') if name]

    def __str__(self):
        return f'{self.supplier or "*"}: {self.supplier_category} -> {"/".join(self.path)}'


class ParameterScope(PluginPermissionMixin, models.Model):
    """Parameter mapping of a category (a section of Ki-nTree supplier_parameters.yaml).

    'name' is an InvenTree category name ('Inductors'), a category path
    ('Capacitors/Ceramic') or a shared scope used as parent ('Base', 'Passives').
    """

    class Meta:
        app_label = 'supplier_part_import'
        verbose_name = _('Parameter Scope')
        verbose_name_plural = _('Parameter Scopes')
        ordering = ['name']

    name = models.CharField(max_length=250, unique=True, verbose_name=_('Name'))

    parents = models.JSONField(
        default=list,
        blank=True,
        verbose_name=_('Parents'),
        help_text=_('Scopes whose mappings are inherited (e.g. ["Base", "Passives"])'),
    )

    mappings = models.JSONField(
        default=dict,
        blank=True,
        verbose_name=_('Mappings'),
        help_text=_('{"InvenTree template": ["supplier name", ...]}'),
    )

    def __str__(self):
        return self.name


class TemplateUnit(PluginPermissionMixin, models.Model):
    """Units used when a parameter template must be created (Ki-nTree parameters.yaml)."""

    class Meta:
        app_label = 'supplier_part_import'
        verbose_name = _('Template Unit')
        verbose_name_plural = _('Template Units')
        ordering = ['name']

    name = models.CharField(max_length=100, unique=True, verbose_name=_('Template'))

    units = models.CharField(max_length=25, blank=True, default='', verbose_name=_('Units'))

    def __str__(self):
        return f'{self.name} [{self.units}]'
