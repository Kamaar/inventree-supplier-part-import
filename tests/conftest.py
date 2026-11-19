"""The integration tests need a real InvenTree: they run only when INVENTREE_SRC is set (see tests/integration)."""

import os

collect_ignore = [] if os.environ.get('INVENTREE_SRC') else ['integration']
