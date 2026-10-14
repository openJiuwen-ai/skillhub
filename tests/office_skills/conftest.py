import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import pytest  # noqa: E402


@pytest.fixture()
def workspace(tmp_path):
    """Fresh workspace directory per test."""
    w = tmp_path / "ws"
    w.mkdir()
    return w
