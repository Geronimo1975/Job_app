"""
Teste pentru statisticile per piață — funcții pure, fără disc.
"""

from store.market_stats import aggregate, render


def _job(market, prescore=50, email_hr="", applied=False):
    return {
        "market": market,
        "prescore": prescore,
        "email_hr": email_hr,
        "applied": applied,
    }


class TestAggregate:
    def test_groups_by_market(self):
        stats = aggregate([_job("de"), _job("de"), _job("ro_remote")])
        assert stats["de"]["count"] == 2
        assert stats["ro_remote"]["count"] == 1

    def test_avg_prescore(self):
        stats = aggregate([_job("de", prescore=40), _job("de", prescore=60)])
        assert stats["de"]["avg_prescore"] == 50.0

    def test_email_coverage_counts_only_nonempty(self):
        stats = aggregate([
            _job("de", email_hr="bewerbung@acme.de"),
            _job("de", email_hr=""),
            _job("de"),  # cheie lipsă
        ])
        assert stats["de"]["with_email"] == 1
        assert stats["de"]["pct_email"] == round(100 / 3, 1)

    def test_applied_count(self):
        stats = aggregate([_job("de", applied=True), _job("de", applied=False)])
        assert stats["de"]["applied"] == 1

    def test_missing_market_bucketed(self):
        stats = aggregate([_job(None)])
        assert "(fără piață)" in stats

    def test_non_numeric_prescore_ignored(self):
        stats = aggregate([_job("de", prescore="n/a")])
        assert stats["de"]["avg_prescore"] == 0.0

    def test_empty_input(self):
        assert aggregate([]) == {}


class TestRender:
    def test_empty(self):
        assert "niciun job" in render({})

    def test_sorted_by_email_coverage(self):
        jobs = [
            _job("ro", email_hr=""),
            _job("ro", email_hr=""),
            _job("de", email_hr="bewerbung@acme.de"),
        ]
        out = render(aggregate(jobs))
        # `de` (100% email) trebuie listat înaintea lui `ro` (0%).
        assert out.index("de") < out.index("ro")
        assert "TOTAL" in out
