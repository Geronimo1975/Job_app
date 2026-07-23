"""
Teste pentru poarta dintre scrape și analyze: dedupe + prefiltru.
"""

from store.seen import ANALIZAT, APLICAT, RESPINS_PREFILTRU, SeenIndex

from main import apply_prefilter_gate


CFG = {
    "skills": {"primary": ["Python", "Django"], "secondary": ["Docker"]},
    "scraping": {
        "filters": {"exclude_keywords": ["Director"], "min_prescore": 40},
    },
}


def _job(url, title, description=""):
    return {"url": url, "title": title, "company": "Co", "description": description}


class TestGate:
    def test_relevant_job_is_kept(self, tmp_path):
        seen = SeenIndex.load(tmp_path / "seen.json")
        kept, stats = apply_prefilter_gate(
            [_job("https://a.com/1", "Python Django Developer")], CFG, seen
        )
        assert len(kept) == 1
        assert stats["retinute"] == 1

    def test_irrelevant_job_is_dropped(self, tmp_path):
        seen = SeenIndex.load(tmp_path / "seen.json")
        kept, stats = apply_prefilter_gate([_job("https://a.com/1", "Truck Driver")], CFG, seen)
        assert kept == []
        assert stats["respinse"] == 1

    def test_excluded_title_is_dropped(self, tmp_path):
        seen = SeenIndex.load(tmp_path / "seen.json")
        kept, _ = apply_prefilter_gate(
            [_job("https://a.com/1", "Director of Python Engineering")], CFG, seen
        )
        assert kept == []

    def test_already_applied_job_is_dropped(self, tmp_path):
        seen = SeenIndex.load(tmp_path / "seen.json")
        seen.mark(_job("https://a.com/1", "Python Django Developer"), APLICAT)

        kept, stats = apply_prefilter_gate(
            [_job("https://a.com/1", "Python Django Developer")], CFG, seen
        )
        assert kept == []
        assert stats["deja_vazute"] == 1

    def test_rejected_job_is_remembered(self, tmp_path):
        """Un job respins nu trebuie re-evaluat la rularea următoare."""
        seen = SeenIndex.load(tmp_path / "seen.json")
        job = _job("https://a.com/1", "Truck Driver")
        apply_prefilter_gate([job], CFG, seen)
        assert seen.is_seen(job)
        assert seen.entries["https://a.com/1"]["decision"] == RESPINS_PREFILTRU

    def test_kept_job_carries_prescore_and_reasons(self, tmp_path):
        seen = SeenIndex.load(tmp_path / "seen.json")
        kept, _ = apply_prefilter_gate(
            [_job("https://a.com/1", "Python Django Developer")], CFG, seen
        )
        assert kept[0]["prescore"] >= 40
        assert "Python" in kept[0]["keywords_match"]

    def test_duplicate_in_same_batch_counted_once(self, tmp_path):
        seen = SeenIndex.load(tmp_path / "seen.json")
        job = _job("https://a.com/1", "Python Django Developer")
        kept, stats = apply_prefilter_gate([job, dict(job)], CFG, seen)
        assert len(kept) == 1
        assert stats["total"] == 2

    def test_empty_input_is_safe(self, tmp_path):
        seen = SeenIndex.load(tmp_path / "seen.json")
        kept, stats = apply_prefilter_gate([], CFG, seen)
        assert kept == []
        assert stats["total"] == 0

    def test_kept_job_is_remembered_as_analizat(self, tmp_path):
        """Simetric cu test_rejected_job_is_remembered: un job reținut nu trebuie
        re-trimis la AI la rularea următoare."""
        seen = SeenIndex.load(tmp_path / "seen.json")
        job = _job("https://a.com/1", "Python Django Developer")
        apply_prefilter_gate([job], CFG, seen)
        assert seen.is_seen(job)
        assert seen.entries["https://a.com/1"]["decision"] == ANALIZAT

    def test_keyless_jobs_counted_separately(self, tmp_path):
        """Un job fără URL și fără firmă+titlu nu e „deja văzut" — e de neurmărit."""
        seen = SeenIndex.load(tmp_path / "seen.json")
        keyless = {"url": "", "title": "", "company": "", "description": ""}
        kept, stats = apply_prefilter_gate([keyless], CFG, seen)
        assert kept == []
        assert stats["fara_cheie"] == 1
        assert stats["deja_vazute"] == 0

    def test_gate_second_run_drops_previously_kept(self, tmp_path):
        """A doua rulare a porții pe același index nu re-trimite jobul reținut."""
        seen = SeenIndex.load(tmp_path / "seen.json")
        job = _job("https://a.com/1", "Python Django Developer")
        kept1, _ = apply_prefilter_gate([job], CFG, seen)
        kept2, stats2 = apply_prefilter_gate([dict(job)], CFG, seen)
        assert len(kept1) == 1
        assert kept2 == []
        assert stats2["deja_vazute"] == 1
