"""Admin site configuration for the SupplierPartImport plugin."""

from django.contrib import admin

from .models import CategoryMapping, ParameterScope, TemplateUnit


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
