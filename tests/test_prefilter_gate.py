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


class TestRequireRemote:
    """`require_remote: true` — jobul care nu e remote nu are ce căuta în Sheet.

    Poarta e singurul loc care poate impune asta pe TOATE sursele:
    `market.remote` ajunge doar la jobspy, iar `ro_boards` (eJobs) nici măcar
    nu se uită la el, deci fără poartă un anunț on-site din eJobs ar intra în
    Sheet chiar cu `remote: true` în config.
    """

    CFG_REMOTE = {
        "skills": CFG["skills"],
        "scraping": {"filters": {**CFG["scraping"]["filters"], "require_remote": True}},
    }

    def test_non_remote_job_is_dropped(self, tmp_path):
        seen = SeenIndex.load(tmp_path / "seen.json")
        job = {**_job("https://a.com/1", "Python Django Developer"),
               "location": "București", "description": "Lucru la birou."}
        kept, stats = apply_prefilter_gate([job], self.CFG_REMOTE, seen)
        assert kept == []
        assert stats["nu_e_remote"] == 1
        assert stats["respinse"] == 0        # respins pentru remote, nu pentru scor

    def test_remote_job_is_kept(self, tmp_path):
        seen = SeenIndex.load(tmp_path / "seen.json")
        job = {**_job("https://a.com/1", "Python Django Developer"),
               "location": "Remote (de acasă)"}
        kept, stats = apply_prefilter_gate([job], self.CFG_REMOTE, seen)
        assert len(kept) == 1
        assert stats["nu_e_remote"] == 0

    def test_remote_is_not_required_by_default(self, tmp_path):
        """Fără flag, o config existentă nu are voie să-și golească poarta."""
        seen = SeenIndex.load(tmp_path / "seen.json")
        job = {**_job("https://a.com/1", "Python Django Developer"),
               "location": "București"}
        kept, stats = apply_prefilter_gate([job], CFG, seen)
        assert len(kept) == 1
        assert stats["nu_e_remote"] == 0

    def test_irrelevant_job_is_counted_as_rejected_not_as_non_remote(self, tmp_path):
        """Scorul rămâne primul: statistica trebuie să spună de ce a căzut."""
        seen = SeenIndex.load(tmp_path / "seen.json")
        kept, stats = apply_prefilter_gate(
            [_job("https://a.com/1", "Truck Driver")], self.CFG_REMOTE, seen
        )
        assert kept == []
        assert stats["respinse"] == 1
        assert stats["nu_e_remote"] == 0

    def test_non_remote_job_is_remembered(self, tmp_path):
        seen = SeenIndex.load(tmp_path / "seen.json")
        job = {**_job("https://a.com/1", "Python Django Developer"),
               "location": "București"}
        apply_prefilter_gate([job], self.CFG_REMOTE, seen)
        assert seen.entries["https://a.com/1"]["decision"] == RESPINS_PREFILTRU

    def test_remote_is_checked_before_email(self, tmp_path):
        """Un job nici remote nici cu email se numără o singură dată, la cauza
        cea mai de fond — altfel cele două contoare ar spune povești diferite."""
        seen = SeenIndex.load(tmp_path / "seen.json")
        cfg = {"skills": CFG["skills"],
               "scraping": {"filters": {**CFG["scraping"]["filters"],
                                        "require_remote": True, "require_email": True}}}
        job = {**_job("https://a.com/1", "Python Django Developer"),
               "location": "București"}
        kept, stats = apply_prefilter_gate([job], cfg, seen)
        assert kept == []
        assert stats["nu_e_remote"] == 1
        assert stats["fara_email"] == 0

    def test_all_three_conditions_together(self, tmp_path):
        """România + remote + email — exact combinația cerută."""
        seen = SeenIndex.load(tmp_path / "seen.json")
        cfg = {"skills": CFG["skills"],
               "scraping": {"filters": {**CFG["scraping"]["filters"]},
                            "markets": {"ro": {"require_remote": True,
                                               "require_email": True}}}}
        batch = [
            {**_job("https://a.com/1", "Python Django Developer"), "company": "A",
             "market": "ro", "location": "Remote", "email_hr": "hr@a.ro"},
            {**_job("https://a.com/2", "Python Django Developer"), "company": "B",
             "market": "ro", "location": "Remote"},
            {**_job("https://a.com/3", "Python Django Developer"), "company": "C",
             "market": "ro", "location": "București", "email_hr": "hr@c.ro"},
        ]
        kept, stats = apply_prefilter_gate(batch, cfg, seen)
        assert [j["url"] for j in kept] == ["https://a.com/1"]
        assert stats["nu_e_remote"] == 1
        assert stats["fara_email"] == 1


# Proză reală, ca detecția să lucreze pe ce apare efectiv în anunțuri.
_DESC_EN = ("You will work with our team on the platform and we expect that you "
            "have experience with Python and modern development practices for "
            "the role, as well as skills in Django and API design.")
_DESC_SV = ("Vi söker dig som vill bygga framtidens verktyg och som gillar att "
            "arbeta med moderna system. Du har erfarenhet av utveckling och vill "
            "arbeta tillsammans med ett härligt gäng kollegor på vårt kontor.")


class TestLanguageGate:
    """Poarta de limbă: userul citește EN/DE/RO, deci un anunț suedez e
    inutilizabil oricât de bine ar scora. Filtrul respinge doar pe dovadă
    pozitivă — „nu știu ce limbă e" trece mai departe."""

    CFG_LANG = {
        "skills": CFG["skills"],
        "scraping": {"filters": {**CFG["scraping"]["filters"],
                                 "allowed_languages": ["en", "de", "ro"]}},
    }

    def test_foreign_language_job_is_dropped(self, tmp_path):
        seen = SeenIndex.load(tmp_path / "seen.json")
        job = {**_job("https://a.com/1", "Python Django Developer", _DESC_SV)}
        kept, stats = apply_prefilter_gate([job], self.CFG_LANG, seen)
        assert kept == []
        assert stats["limba_straina"] == 1
        assert stats["respinse"] == 0        # respins pentru limbă, nu pentru scor

    def test_allowed_language_job_is_kept(self, tmp_path):
        seen = SeenIndex.load(tmp_path / "seen.json")
        job = {**_job("https://a.com/1", "Python Django Developer", _DESC_EN)}
        kept, stats = apply_prefilter_gate([job], self.CFG_LANG, seen)
        assert len(kept) == 1
        assert stats["limba_straina"] == 0

    def test_job_without_description_is_kept(self, tmp_path):
        """Cazul care contează cel mai mult: 77% dintre anunțuri n-au
        descriere. Poarta NU are voie să le taie pe un câmp gol (JOB-48)."""
        seen = SeenIndex.load(tmp_path / "seen.json")
        job = _job("https://a.com/1", "Python Django Developer")
        kept, stats = apply_prefilter_gate([job], self.CFG_LANG, seen)
        assert len(kept) == 1
        assert stats["limba_straina"] == 0

    def test_language_is_not_filtered_by_default(self, tmp_path):
        seen = SeenIndex.load(tmp_path / "seen.json")
        job = {**_job("https://a.com/1", "Python Django Developer", _DESC_SV)}
        kept, stats = apply_prefilter_gate([job], CFG, seen)
        assert len(kept) == 1
        assert stats["limba_straina"] == 0

    def test_foreign_language_job_is_remembered(self, tmp_path):
        seen = SeenIndex.load(tmp_path / "seen.json")
        job = {**_job("https://a.com/1", "Python Django Developer", _DESC_SV)}
        apply_prefilter_gate([job], self.CFG_LANG, seen)
        assert seen.entries["https://a.com/1"]["decision"] == RESPINS_PREFILTRU

    def test_language_is_checked_before_email(self, tmp_path):
        """Un anunț suedez fără email se numără la limbă, nu la email: limba e
        cauza de fond, adresa e doar felul în care s-ar aplica."""
        seen = SeenIndex.load(tmp_path / "seen.json")
        cfg = {"skills": CFG["skills"],
               "scraping": {"filters": {**CFG["scraping"]["filters"],
                                        "allowed_languages": ["en", "de", "ro"],
                                        "require_email": True}}}
        job = {**_job("https://a.com/1", "Python Django Developer", _DESC_SV)}
        kept, stats = apply_prefilter_gate([job], cfg, seen)
        assert kept == []
        assert stats["limba_straina"] == 1
        assert stats["fara_email"] == 0

    def test_per_market_override(self, tmp_path):
        """Regula e per piață, ca `require_email`."""
        seen = SeenIndex.load(tmp_path / "seen.json")
        cfg = {"skills": CFG["skills"],
               "scraping": {"filters": {**CFG["scraping"]["filters"],
                                        "allowed_languages": ["en"]},
                            "markets": {"se": {"allowed_languages": ["en", "sv"]}}}}
        # Piața `se` acceptă suedeza — dar detecția întoarce „alta", nu „sv",
        # deci un anunț suedez tot cade. Scutirea reală se face cu listă goală.
        job = {**_job("https://a.com/1", "Python Django Developer", _DESC_SV),
               "market": "se"}
        kept, _ = apply_prefilter_gate([job], cfg, seen)
        assert kept == []

    def test_market_can_disable_the_rule_with_an_empty_list(self, tmp_path):
        seen = SeenIndex.load(tmp_path / "seen.json")
        cfg = {"skills": CFG["skills"],
               "scraping": {"filters": {**CFG["scraping"]["filters"],
                                        "allowed_languages": ["en", "de", "ro"]},
                            "markets": {"se": {"allowed_languages": []}}}}
        job = {**_job("https://a.com/1", "Python Django Developer", _DESC_SV),
               "market": "se"}
        kept, stats = apply_prefilter_gate([job], cfg, seen)
        assert len(kept) == 1
        assert stats["limba_straina"] == 0
