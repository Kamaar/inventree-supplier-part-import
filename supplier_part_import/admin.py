"""Admin site configuration for the SupplierPartImport plugin."""

from django.contrib import admin

from .models import CategoryMapping, ParameterScope, PreviewSession, TemplateUnit


@admin.register(CategoryMapping)
class CategoryMappingAdmin(admin.ModelAdmin):
    """Admin interface for the category mappings."""

    list_display = ('supplier_category', 'supplier', 'category')
    list_filter = ('supplier',)
    search_fields = ('supplier_category',)
    raw_id_fields = ('category',)


@admin.register(ParameterScope)
class ParameterScopeAdmin(admin.ModelAdmin):
    """Admin interface for the parameter mappings."""

    list_display = ('name', 'parents')
    search_fields = ('name',)


@admin.register(TemplateUnit)
class TemplateUnitAdmin(admin.ModelAdmin):
    """Admin interface for the template units."""

    list_display = ('name', 'units')
    search_fields = ('name',)


# Every model of the plugin must be registered: on a plugin reload InvenTree reloads this
# module when a model is missing from the admin site, and the registration of the models
# already there fails (AlreadyRegistered), so the plugin URLs are not registered
@admin.register(PreviewSession)
class PreviewSessionAdmin(admin.ModelAdmin):
    """Admin interface for the previews kept for the import (read only)."""

    list_display = ('token', 'created')
    readonly_fields = ('token', 'created')
    exclude = ('data',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
