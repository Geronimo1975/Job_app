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

    def test_email_is_not_required_by_default(self, tmp_path):
        """Fără flag, comportamentul rămâne cel vechi — altfel orice config
        existentă și-ar goli poarta la prima rulare după update."""
        seen = SeenIndex.load(tmp_path / "seen.json")
        kept, stats = apply_prefilter_gate(
            [_job("https://a.com/1", "Python Django Developer")], CFG, seen
        )
        assert len(kept) == 1
        assert stats["fara_email"] == 0

    def test_gate_second_run_drops_previously_kept(self, tmp_path):
        """A doua rulare a porții pe același index nu re-trimite jobul reținut."""
        seen = SeenIndex.load(tmp_path / "seen.json")
        job = _job("https://a.com/1", "Python Django Developer")
        kept1, _ = apply_prefilter_gate([job], CFG, seen)
        kept2, stats2 = apply_prefilter_gate([dict(job)], CFG, seen)
        assert len(kept1) == 1
        assert kept2 == []
        assert stats2["deja_vazute"] == 1


class TestRequireEmail:
    """`require_email: true` — fără adresă de contact nu există destinatar.
    EmailAgent loga „lipsă email" și sărea rândul, deci jobul ocupa un rând în
    Sheet fără să poată fi trimis vreodată."""

    CFG_MAIL = {
        "skills": CFG["skills"],
        "scraping": {"filters": {**CFG["scraping"]["filters"], "require_email": True}},
    }

    def test_job_without_email_is_dropped(self, tmp_path):
        seen = SeenIndex.load(tmp_path / "seen.json")
        kept, stats = apply_prefilter_gate(
            [_job("https://a.com/1", "Python Django Developer")], self.CFG_MAIL, seen
        )
        assert kept == []
        assert stats["fara_email"] == 1
        assert stats["respinse"] == 0        # respins pentru email, nu pentru scor

    def test_job_with_email_is_kept(self, tmp_path):
        seen = SeenIndex.load(tmp_path / "seen.json")
        job = {**_job("https://a.com/1", "Python Django Developer"),
               "email_hr": "bewerbung@testco.de"}
        kept, stats = apply_prefilter_gate([job], self.CFG_MAIL, seen)
        assert len(kept) == 1
        assert stats["fara_email"] == 0

    def test_blank_email_counts_as_missing(self, tmp_path):
        seen = SeenIndex.load(tmp_path / "seen.json")
        job = {**_job("https://a.com/1", "Python Django Developer"), "email_hr": "   "}
        kept, stats = apply_prefilter_gate([job], self.CFG_MAIL, seen)
        assert kept == []
        assert stats["fara_email"] == 1

    def test_irrelevant_job_is_counted_as_rejected_not_as_missing_email(self, tmp_path):
        """Ordinea contează: scorul se evaluează primul, ca statistica să spună
        de ce a căzut jobul de fapt."""
        seen = SeenIndex.load(tmp_path / "seen.json")
        kept, stats = apply_prefilter_gate(
            [_job("https://a.com/1", "Truck Driver")], self.CFG_MAIL, seen
        )
        assert kept == []
        assert stats["respinse"] == 1
        assert stats["fara_email"] == 0

    def test_job_dropped_for_missing_email_is_remembered(self, tmp_path):
        seen = SeenIndex.load(tmp_path / "seen.json")
        job = _job("https://a.com/1", "Python Django Developer")
        apply_prefilter_gate([job], self.CFG_MAIL, seen)
        assert seen.entries["https://a.com/1"]["decision"] == RESPINS_PREFILTRU


class TestRequireEmailPerMarket:
    """Poarta trebuie să respecte scutirea pe piață, nu doar flagul global —
    altfel `require_email: true` ar goli exact piețele fără email (RO)."""

    CFG_MIXT = {
        "skills": CFG["skills"],
        "scraping": {
            "filters": {**CFG["scraping"]["filters"], "require_email": True},
            "markets": {
                "de": {"country": "germany"},
                "ro_remote": {"country": "romania", "require_email": False},
            },
        },
    }

    def test_exempt_market_keeps_jobs_without_email(self, tmp_path):
        seen = SeenIndex.load(tmp_path / "seen.json")
        job = {**_job("https://a.com/1", "Python Django Developer"), "market": "ro_remote"}
        kept, stats = apply_prefilter_gate([job], self.CFG_MIXT, seen)
        assert len(kept) == 1
        assert stats["fara_email"] == 0

    def test_strict_market_still_drops_jobs_without_email(self, tmp_path):
        seen = SeenIndex.load(tmp_path / "seen.json")
        job = {**_job("https://a.com/2", "Python Django Developer"), "market": "de"}
        kept, stats = apply_prefilter_gate([job], self.CFG_MIXT, seen)
        assert kept == []
        assert stats["fara_email"] == 1

    def test_both_markets_in_one_batch(self, tmp_path):
        """Regula se aplică per job, nu o dată pentru tot lotul."""
        seen = SeenIndex.load(tmp_path / "seen.json")
        # Firme diferite: altfel al doilea job cade pe dedupe firmă+titlu,
        # nu pe regula de email, și testul ar verifica altceva.
        batch = [
            {**_job("https://a.com/3", "Python Django Developer"),
             "company": "RoCo", "market": "ro_remote"},
            {**_job("https://a.com/4", "Python Django Developer"),
             "company": "DeCo", "market": "de"},
        ]
        kept, stats = apply_prefilter_gate(batch, self.CFG_MIXT, seen)
        assert [j["market"] for j in kept] == ["ro_remote"]
        assert stats["fara_email"] == 1
