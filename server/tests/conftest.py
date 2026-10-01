"""Keep server tests off live state and the live corpus.

Without this the tests wrote `pytest_*` pid files into ~/.local/state/mo_pipeline
(they showed up in `dev.sh status`), launched a fake stage under the real id
`extract`, and the probe test listed the 5,000-folder live inbox a dozen times.

Runner state is redirected through MO_STATE_DIR, which server.app reads at import
and nothing outside server/ reads. Data roots are patched per test instead of via
the environment, so other test directories collected in the same pytest run keep
their own config.
"""
import os
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(tempfile.mkdtemp(prefix="mo_server_tests_"))
os.environ["MO_STATE_DIR"] = str(_ROOT / "state")

_PATHS = {  # config attribute -> path under the throwaway root
    "MEDIA_ROOT": "media", "PAPERS_DIR": "media/papers", "INBOX_DIR": "media/inbox",
    "CATALOG_PATH": "media/corpus.sqlite", "DATA_DIR": "data", "PROGRESS_DIR": "progress",
}


@pytest.fixture(autouse=True)
def _isolated_data(monkeypatch):
    from mo_pipeline import config
    for attr, sub in _PATHS.items():
        if hasattr(config, attr):
            target = _ROOT / sub
            if not target.suffix:
                target.mkdir(parents=True, exist_ok=True)
            monkeypatch.setattr(config, attr, target)
