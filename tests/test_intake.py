"""Tests for the CV document intake classifier and staging helpers."""

from __future__ import annotations

from pathlib import Path

from cvbuilder.intake import (
    classify_document,
    content_target,
    filename_hints_doc_type,
    filename_hints_profile,
    make_slug,
    scan_file,
    to_markdown,
)


class TestFilenameHints:
    """Verify filename-only classification signals."""

    def test_danish_application_filename(self) -> None:
        assert filename_hints_doc_type("Nykredit_ansøgning.docx") == "application"

    def test_english_cover_letter_filename(self) -> None:
        assert filename_hints_doc_type("Cover Letter - Tryg.docx") == "application"

    def test_cv_filename(self) -> None:
        assert filename_hints_doc_type("cv - projektleder - KK.end.docx") == "cv"

    def test_posting_filename(self) -> None:
        assert filename_hints_doc_type("stilling - UX-designer.docx") == "posting"

    def test_no_signal(self) -> None:
        assert filename_hints_doc_type("report.pdf") is None

    def test_profile_hint_by_name(self) -> None:
        assert filename_hints_profile("Communicator_JENS.docx") == "jens"
        assert filename_hints_profile("cv susanne.docx") == "susanne"
        assert filename_hints_profile("cv agnes.docx") == "agnes"
        assert filename_hints_profile("cover-letter.pdf") is None


class TestClassifyDocument:
    """Verify content-based classification of applications versus postings."""

    def test_application_letter_content(self) -> None:
        text = "Hej\n\nJeg søger stillingen, fordi jeg trives i komplekse processer."
        assert classify_document("untitled.docx", text)[0] == "application"

    def test_english_application_content(self) -> None:
        text = "When you hire me as your new consultant, you get an engaged colleague."
        assert classify_document("letter.docx", text)[0] == "application"

    def test_posting_content(self) -> None:
        text = "Vi tilbyder ansvarsområder med stort udviklingspotentiale. Ansøgningsfrist"
        assert classify_document("advert.docx", text)[0] == "posting"

    def test_mixed_posting_filename_wins(self) -> None:
        text = "Vi tilbyder et udfordrende job."
        assert classify_document("stilling - data.docx", text)[0] == "posting"

    def test_unrecognised_content_defaults_to_other(self) -> None:
        text = "Rapport for 3. kvartal viser framgang i nettoomsætningen."
        assert classify_document("report.docx", text)[0] == "other"


class TestStagingHelpers:
    """Verify slugging, markdown conversion, and content targets."""

    def test_make_slug_handles_danish_and_spaces(self) -> None:
        assert make_slug("Nykredit_ansøgning.docx") == "nykredit-ans-gning-docx"

    def test_make_slug_coerces_path_like_input(self) -> None:
        assert make_slug(Path("Some/Thing.Stem")) == "some-thing-stem"

    def test_to_markdown_joins_paragraphs(self) -> None:
        text = "First line.\nStill first.\n\nSecond paragraph."
        assert to_markdown(text) == "First line. Still first.\n\nSecond paragraph."

    def test_content_target_mapping(self) -> None:
        assert content_target("application") == "applications"
        assert content_target("posting") == "postings"
        assert content_target("cv") == "cvs"
        assert content_target("weird") == "misc"

    def test_scan_file_unsupported_extension_reports_error(self, tmp_path: Path) -> None:
        unsupported = tmp_path / "notes.zip"
        unsupported.write_bytes(b"MZ")
        scan = scan_file(unsupported)
        assert scan.error


class TestScanFile:
    """Verify a real scanned markdown file is extracted."""

    def test_scan_text_file(self, tmp_path: Path) -> None:
        source = tmp_path / "cv-susanne.md"
        source.write_text("CV Susanne\n\nProjektleder med stærke kompetencer.", encoding="utf-8")
        scan = scan_file(source)
        assert not scan.error
        assert "Projektleder" in scan.markdown
        assert len(scan.sha256) == 64