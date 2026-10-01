import originals as o


def test_double_slash_and_truncated_tail():
    assert o._canon("10.1037//0021-843X.108.3.511") == o._canon("10.1037/0021-843x.108.3.511")
    s = o.score_paper({"10.1111/j.1467-7687.2009.00859.x"}, {"10.1111/j.1467-7687.2009.00859"})
    assert not s["hit"] and s["hit_lenient"]


def test_set_scores_and_summary():
    a = o.score_paper({"10.1/a", "10.1/b"}, {"10.1/a", "10.1/c"})
    assert a["tp"] == 1 and a["recall"] == 0.5 and a["precision_lb"] == 0.5 and a["hit"] and not a["exact"]
    none = o.score_paper({"10.1/a"}, set())
    m = o.summarize([a, none])
    assert m["n_papers"] == 2 and m["extracted_none"] == 1 and m["hit_rate"] == 0.5
    assert abs(m["micro_recall"] - 1 / 3) < 1e-9
