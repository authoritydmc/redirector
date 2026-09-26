"""Test isolation.

Without this, running ``pytest`` in a checkout connects to the *real*
``data/redirect.db`` and the ``db.drop_all()`` in the fixtures deletes the
operator's shortcuts, ``user_params`` and upstream history. That is a data
destroying test bug, not a hypothetical one.

The fix is to point the whole app at a throwaway data directory before anything
imports ``app``, using the same ``REDIRECTOR_DATA_DIR`` override that Docker,
systemd and launchd use. Setting it here - in ``conftest.py``, which pytest
imports before collecting any test module - means ``app.config`` builds its
paths against a temporary directory and the real data is never opened.
"""

import os
import shutil
import tempfile

_TEST_DATA_DIR = tempfile.mkdtemp(prefix="redirector-tests-")
os.environ["REDIRECTOR_DATA_DIR"] = _TEST_DATA_DIR
# Keep the suite offline and quiet; neither is what these tests are checking.
os.environ.setdefault("REDIRECTOR_TESTING", "1")


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    shutil.rmtree(_TEST_DATA_DIR, ignore_errors=True)
