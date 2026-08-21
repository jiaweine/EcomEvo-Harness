from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path


# API tests import the application during collection. Give every pytest process an
# isolated durable root before that import happens so even ``ECOMEVO_DATA=/production
# pytest`` cannot mutate operator data by accident. A caller may retain a test DB for
# integrity inspection through the deliberately test-only variable below.
_configured_test_data = os.environ.get("ECOMEVO_TEST_DATA")
_owns_test_data = not _configured_test_data
_test_data = Path(_configured_test_data or tempfile.mkdtemp(prefix="ecomevo-pytest-"))
_test_data.mkdir(parents=True, exist_ok=True)
os.environ["ECOMEVO_DATA"] = str(_test_data)
os.environ.setdefault("ECOMEVO_AUTH_MODE", "local")
os.environ.setdefault("ECOMEVO_LOCAL_ROLE", "admin")


@atexit.register
def _cleanup_test_data() -> None:
    if _owns_test_data:
        shutil.rmtree(_test_data, ignore_errors=True)
