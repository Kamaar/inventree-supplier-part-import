"""Tests for the import rules, without Django."""

from dataclasses import dataclass

import pytest

from supplier_part_import import rules

# Same structure as Ki-nTree's supplier_parameters.yaml
SCOPES = {
    'Base': {'parents': [], 'mappings': {'Package Type': ['Package / Case'], 'Temperature Range': ['Operating Temperature']}},
    'Passives': {'parents': [], 'mappings': {'Tolerance': ['Tolerance']}},
    'Inductors': {
        'parents': ['Base', 'Passives'],
        'mappings': {
            'ESR': ['DC Resistance (DCR)', 'DC Resistance (DCR) (Max)'],
            'Package Height': ['Height - Seated (Max)', 'Height (Max)'],
            'Rated Current': ['Current Rating (Max)', 'Current Rating (Amps)'],
            'Saturation Current': ['Current - Saturation'],
            'Value': ['Inductance', 'Impedance @ Frequency', 'Induttanza'],
        },
    },
    'Capacitors/Ceramic': {'parents': ['Base'], 'mappings': {'Value': ['Capacitance']}},
}


def test_parameter_map_inherits_parents():
    mapping = rules.parameter_map(SCOPES, ['Inductors', 'Power'])

    assert mapping['Inductance'] == 'Value'
    assert mapping['Induttanza'] == 'Value'  # Farnell it.farnell.com
    assert mapping['Package / Case'] == 'Package Type'  # from Base
    assert mapping['Tolerance'] == 'Tolerance'  # from Passives


def test_parameter_map_most_specific_scope():
    assert rules.parameter_map(SCOPES, ['Capacitors', 'Ceramic', '0402'])['Capacitance'] == 'Value'
    assert rules.parameter_map(SCOPES, ['Connectors']) == {}


def test_map_parameters_mapped_unmapped_ignored():
    supplier = {
        'Inductance': '8.2 µH',
        'Operating Temperature': '-40°C ~ 125°C',
        'Package / Case': 'Nonstandard',
        'Shielding': 'Shielded',
        'tariffCode': '85045000',
        'Impedance @ Frequency': '-',
    }
    mapping = rules.parameter_map(SCOPES, ['Inductors'])

    result = rules.map_parameters(supplier, mapping, 'Inductors', ignored={'tariffcode'})

    by_template = {p['template']: p for p in result}
    assert by_template['Value']['value'] == '8.2 µH'
    assert by_template['Temperature Range']['value'] == '-40~125°C'
    assert by_template['Shielding']['mapped'] is False  # imported with the supplier name
    assert 'tariffCode' not in by_template
    assert [p['mapped'] for p in result] == sorted([p['mapped'] for p in result], reverse=True)

    without_unmapped = rules.map_parameters(supplier, mapping, 'Inductors', import_unmapped=False)
    assert all(p['mapped'] for p in without_unmapped)


@pytest.mark.parametrize(
    ('category', 'name', 'value', 'expected'),
    [
        ('Resistors', 'Resistance', '10 kOhms', '10K'),
        ('Resistors', 'Resistance', '100 Ohms', '100R'),
        ('Capacitors', 'Package Size', '0.039" L x 0.020" W (1.00mm x 0.50mm)', '1.00x0.50mm'),
        ('Inductors', 'Package Height', '0.189" (4.80mm)', '4.80mm'),
        ('Resistors', 'Power', '0.25W, 1/4W', '1/4W'),
        ('Inductors', 'ESR', '20mOhm Max', '20mR'),
        ('Base', 'Temperature Range', '-55°C ~ 125°C', '-55~125°C'),
        ('Base', 'Package Type', '0402 (1005 Metric)', '0402'),
    ],
)
def test_clean_parameter_value(category, name, value, expected):
    assert rules.clean_parameter_value(category, name, value) == expected


def test_compliance_parameters():
    params = rules.compliance_parameters('Conforme (RoHS3)', 'Conforme (nessuna SVHC)')

    assert [(p['template'], p['value']) for p in params] == [
        ('Stato RoHS', 'Conforme (RoHS3)'),
        ('RoHS', 'True'),
        ('REACH', 'Conforme (nessuna SVHC)'),
    ]
    assert [(p['template'], p['value']) for p in rules.compliance_parameters('Non conforme', '')] == [
        ('Stato RoHS', 'Non conforme'),
        ('RoHS', 'False'),
    ]
    # 'Non applicabile': no checkbox value
    assert [p['template'] for p in rules.compliance_parameters('Non applicabile', '')] == ['Stato RoHS']


@pytest.mark.parametrize(
    ('url', 'expected'),
    [
        (
            'https://industrial.panasonic.com/cdbs/www-cgi/auto-publishing.cgi?type=ROSU&region=WW&layer=4',
            'https://industrial.panasonic.com/cdbs/www-cgi/auto-publishing.cgi?region=WW&type=ROSU&layer=4',
        ),
        ('//mm.digikey.com/a b.pdf', 'https://mm.digikey.com/a%20b.pdf'),
        ('httpss://it.farnell.com/x.pdf', 'https://it.farnell.com/x.pdf'),
        ('ftp://x', ''),
        ('', ''),
    ],
)
def test_normalize_url(url, expected):
    assert rules.normalize_url(url) == expected


@pytest.mark.parametrize(
    ('value', 'digit'),
    [('Surface Mount', '1'), ('Through Hole', '2'), ('Panel Mount', '3'), ('Free Hanging (In-Line)', '6'), ('Other', None)],
)
def test_mounting_digit(value, digit):
    assert rules.mounting_digit({'Mounting Type': value}) == digit


def test_ipn_prefix_and_next():
    category_ipns = ['PE061-0094', 'PE061-0095', 'PE062-0003', 'PE151-0001', 'not an ipn', '']

    assert rules.ipn_prefix(category_ipns, '1') == ('PE06', '1')
    assert rules.ipn_prefix(category_ipns, '2') == ('PE06', '2')
    assert rules.ipn_prefix(category_ipns, None) == ('PE06', '1')  # most common mounting
    assert rules.ipn_prefix([], '1') is None

    all_ipns = category_ipns + ['PE061-0120', 'PE061-0099A']
    assert rules.next_ipn('PE06', '1', all_ipns) == 'PE061-0121'
    assert rules.next_ipn('PE06', '3', all_ipns) == 'PE063-0001'


@dataclass
class Result:
    sku: str
    mpn: str
    packaging: str


def test_preferred_result():
    results = [
        Result('296-1-2-ND', 'NE555PWR', 'Tape & Reel (TR)'),
        Result('296-1-1-ND', 'NE555PWR', 'Cut Tape (CT)'),
        Result('296-1-6-ND', 'NE555PWR', 'Digi-Reel®'),
        Result('X', 'NE555P', 'Tube'),
    ]

    assert rules.preferred_result(results, 'ne555-pwr').sku == '296-1-1-ND'
    assert rules.preferred_result(results, 'NE555P').sku == 'X'
    assert rules.preferred_result(results, 'OTHER') is None


@pytest.mark.parametrize(
    ('code', 'stored', 'match'),
    [
        ('885012107027', '885012107027', True),
        ('885012107027', '8850-1210-7027', True),
        ('ERA-8AEB470V', 'era8aeb470v', True),
        ('MSS1048-822NLC', ' MSS1048-822NLC ', True),
        ('MSS1048-822NLC', 'MSS1048-822NLCT', False),
        ('MSS1048-822NLC', 'XMSS1048-822NLC', False),
        ('1N4148', '1N4148W', False),
    ],
)
def test_code_regex(code, stored, match):
    import re

    assert bool(re.match(rules.code_regex(code), stored, re.IGNORECASE)) is match


def test_code_regex_too_short():
    assert rules.code_regex('R1') is None
    assert rules.code_regex('-') is None


TRANSISTOR_MAPPING = {'Transistor Type': 'Function Type', 'FET Type': 'Function Type', 'Package / Case': 'Package Type'}


def test_function_values():
    params = {'FET Type': 'N-Channel', 'Package / Case': 'SOT-23-3', 'Technology': 'MOSFET'}

    assert rules.function_values(params, TRANSISTOR_MAPPING) == ['N-Channel']


@pytest.mark.parametrize(
    ('subcategory', 'values', 'match'),
    [
        ('N-Channel FET', ['N-Channel'], True),
        ('P-Channel FET', ['N-Channel'], False),
        ('NPN', ['NPN'], True),
        ('PNP', ['NPN'], False),
        ('Schottky', ['Schottky'], True),
        ('Standard', ['Schottky'], False),
        ('Buck', ['Step-Down (Buck)'], True),
        ('Boost', ['Step-Down (Buck)'], False),
        ('Schottky', [], False),
    ],
)
def test_function_match(subcategory, values, match):
    assert rules.function_match(subcategory, values) is match


@pytest.mark.parametrize(
    ('name', 'path', 'match'),
    [
        # Old DigiKey API v3 names (Ki-nTree digikey_categories.yaml) against v4 paths
        ('Transistors - FETs, MOSFETs - Single',
         ['Discrete Semiconductor Products', 'Transistors', 'FETs, MOSFETs', 'Single FETs, MOSFETs'], True),
        ('Diodes - Rectifiers - Single', ['Discrete Semiconductor Products', 'Diodes', 'Rectifiers', 'Single Diodes'], True),
        ('PMIC - Voltage Regulators - Linear',
         ['Integrated Circuits (ICs)', 'Power Management (PMIC)', 'Voltage Regulators - Linear, Low Drop Out (LDO) Regulators'], True),
        ('Transistors - Bipolar (BJT) - Single',
         ['Discrete Semiconductor Products', 'Transistors', 'FETs, MOSFETs', 'Single FETs, MOSFETs'], False),
        ('Diodes - Rectifiers - Single', ['Discrete Semiconductor Products', 'Diodes', 'Zener', 'Single Zener Diodes'], False),
        # Plain names are compared exactly, not here
        ('Ceramic Capacitors', ['Capacitors', 'Ceramic Capacitors'], False),
    ],
)
def test_legacy_category_match(name, path, match):
    assert rules.legacy_category_match(name, path) is match


def test_with_derived_parameters():
    farnell = {'Operating Temperature Min': '-55°C', 'Operating Temperature Max': '125°C', 'Supply Voltage Min': '4.5V',
               'Supply Voltage Max': '16V', 'Product Width': '1mm'}

    derived = rules.with_derived_parameters(farnell, 'NE555P')

    assert derived['Operating Temperature'] == '-55°C ~ 125°C'
    assert derived['Supply Voltage'] == '4.5V ~ 16V'
    assert derived['Manufacturer Part Number'] == 'NE555P'
    # DigiKey already has the range: not replaced
    assert rules.with_derived_parameters({'Operating Temperature': '0°C ~ 70°C', 'Operating Temperature Min': '0°C',
                                          'Operating Temperature Max': '70°C'})['Operating Temperature'] == '0°C ~ 70°C'


def test_value_from_mpn_and_ranges_mapped():
    mapping = {'Manufacturer Part Number': 'Value', 'Operating Temperature': 'Temperature Range'}
    params = rules.with_derived_parameters({'Operating Temperature Min': '-55°C', 'Operating Temperature Max': '150°C'}, 'BAT54')

    result = {p['template']: p['value'] for p in rules.map_parameters(params, mapping, 'Diodes', ignored={'manufacturer part number'})}

    assert result['Value'] == 'BAT54'  # mapped even if in the ignored list
    assert result['Temperature Range'] == '-55~150°C'
    # Not mapped: the MPN is never imported as a parameter of its own
    unmapped = rules.map_parameters(rules.with_derived_parameters({}, 'BAT54'), {}, 'Diodes')
    assert unmapped == []


@pytest.mark.parametrize(('value', 'expected'), [('10kohm', '10K'), ('100ohm', '100R'), ('4.7Mohm', '4.7M'), ('10 kOhms', '10K')])
def test_clean_farnell_resistance(value, expected):
    assert rules.clean_parameter_value('Resistors', 'Value', 'x') == 'x'
    assert rules.clean_parameter_value('Resistors', 'Resistance', value) == expected


@pytest.mark.parametrize(('subcategory', 'value', 'match'), [('N-Channel FET', 'N Channel', True), ('P-Channel FET', 'N Channel', False)])
def test_function_match_farnell_spacing(subcategory, value, match):
    assert rules.function_match(subcategory, [value]) is match


def test_map_parameters_clean_by_supplier_and_template_name():
    mapping = {'Resistance': 'Value', 'Power Inductor Case': 'Package Size', 'Diode Case Style': 'Package Type'}
    params = {'Resistance': '10kohm', 'Power Inductor Case': '10.2mm x 10mm x 4.8mm', 'Diode Case Style': 'DO-214AA (SMB)'}

    result = {p['template']: p['value'] for p in rules.map_parameters(params, mapping, 'Resistors')}

    assert result == {'Value': '10K', 'Package Size': '10.2x10x4.8mm', 'Package Type': 'DO-214AA'}


@pytest.mark.parametrize(
    ('name', 'value', 'expected'),
    [
        ('ESR DC Resistance Max', '0.02ohm', '0.02R'),
        ('RDS On Resistance Drain Source On State Resistance', '3.5ohm', '3.5R'),
        ('Number of Contacts No. of Contacts', '2Contacts', '2'),
        ('Number of Rows No. of Rows', '1Rows', '1'),
        ('Tolerance Capacitance Tolerance', '± 10%', '±10%'),
    ],
)
def test_clean_farnell_formats(name, value, expected):
    assert rules.clean_parameter_value('Base', name, value) == expected


@pytest.mark.parametrize(
    ('value', 'expected'),
    [('22 µF', '22µF'), ('0.1 µF', '0.1µF'), ('8.2µH', '8.2µH'), ('Through Hole', 'Through Hole'), ('3 Pins Header', '3 Pins Header')],
)
def test_compact_quantity(value, expected):
    assert rules.compact_quantity(value) == expected


@pytest.mark.parametrize(
    ('value', 'expected'),
    [
        ('±20%', ('±', ['20%'], '')),
        ('-55~85°C', ('', ['-55', '85°C'], '~')),
        ('-55°C ~ 125°C', ('', ['-55°C', '125°C'], '~')),
        ('2.00x1.25mm', ('', ['2.00', '1.25mm'], 'x')),
        ('10.2x10x4.8mm', ('', ['10.2', '10', '4.8mm'], 'x')),
        ('25V', ('', ['25V'], '')),
        ('0x', ('', ['0x'], '')),
    ],
)
def test_split_quantity(value, expected):
    assert rules.split_quantity(value) == expected


def test_normalize_unit_text_and_format():
    assert rules.normalize_unit_text('20mOhm') == '20mohm'
    assert rules.normalize_unit_text('10 kOhms') == '10 kohm'
    assert rules.format_number(21999.999999999996) == '22000'
    assert rules.format_number(1.25) == '1.25'
    assert rules.format_number(25.0) == '25'


@pytest.mark.parametrize(
    ('number', 'expected'),
    [
        (1e-06, '0.000001'),
        (5e-05, '0.00005'),
        (4.7e-06, '0.0000047'),
        (1e-05, '0.00001'),
        (1.2e10, '12000000000'),
        (-3.3e-07, '-0.00000033'),
        (2.2000000000000002e-11, '0.000000000022'),
        (0.0001, '0.0001'),
        (0.0, '0'),
    ],
)
def test_format_number_without_exponent(number, expected):
    assert rules.format_number(number) == expected


@pytest.mark.parametrize(
    ('number', 'expected'),
    [
        (1e-06, ['1µ', '1u']),
        (4.7e-06, ['4.7µ', '4.7u']),
        (1e-07, ['100n']),
        (0.5, ['500m']),
        (4700.0, ['4.7k']),
        (1.2e10, ['12G']),
        (0.9999999999999, []),
        (0.99999999999999e-3, ['1m']),
        (25.0, []),
        (999.0, []),
        (0.0, []),
        (-3.3e-12, ['-3.3p']),
    ],
)
def test_si_prefixed(number, expected):
    assert rules.si_prefixed(number) == expected


@pytest.mark.parametrize('value', ['-', '--', ' - ', '', 'N/A', '?'])
def test_placeholder_values(value):
    assert rules.is_placeholder_value(value)


@pytest.mark.parametrize('value', ['-55', '-1', '0', 'Jumper', '-20%, +50%'])
def test_not_placeholder_values(value):
    assert not rules.is_placeholder_value(value)


@pytest.mark.parametrize(
    ('value', 'expected'),
    [
        ('⌀10.00mm', ('⌀', ['10.00mm'], '')),
        ('-55°C to 125°C', ('', ['-55°C', '125°C'], '~')),
        ('-55°C to +150°C', ('', ['-55°C', '+150°C'], '~')),
    ],
)
def test_split_quantity_diameter_and_to(value, expected):
    assert rules.split_quantity(value) == expected


def test_normalize_unit_text_kintree_formats():
    assert rules.normalize_unit_text('-55℃ to 125℃') == '-55°C to 125°C'
    assert rules.normalize_unit_text('0.236\\"') == '0.236"'
    assert rules.normalize_unit_text('277 VDC') == '277 V'
    assert rules.normalize_unit_text('125VDC') == '125V'
    assert rules.normalize_unit_text('250 VAC') == '250 VAC'
