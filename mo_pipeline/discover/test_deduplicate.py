from mo_pipeline.discover import deduplicate_candidates as d


def _row(**kw):
    return {"doi": "", "pmid": "", "title": "", "source_api": "x", "source_query": "q", **kw}


def test_a_bridging_row_merges_two_groups():
    rows = [_row(doi="10.1000/x", title="first paper title long enough"),
            _row(pmid="123", title="a different title also long enough"),
            _row(doi="10.1000/x", pmid="123")]
    assert len(d.deduplicate(rows)) == 1


def test_unrelated_rows_stay_apart_and_short_titles_do_not_link():
    rows = [_row(doi="10.1000/a", title="short"), _row(doi="10.1000/b", title="short"),
            _row(doi="10.1000/c", title="a sufficiently long distinct title")]
    assert len(d.deduplicate(rows)) == 3
