"""Each fetching entry point must see the Elsevier key. Run in fresh processes:
import order is the bug, so a shared interpreter would hide it."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

GREY_ENV = Path(__file__).resolve().parents[3] / "fetchpdf-grey" / ".env.local"
REPO = Path(__file__).resolve().parents[2]


def _has_key_line():
    return GREY_ENV.exists() and any(l.startswith("ELSEVIER_TDM_API_KEY=") and l.split("=", 1)[1].strip()
                                     for l in GREY_ENV.read_text().splitlines())


@pytest.mark.skipif(not _has_key_line(), reason="no Elsevier key configured in fetchpdf-grey/.env.local")
@pytest.mark.parametrize("module", ["mo_pipeline.shared.fetch",
                                    "mo_pipeline.discover.filter_direct_replications",
                                    "mo_pipeline.discover.download_all_confirmed"])
def test_entry_point_sees_the_elsevier_key(module):
    env = {k: v for k, v in os.environ.items() if k != "ELSEVIER_TDM_API_KEY"}
    code = (f"import {module}; import fetchpdf.fetchpdf as f; "
            "print('KEY' if f._ELSEVIER_TDM_API_KEY else 'NOKEY')")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=REPO, env=env, timeout=120)
    assert out.stdout.strip().endswith("KEY") and "NOKEY" not in out.stdout, out.stderr[-2000:]


def test_backfill_fetches_through_the_shim():
    src = (REPO / "mo_pipeline/corpus/backfill.py").read_text()
    assert "from fetchpdf import" not in src and "mo_pipeline.shared.fetch" in src
