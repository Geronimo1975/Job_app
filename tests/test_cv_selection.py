"""
Alegerea CV-ului după limba jobului.

Profilul are două CV-uri echivalente — `Lebenslauf` (DE) și `Resume` (EN).
Limba vine din piață (`market.language` → col „Sprache"), aceeași care comandă
limba Anschreiben-ului: un Anschreiben german însoțit de un CV englezesc e o
inconsecvență pe care o vede imediat cel care citește candidatura.
"""

import os

from agents.base_agent import (
    PROJECT_ROOT,
    cv_path_for,
    job_language,
    package_is_stale,
)


CFG = {
    "documents": {
        "cv_files": {
            "de": "documents/cv/Lebenslauf.pdf",
            "en": "documents/cv/Resume.pdf",
        },
        "cv_file": "documents/cv/Resume.pdf",
    }
}


class TestCvPathForLanguage:
    def test_german_gets_the_lebenslauf(self):
        assert cv_path_for(CFG, "de").name == "Lebenslauf.pdf"

    def test_english_gets_the_resume(self):
        assert cv_path_for(CFG, "en").name == "Resume.pdf"

    def test_path_is_absolute_under_project_root(self):
        assert cv_path_for(CFG, "de").is_absolute()
        assert str(cv_path_for(CFG, "de")).startswith(str(PROJECT_ROOT))

    def test_unknown_language_falls_back_to_cv_file(self):
        """O piață nouă (ex. `nl`) nu trebuie să rămână fără CV."""
        assert cv_path_for(CFG, "nl").name == "Resume.pdf"

    def test_language_matching_ignores_case_and_spacing(self):
        assert cv_path_for(CFG, " DE ").name == "Lebenslauf.pdf"

    def test_missing_language_falls_back_to_cv_file(self):
        assert cv_path_for(CFG, None).name == "Resume.pdf"
        assert cv_path_for(CFG, "").name == "Resume.pdf"

    def test_config_without_cv_files_still_works(self):
        """Compatibilitate: o config veche, fără `cv_files`, merge neschimbată."""
        old = {"documents": {"cv_file": "documents/cv/Vechi.pdf"}}
        assert cv_path_for(old, "de").name == "Vechi.pdf"
        assert cv_path_for(old, "en").name == "Vechi.pdf"

    def test_config_without_documents_block_has_a_default(self):
        assert cv_path_for({}, "de").name.endswith(".pdf")


class TestJobLanguage:
    def test_analysis_language_wins(self):
        """Analiza AI a citit anunțul — știe mai bine decât piața."""
        job = {"language": "en", "analysis": {"language": "de"}}
        assert job_language(job) == "de"

    def test_falls_back_to_the_market_language(self):
        """Fără analiză (`--step scrape` singur), limba piaței e tot ce avem.
        Înainte se cădea direct pe „de", deci un job de pe o piață engleză
        primea email și CV în germană."""
        assert job_language({"language": "en"}) == "en"

    def test_defaults_to_german(self):
        assert job_language({}) == "de"

    def test_empty_values_do_not_win(self):
        assert job_language({"language": "en", "analysis": {"language": ""}}) == "en"
        assert job_language({"language": "", "analysis": {}}) == "de"

    def test_real_pipeline_shape(self):
        """Forma reală produsă de `make_job` + AnalyzerAgent."""
        de_job = {"title": "AI Engineer", "language": "de", "market": "de"}
        en_job = {"title": "SEO Specialist", "language": "en", "market": "remote_global"}
        assert cv_path_for(CFG, job_language(de_job)).name == "Lebenslauf.pdf"
        assert cv_path_for(CFG, job_language(en_job)).name == "Resume.pdf"


class TestStalePackageIsNotSent:
    """„Bewerbung Komplett" are CV-ul LIPIT înăuntru la generare, iar
    `_collect_attachments` îl preferă și îl trimite singur. Un pachet generat
    înainte de schimbarea CV-ului trimite deci CV-ul VECHI, tăcut — exact ce
    s-a întâmplat cu jobul din 23 iulie după ce CV-urile au fost înlocuite.
    """

    def _touch(self, path, when):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-1.4\n")
        os.utime(path, (when, when))
        return path

    def test_package_older_than_the_cv_is_stale(self, tmp_path):
        pkg = self._touch(tmp_path / "Komplett.pdf", 1000)
        cv = self._touch(tmp_path / "cv.pdf", 2000)
        assert package_is_stale(pkg, cv) is True

    def test_package_newer_than_the_cv_is_fresh(self, tmp_path):
        cv = self._touch(tmp_path / "cv.pdf", 1000)
        pkg = self._touch(tmp_path / "Komplett.pdf", 2000)
        assert package_is_stale(pkg, cv) is False

    def test_a_newer_certificate_also_makes_it_stale(self, tmp_path):
        """Certificatele sunt la fel de lipite în pachet ca CV-ul."""
        cv = self._touch(tmp_path / "cv.pdf", 1000)
        pkg = self._touch(tmp_path / "Komplett.pdf", 2000)
        cert = self._touch(tmp_path / "certs" / "z.pdf", 3000)
        assert package_is_stale(pkg, cv, cert) is True

    def test_missing_package_counts_as_stale(self, tmp_path):
        cv = self._touch(tmp_path / "cv.pdf", 1000)
        assert package_is_stale(tmp_path / "nu-exista.pdf", cv) is True

    def test_missing_source_is_ignored(self, tmp_path):
        """O sursă inexistentă nu poate învechi nimic."""
        pkg = self._touch(tmp_path / "Komplett.pdf", 2000)
        assert package_is_stale(pkg, tmp_path / "nu-exista.pdf") is False

    def test_no_sources_means_fresh(self, tmp_path):
        pkg = self._touch(tmp_path / "Komplett.pdf", 2000)
        assert package_is_stale(pkg) is False
