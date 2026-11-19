"""Check that the git tag (EXPECTED_VERSION, e.g. 'v0.3.7') matches PLUGIN_VERSION.

Used by the CI publish job, so that a wrong tag never publishes a package
with a different version number.
"""

import os
import pathlib
import re
import sys

source = pathlib.Path(__file__).resolve().parent.parent / 'supplier_part_import' / '__init__.py'
version = re.search(r'PLUGIN_VERSION\s*=\s*"([^"]+)"', source.read_text(encoding='utf-8')).group(1)
expected = os.environ.get('EXPECTED_VERSION', '').removeprefix('v')

if version != expected:
    sys.exit(f'Tag v{expected} does not match PLUGIN_VERSION {version}')

print(f'Version {version} OK')
