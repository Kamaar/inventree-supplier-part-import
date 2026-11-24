"""The plugin on a real InvenTree: default mappings, categories, duplicates, parameters, maintenance."""

from datetime import timedelta

import pytest
from django.contrib.contenttypes.models import ContentType

from supplier_part_import import importer, kintree
from supplier_part_import.models import CategoryMapping, ParameterScope

from conftest import details


def test_plugin_is_active_and_finds_the_pricing_plugin(plugin):
    from InvenTree.version import inventreeVersion
    from plugin import registry

    assert registry.get_plugin_config(plugin.slug).is_active()
    assert plugin.check_version(), f'Not compatible with InvenTree {inventreeVersion()}'
    assert importer.pricing_api() is not None, 'The Part Pricing Update plugin is required'


def test_default_mappings_on_first_use(plugin, clean_mappings):
    plugin.set_setting('DEFAULT_MAPPINGS_CHECKED', False)

    try:
        summary = kintree.ensure_defaults(plugin)

        assert summary['scopes'] > 10 and summary['units'] > 40
        # No categories yet: every category mapping is pending
        assert summary['categories'] == 0 and summary['categories_pending'] > 100
        assert not CategoryMapping.objects.filter(category__isnull=False).exists()
        # Only once
        assert kintree.ensure_defaults(plugin) is None
    finally:
        plugin.set_setting('DEFAULT_MAPPINGS_CHECKED', True)


def test_default_mappings_create_the_categories(clean_mappings):
    summary = kintree.load_defaults(create_categories=True)

    assert summary['categories_created'] > 50 and summary['categories_missing'] == []
    assert not CategoryMapping.objects.filter(category__isnull=True).exists()

    for supplier in ('digikey', 'mouser', 'farnell'):
        assert CategoryMapping.objects.filter(supplier=supplier).count() > 50, supplier


def test_category_proposals(clean_mappings):
    kintree.load_defaults(create_categories=True)

    cases = [
        (details('mouser', ['Multilayer Ceramic Capacitors MLCC - SMD/SMT']), 'Capacitors/Ceramic'),
        (details('farnell', ['Power MOSFET'], **{'Channel Type': 'P Channel'}), 'Transistors/P-Channel FET'),
        (details('farnell', ['Power Inductor (SMD)']), 'Inductors/Power'),
        (
            details('digikey', ['Integrated Circuits (ICs)', 'Power Management (PMIC)',
                                'Voltage Regulators - DC DC Switching Regulators'], Topology='Buck'),
            'Power Management/Buck',
        ),
        (details('digikey', ['Sensors, Transducers', 'Temperature Sensors', 'NTC Thermistors']), 'Resistors/NTC'),
    ]

    for product, expected in cases:
        category = importer.propose_category([product])
        assert category is not None and category.pathstring == expected, (product.category, category)


def test_pending_category_is_created_by_the_import(clean_mappings):
    from part.models import Part

    kintree.load_defaults()
    shield = details('mouser', ['EMI Gaskets, Sheets, Absorbers & Shielding'])
    shield.mpn = 'BMI-S-201-F'

    category = importer.propose_category([shield])
    assert isinstance(category, importer.PendingCategory) and category.pathstring == 'RF/Shield'

    proposal = importer.category_proposal({'mouser': shield}, 'mouser', shield, category)
    assert proposal['category'] is None and proposal['new_category'] == 'RF/Shield'

    token = importer.save_preview({
        'sources': {'mouser': shield}, 'declarations': [], 'primary': 'mouser', 'parameter_source': 'mouser',
        'parameter_details': shield, 'existing_codes': {},
    })
    result = importer.create({
        'token': token,
        'part': {'name': 'BMI-S-201-F', 'description': 'Shield', 'category': None, 'new_category': 'RF/Shield'},
        'mpn': 'BMI-S-201-F', 'manufacturer': 'Laird', 'parameters': [], 'supplier_parts': [],
        'attach_datasheet': False, 'download_image': False, 'attach_declarations': False,
    })

    part = Part.objects.get(pk=result['pk'])
    assert part.category.pathstring == 'RF/Shield'
    # The pending mappings of the new category are linked
    assert not CategoryMapping.objects.filter(category__isnull=True, category_path='RF/Shield').exists()
    assert CategoryMapping.objects.filter(category=part.category).exists()


def test_preview_is_kept_in_the_database():
    """Issue #1: the preview must be readable by any server process, not only by the one which made it."""
    from django.utils import timezone

    from supplier_part_import.models import PreviewSession

    product = details('mouser', ['TRIACs'])
    token = importer.save_preview({'sources': {'mouser': product}, 'primary': 'mouser'})

    assert PreviewSession.objects.filter(token=token).exists()
    assert importer.load_preview(token)['sources']['mouser'].sku == product.sku

    with pytest.raises(importer.ImportError_, match='expired'):
        importer.load_preview('missing')

    PreviewSession.objects.filter(token=token).update(
        created=timezone.now() - timedelta(seconds=importer.PREVIEW_TIMEOUT + 1)
    )

    with pytest.raises(importer.ImportError_, match='expired'):
        importer.load_preview(token)

    # The expired previews are removed by the next one
    importer.save_preview({'sources': {}})
    assert not PreviewSession.objects.filter(token=token).exists()


def test_category_from_the_supplier_category_without_mapping(clean_mappings):
    """Issue #2: a supplier category without mapping proposes a new category named as the supplier one."""
    from part.models import Part, PartCategory

    triac = details('digikey', ['Discrete Semiconductor Products', 'Thyristors', 'TRIACs'])
    triac.mpn = 'BT137S-800F,118'

    assert importer.propose_category([triac]) is None

    # No category with the supplier names: a new one at the root
    category = importer.supplier_category_fallback([triac])
    assert isinstance(category, importer.PendingCategory) and category.pathstring == 'TRIACs'

    # Below the existing category named as an upper supplier level
    semiconductors = PartCategory.objects.create(name='Semiconductors')
    thyristors = PartCategory.objects.create(name='Thyristors', parent=semiconductors)
    category = importer.supplier_category_fallback([triac])
    assert isinstance(category, importer.PendingCategory)
    assert category.pathstring == 'Semiconductors/Thyristors/TRIACs'

    token = importer.save_preview({
        'sources': {'digikey': triac}, 'declarations': [], 'primary': 'digikey', 'parameter_source': 'digikey',
        'parameter_details': triac, 'existing_codes': {},
    })

    # A path typed by the user which exists (in another case) is the existing category
    proposal = importer.proposal_for_category(token, None, 'semiconductors / thyristors')
    assert proposal['category'] == thyristors.pk and proposal['new_category'] == ''

    result = importer.create({
        'token': token,
        'part': {'name': triac.mpn, 'description': 'TRIAC', 'category': None,
                 'new_category': 'Semiconductors/Thyristors/TRIACs'},
        'mpn': triac.mpn, 'manufacturer': 'WeEn', 'parameters': [], 'supplier_parts': [],
        'remember_category': True,
        'attach_datasheet': False, 'download_image': False, 'attach_declarations': False,
    })

    part = Part.objects.get(pk=result['pk'])
    assert part.category.pathstring == 'Semiconductors/Thyristors/TRIACs'
    # Remembered: the next TRIAC is proposed by the mapping
    assert importer.propose_category([triac]) == part.category
    # Now the category exists: proposed as it is
    assert importer.supplier_category_fallback([triac]) == part.category


def test_duplicate_found_by_the_old_mpn():
    from company.models import Company, ManufacturerPart
    from part.models import Part

    avx = Company.objects.create(name='IT KYOCERA AVX', is_manufacturer=True)
    part = Part.objects.create(name='IT CAP 0.22uF', IPN='IT-PE041-0194', component=True, purchaseable=True)
    ManufacturerPart.objects.create(part=part, manufacturer=avx, MPN='IT0402ZD224KAT2A')

    assert importer.existing_candidates(['IT-KGM05AR51A224KH']) == []
    found = importer.existing_candidates(['IT-KGM05AR51A224KH'], ['IT0402ZD224KAT2A'])
    assert [(c['pk'], c['match']) for c in found] == [(part.pk, 'old name')]


def test_dimensionless_template_units():
    """'±50ppm' on a 'ppm' template is 50, not the fraction 5e-05 returned by the InvenTree converter."""
    from supplier_part_import.units import to_template_units

    assert to_template_units('±50ppm', 'ppm') == '±50'
    assert to_template_units('±30 ppm', 'ppm') == '±30'
    assert to_template_units('±0.005%', 'ppm') == '±50'
    assert to_template_units('20', 'ppm') == '20'
    assert to_template_units('±20%', '%') == '±20'
    assert to_template_units('5V', 'ppm') is None


def test_si_prefix_in_template_units():
    """Very small or large values keep an SI prefix ('1µ' in A), read back by InvenTree as the same number."""
    from InvenTree.conversion import convert_physical_value

    from supplier_part_import.units import to_template_units

    cases = {
        ('1µA', 'A'): ('1µ', '1u'),
        ('100nF', 'F'): ('100n',),
        ('4 MHz', 'Hz'): ('4M',),
        ('0.5W', 'W'): ('500m',),
        ('4.7kOhm', 'ohm'): ('4.7k',),
        ('25V', 'V'): ('25',),
        ('-55~85°C', 'degC'): ('-55~85',),
        # 'm' would be read as metres: no prefix
        ('5mm', 'm'): ('0.005',),
        ('0.1mm', 'mm'): ('0.1',),
    }

    for (value, units), expected in cases.items():
        result = to_template_units(value, units)
        assert result in expected, (value, units, result)

    for value, units, number in [('1µ', 'A', 1e-6), ('500m', 'W', 0.5), ('4M', 'Hz', 4e6), ('1u', 'A', 1e-6)]:
        assert abs(convert_physical_value(value, units) - number) <= number * 1e-9


def test_parameters_replace_only_when_selected():
    from common.models import Parameter, ParameterTemplate
    from part.models import Part

    part = Part.objects.create(name='IT-PARAMS', component=True)
    content_type = ContentType.objects.get_for_model(Part)
    voltage, _ = ParameterTemplate.objects.get_or_create(name='IT Rated Voltage', defaults={'units': 'V'})
    package, _ = ParameterTemplate.objects.get_or_create(name='IT Package Type', defaults={'units': ''})
    Parameter.objects.create(model_type=content_type, model_id=part.pk, template=voltage, data='-')
    Parameter.objects.create(model_type=content_type, model_id=part.pk, template=package, data='0603')

    warnings = importer.save_parameters(part, [
        {'template': 'IT Rated Voltage', 'value': '10V', 'include': True, 'overwrite': True},
        {'template': 'IT Package Type', 'value': '0402', 'include': True, 'overwrite': False},
    ], only_missing=True)

    values = {p.template.name: p.data for p in Parameter.objects.filter(model_type=content_type, model_id=part.pk)}
    assert values == {'IT Rated Voltage': '10', 'IT Package Type': '0603'}
    assert any('replaced' in w for w in warnings)


def test_move_inductor_dcr(clean_mappings):
    from common.models import Parameter, ParameterTemplate
    from part.models import Part, PartCategory

    from supplier_part_import.maintenance import move_inductor_dcr

    ParameterScope.objects.create(name='Inductors', mappings={'ESR': ['DC Resistance (DCR)']})
    esr, _ = ParameterTemplate.objects.get_or_create(name='ESR', defaults={'units': 'mΩ'})
    inductors = PartCategory.objects.create(name='Inductors')
    power = PartCategory.objects.create(name='Power', parent=inductors)
    part = Part.objects.create(name='IT-L1', category=power, component=True)
    content_type = ContentType.objects.get_for_model(Part)
    Parameter.objects.create(model_type=content_type, model_id=part.pk, template=esr, data='28')

    # Dry run: nothing changes
    assert move_inductor_dcr()['moved'] == 1
    assert Parameter.objects.get(model_type=content_type, model_id=part.pk).template == esr

    summary = move_inductor_dcr(apply=True)

    parameter = Parameter.objects.get(model_type=content_type, model_id=part.pk)
    assert summary['moved'] == 1 and parameter.template.name == 'DC Resistance' and parameter.data == '28'
    assert 'DC Resistance' in ParameterScope.objects.get(name='Inductors').mappings
    # Running it again changes nothing
    assert move_inductor_dcr(apply=True)['moved'] == 0


def test_static_files_resolve(plugin):
    from django.core.management import call_command

    # Copy the static files of the active plugins, then resolve the hashed name of the vite manifest
    call_command('collectplugins')
    url = plugin.plugin_static_file('ImportPage.js:renderImportPage')

    assert url.startswith('/static/plugins/supplier-part-import/ImportPage-') and url.endswith(':renderImportPage')
