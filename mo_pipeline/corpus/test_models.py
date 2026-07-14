"""DOI <-> folder encoding regression tests (this encoding has regressed twice:
the multi-slash decode, and the colon collision). Run:
    cd mo_pipeline && python -m pytest mo_pipeline/corpus/test_models.py -q
"""
from mo_pipeline.corpus.models import doi_to_folder, folder_to_doi

# Real-world DOI shapes, especially the ones that broke before.
CASES = [
    "10.1001/archneurol.2010.292",     # ordinary single-slash
    "10.1093/jpepsy/jsy104",           # multi-slash (OSF / OUP style)
    "10.1023/a:1018769825030",         # colon (old Springer/Kluwer) — must round-trip
    "10.1023/a:1021350122677",         # colon that previously decoded to a slash
    "10.3758/s13428-021-01694-3",      # literal hyphens must be preserved
    "10.17605/osf.io/e9d3k",           # OSF triple segment
]


def test_doi_folder_roundtrip():
    for doi in CASES:
        folder = doi_to_folder(doi)
        assert ":" not in folder, f"colon leaked into folder name for {doi}"
        assert folder_to_doi(folder) == doi, f"round-trip failed for {doi}"


def test_colon_encodes_to_tilde_not_slash():
    # The historical bug: ':' and '/' both became '--', so a colon DOI decoded
    # to a slash DOI. They must stay distinct.
    assert doi_to_folder("10.1023/a:123") == "10.1023--a~123"
    assert folder_to_doi("10.1023--a~123") == "10.1023/a:123"
    assert folder_to_doi("10.1023--a--123") == "10.1023/a/123"  # slash stays slash


def test_dedup_suffix_stripped():
    assert folder_to_doi("10.1001--foo (1)") == "10.1001/foo"
