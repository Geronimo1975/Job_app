"""
Teste pentru indexul de joburi văzute — dedupe cu memorie între rulări.
"""

import json

import pytest

from store.seen import (
    APLICAT,
    RESPINS_PREFILTRU,
    SeenIndex,
    company_title_key,
    normalize_url,
    url_key,
)


class TestNormalizeUrl:
    def test_lowercases_host_only(self):
        assert normalize_url("https://Example.COM/Jobs/Dev") == "https://example.com/Jobs/Dev"

    def test_strips_trailing_slash(self):
        assert normalize_url("https://example.com/jobs/") == "https://example.com/jobs"

    def test_drops_tracking_params(self):
        assert (
            normalize_url("https://example.com/j?utm_source=x&jk=abc123&from=serp")
            == "https://example.com/j?jk=abc123"
        )

    def test_keeps_meaningful_params_sorted(self):
        assert (
            normalize_url("https://example.com/j?b=2&a=1")
            == "https://example.com/j?a=1&b=2"
        )

    def test_drops_fragment(self):
        assert normalize_url("https://example.com/j#apply") == "https://example.com/j"

    def test_two_tracking_variants_collapse_to_one(self):
        a = normalize_url("https://example.com/j?jk=1&utm_source=a")
        b = normalize_url("https://example.com/j?jk=1&utm_source=b")
        assert a == b

    def test_empty_url_is_safe(self):
        assert normalize_url("") == ""


class TestKeys:
    def test_company_title_key_is_slugified(self):
        job = {"company": "Test GmbH & Co.", "title": "Python Developer (m/w/d)"}
        assert company_title_key(job) == "test-gmbh-co|python-developer-m-w-d"

    def test_url_key_uses_normalized_url(self):
        job = {"url": "https://Example.com/j/?utm_source=x"}
        assert url_key(job) == "https://example.com/j"


class TestSeenIndex:
    def test_new_index_is_empty(self, tmp_path):
        idx = SeenIndex.load(tmp_path / "seen.json")
        assert len(idx) == 0

    def test_unseen_job_is_not_seen(self, tmp_path):
        idx = SeenIndex.load(tmp_path / "seen.json")
        assert idx.is_seen({"url": "https://a.com/1", "company": "A", "title": "T"}) is False

    def test_marked_job_is_seen(self, tmp_path):
        idx = SeenIndex.load(tmp_path / "seen.json")
        job = {"url": "https://a.com/1", "company": "A", "title": "T"}
        idx.mark(job, RESPINS_PREFILTRU)
        assert idx.is_seen(job) is True

    def test_same_job_with_tracking_params_is_seen(self, tmp_path):
        idx = SeenIndex.load(tmp_path / "seen.json")
        idx.mark({"url": "https://a.com/1?jk=9", "company": "A", "title": "T"}, APLICAT)
        assert idx.is_seen({"url": "https://a.com/1?jk=9&utm_source=z", "company": "A", "title": "T"})

    def test_same_job_on_another_board_is_seen_via_company_title(self, tmp_path):
        idx = SeenIndex.load(tmp_path / "seen.json")
        idx.mark({"url": "https://boardA.com/1", "company": "ACME", "title": "Python Dev"}, APLICAT)
        assert idx.is_seen({"url": "https://boardB.com/9", "company": "ACME", "title": "Python Dev"})

    def test_persists_across_reload(self, tmp_path):
        path = tmp_path / "seen.json"
        idx = SeenIndex.load(path)
        idx.mark({"url": "https://a.com/1", "company": "A", "title": "T"}, APLICAT)
        idx.save()

        reloaded = SeenIndex.load(path)
        assert reloaded.is_seen({"url": "https://a.com/1", "company": "A", "title": "T"})

    def test_save_writes_valid_json(self, tmp_path):
        path = tmp_path / "seen.json"
        idx = SeenIndex.load(path)
        idx.mark({"url": "https://a.com/1", "company": "A", "title": "T"}, APLICAT)
        idx.save()

        data = json.loads(path.read_text(encoding="utf-8"))
        entry = data["https://a.com/1"]
        assert entry["decision"] == APLICAT
        assert "first_seen" in entry and "last_seen" in entry

    def test_mark_twice_keeps_first_seen(self, tmp_path):
        idx = SeenIndex.load(tmp_path / "seen.json")
        job = {"url": "https://a.com/1", "company": "A", "title": "T"}
        idx.mark(job, RESPINS_PREFILTRU)
        first = idx.entries["https://a.com/1"]["first_seen"]
        idx.mark(job, APLICAT)
        assert idx.entries["https://a.com/1"]["first_seen"] == first
        assert idx.entries["https://a.com/1"]["decision"] == APLICAT

    def test_seed_urls_marks_them_seen(self, tmp_path):
        idx = SeenIndex.load(tmp_path / "seen.json")
        added = idx.seed_urls(["https://a.com/1?utm_source=x", "https://b.com/2"])
        assert added == 2
        assert idx.is_seen({"url": "https://a.com/1", "company": "", "title": ""})

    def test_seed_urls_ignores_empty(self, tmp_path):
        idx = SeenIndex.load(tmp_path / "seen.json")
        assert idx.seed_urls(["", None, "  "]) == 0

    def test_filter_new_removes_seen_and_intra_batch_duplicates(self, tmp_path):
        idx = SeenIndex.load(tmp_path / "seen.json")
        idx.mark({"url": "https://a.com/1", "company": "A", "title": "T"}, APLICAT)

        batch = [
            {"url": "https://a.com/1", "company": "A", "title": "T"},   # deja văzut
            {"url": "https://a.com/2", "company": "B", "title": "U"},   # nou
            {"url": "https://a.com/2?utm_source=q", "company": "B", "title": "U"},  # dublură în lot
        ]
        fresh = idx.filter_new(batch)

        assert len(fresh) == 1
        assert fresh[0]["url"] == "https://a.com/2"

    def test_filter_new_does_not_mark(self, tmp_path):
        """filter_new doar filtrează; marcarea e decizia apelantului."""
        idx = SeenIndex.load(tmp_path / "seen.json")
        job = {"url": "https://a.com/1", "company": "A", "title": "T"}
        idx.filter_new([job])
        assert idx.is_seen(job) is False

    def test_corrupt_file_starts_empty(self, tmp_path):
        path = tmp_path / "seen.json"
        path.write_text("{ nu e json valid", encoding="utf-8")
        assert len(SeenIndex.load(path)) == 0

    def test_save_leaves_no_temp_file(self, tmp_path):
        path = tmp_path / "seen.json"
        idx = SeenIndex.load(path)
        idx.mark({"url": "https://a.com/1", "company": "A", "title": "T"}, APLICAT)
        idx.save()

        assert path.exists()
        assert list(tmp_path.glob("*.tmp")) == []

    def test_failed_save_leaves_no_temp_file(self, tmp_path):
        """Chiar și când scrierea crapă, temporarul nu rămâne în urmă."""
        from unittest.mock import patch

        path = tmp_path / "seen.json"
        idx = SeenIndex.load(path)
        idx.mark({"url": "https://a.com/1", "company": "A", "title": "T"}, APLICAT)

        with patch("store.seen.json.dump", side_effect=RuntimeError("disc plin")):
            with pytest.raises(RuntimeError):
                idx.save()

        assert list(tmp_path.glob("*.tmp")) == []

    def test_json_of_wrong_type_starts_empty(self, tmp_path):
        """JSON valid dar care nu e obiect nu trebuie să crape la primul mark()."""
        path = tmp_path / "seen.json"
        path.write_text("[]", encoding="utf-8")

        idx = SeenIndex.load(path)

        assert len(idx) == 0
        idx.mark({"url": "https://a.com/1", "company": "A", "title": "T"}, APLICAT)
        assert idx.is_seen({"url": "https://a.com/1", "company": "A", "title": "T"})

    def test_failed_save_leaves_previous_index_intact(self, tmp_path):
        """Dacă serializarea crapă, fișierul vechi trebuie să rămână valid."""
        from unittest.mock import patch

        path = tmp_path / "seen.json"
        idx = SeenIndex.load(path)
        idx.mark({"url": "https://a.com/1", "company": "A", "title": "T"}, APLICAT)
        idx.save()

        idx.mark({"url": "https://a.com/2", "company": "B", "title": "U"}, APLICAT)
        with patch("store.seen.json.dump", side_effect=RuntimeError("disc plin")):
            with pytest.raises(RuntimeError):
                idx.save()

        # Vechiul index e neatins și încă încărcabil.
        reloaded = SeenIndex.load(path)
        assert reloaded.is_seen({"url": "https://a.com/1", "company": "A", "title": "T"})

    def test_corrupt_file_warns_on_stderr(self, tmp_path, capsys):
        path = tmp_path / "seen.json"
        path.write_text("{ nu e json valid", encoding="utf-8")

        SeenIndex.load(path)

        assert "Index dedupe ilizibil" in capsys.readouterr().err

    def test_company_title_elimination_is_logged(self, tmp_path, capsys):
        idx = SeenIndex.load(tmp_path / "seen.json")
        idx.mark({"url": "https://boardA.com/1", "company": "ACME", "title": "Python Dev"}, APLICAT)

        idx.filter_new([{"url": "https://boardB.com/9", "company": "ACME", "title": "Python Dev"}])

        assert "Eliminat pe firmă+titlu" in capsys.readouterr().err

    def test_url_elimination_is_not_logged(self, tmp_path, capsys):
        """Potrivirea pe URL e sigură — nu merită zgomot."""
        idx = SeenIndex.load(tmp_path / "seen.json")
        idx.mark({"url": "https://a.com/1", "company": "A", "title": "T"}, APLICAT)

        idx.filter_new([{"url": "https://a.com/1", "company": "A", "title": "T"}])

        assert "Eliminat pe firmă+titlu" not in capsys.readouterr().err
