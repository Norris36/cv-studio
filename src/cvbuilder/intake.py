"""Classify and stage CV/application Word documents for the CV library.

Scans a folder of CV-related documents (Word/PDF/Markdown/plain text),
extracts plain text, classifies each file by type (application / posting /
cv / other) and proposes a profile, converts content to Markdown, and
stages everything under ``data/intake/`` behind a reviewable manifest. The
agent or user reviews the manifest before anything is applied.
"""

from __future__ import annotations

import argparse
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ruamel.yaml import YAML

from cvbuilder.resume_extractor import extract_text

YAML_ROUND = YAML(typ="safe")

SUPPORTED_EXTENSIONS = {".md", ".markdown", ".txt", ".pdf", ".docx", ".pptx"}

PROFILES = ("jens", "agnes", "susanne")

DOC_TYPES = ("application", "posting", "cv", "other")

_APPLICATION_MARKERS = (
    "jeg søger",
    "søger hermed",
    "når i ansætter",
    "when you hire",
    "i am applying",
    "i'm applying",
    "i write to apply",
    "i would like to apply",
    "ansøgning",
    "motiveret ansøgning",
)

_POSTING_MARKERS = (
    "ansvarsområder",
    "kvalifikationer",
    "vi tilbyder",
    "om stillingen",
    "jobbeskrivelse",
    "stillingsopslag",
    "jobannonce",
    "ansøgningsfrist",
    "tiltrædelse",
    "your responsibilities",
    "what you will do",
    "we offer",
    "about the role",
    "apply for this position",
    "responsibilities include",
    "requirements include",
)

_PROFILE_FILENAME_HINTS = {
    "jens": "jens",
    "agnes": "agnes",
    "susanne": "susanne",
    "hjern": "susanne",
    "hjerne": "susanne",
}

_CONTENT_TARGETS = {
    "application": "applications",
    "posting": "postings",
    "cv": "cvs",
    "other": "misc",
}

_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass
class DocumentScan:
    """One staged document with its classification and extracted text.

    Attributes:
        source: Absolute path of the original file.
        name: Original filename (basename).
        sha256: Hash of the original file bytes.
        text: Extracted plain text.
        markdown: Paragraph-normalised Markdown rendering of ``text``.
        md_name: Filename of the staged Markdown (set by ``scan_folder``).
        doc_type: One of ``DOC_TYPES``.
        profile: Proposed profile ('jens'|'agnes'|'susanne') or None.
        title: First meaningful paragraph (short).
        error: Extraction error message, when classification failed.
    """

    source: Path
    name: str = ""
    sha256: str = ""
    text: str = ""
    markdown: str = ""
    md_name: Optional[str] = None
    doc_type: str = "other"
    profile: Optional[str] = None
    title: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        """Return a manifest-friendly mapping for this scan."""
        return {
            "source": str(self.source),
            "name": self.name,
            "sha256": self.sha256,
            "md_name": self.md_name,
            "doc_type": self.doc_type,
            "profile": self.profile,
            "title": self.title,
            "chars": len(self.text),
            "words": len(self.text.split()),
            "error": self.error or None,
        }


def sha256_bytes(data: bytes) -> str:
    """Return the SHA-256 hex digest of a byte payload.
    ~~~
    - Hash raw file bytes for stable identity checks
    ~~~
    returns str: lowercase 64-char hex digest
    """
    return hashlib.sha256(data).hexdigest()


def make_slug(value: str) -> str:
    """Normalise a filename into a filesystem-safe lowercase slug.
    ~~~
    - Lowercase, strip non-alnum runs to single hyphens, trim edges
    - Fall back to 'document' when nothing remains
    ~~~
    returns str: slug safe for file paths
    """
    slug = _SLUG_RE.sub("-", str(value).lower()).strip("-")
    return slug or "document"


def _probe_head(markers: tuple[str, ...], text: str) -> int:
    """Count how many markers occur within the first 1500 characters.
    ~~~
    - Case-insensitive substring match against a head slice of the text
    ~~~
    returns int: number of matching markers (0..len(markers))
    """
    head = text[:1500].lower()
    return sum(1 for marker in markers if marker in head)


def filename_hints_doc_type(name: str) -> Optional[str]:
    """Guess a document type purely from its filename.
    ~~~
    - Match 'ansøgning'/'motivation' → application, 'cv' → cv, 'annonce' → posting
    - Return None when the name carries no signal
    ~~~
    returns Optional[str]: one of DOC_TYPES or None
    """
    lowered = name.lower()
    if "ansøg" in lowered or "motivation" in lowered or "cover letter" in lowered:
        return "application"
    if re.search(r"(^|[^a-z])(cv|curriculum)([^a-z]|$)", lowered):
        return "cv"
    if "stilling" in lowered or "annonce" in lowered or "job posting" in lowered:
        return "posting"
    return None


def filename_hints_profile(name: str) -> Optional[str]:
    """Propose a profile from a person-name hint in the filename.
    ~~~
    - Match known person tokens (jens, agnes, susanne, hjern*)
    - Return None when no person is recognised
    ~~~
    returns Optional[str]: 'jens'|'agnes'|'susanne' or None
    """
    lowered = name.lower()
    for token, profile in _PROFILE_FILENAME_HINTS.items():
        if token in lowered:
            return profile
    return None


def first_paragraph(text: str) -> str:
    """Return the first meaningful paragraph of a text block.
    ~~~
    - Split on blank lines, strip, pick the first non-empty line
    - Collapse internal newlines and truncate to 90 chars
    ~~~
    returns str: short title (may be empty)
    """
    for block in re.split(r"\n\s*\n", text):
        joined = " ".join(line.strip() for line in block.splitlines() if line.strip())
        if joined:
            return joined[:90]
    return ""


def to_markdown(text: str) -> str:
    """Convert extracted plain text into paragraph Markdown.
    ~~~
    - Split on blank lines, join each block on single spaces, blank-line separate
    ~~~
    returns str: Markdown body ('' for empty input)
    """
    paragraphs = [
        " ".join(line.strip() for line in block.splitlines() if line.strip())
        for block in re.split(r"\n\s*\n", text)
    ]
    paragraphs = [p for p in paragraphs if p]
    return "\n\n".join(paragraphs) if paragraphs else ""


def classify_document(name: str, text: str) -> tuple[str, Optional[str]]:
    """Classify a document into (type, profile) from filename and content.
    ~~~
    - Filename hints win for application/cv/posting when present
    - Otherwise score first-1500-chars markers for application vs posting
    - Propose a profile from person-name filename hints only
    ~~~
    returns tuple[str, Optional[str]]: (doc_type, profile or None)
    """
    doc_type = filename_hints_doc_type(name)
    if doc_type is None:
        app_score = _probe_head(_APPLICATION_MARKERS, text)
        posting_score = _probe_head(_POSTING_MARKERS, text)
        if app_score > posting_score:
            doc_type = "application"
        elif posting_score > app_score:
            doc_type = "posting"
        else:
            doc_type = "other"
    return doc_type, filename_hints_profile(name)


def scan_file(path: Path) -> DocumentScan:
    """Extract and classify a single document file.
    ~~~
    - Read raw bytes, hash them, extract text via resume_extractor
    - Classify type/profile, render Markdown, derive a short title
    - Capture extraction failures in the returned scan (error set)
    ~~~
    returns DocumentScan: populated scan record (never raises)
    """
    path = Path(path)
    scan = DocumentScan(source=path, name=path.name)
    try:
        data = path.read_bytes()
    except OSError as exc:
        scan.error = f"read failed: {exc}"
        return scan
    scan.sha256 = sha256_bytes(data)
    try:
        scan.text = extract_text(path.name, data)
    except ValueError as exc:
        scan.error = str(exc)
        return scan
    scan.markdown = to_markdown(scan.text)
    scan.doc_type, scan.profile = classify_document(path.name, scan.text)
    scan.title = first_paragraph(scan.text)
    return scan


def scan_folder(folder: Path, out_dir: Path) -> list[DocumentScan]:
    """Scan a folder of candidate files into staged Markdown + scans.
    ~~~
    - Walk ``folder`` non-recursively, filter by SUPPORTED_EXTENSIONS
    - Write each scan's Markdown to ``out_dir/<slug>-<sha8>.md``
    - Return all scans (including failed ones, with error set)
    ~~~
    returns list[DocumentScan]: one entry per candidate file
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    scans: list[DocumentScan] = []
    candidates = sorted(
        (p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS),
        key=lambda p: p.name.lower(),
    )
    for path in candidates:
        scan = scan_file(path)
        if scan.markdown:
            slug = make_slug(Path(scan.name).stem)
            scan.md_name = f"{slug}-{scan.sha256[:8]}.md"
            (out_dir / scan.md_name).write_text(scan.markdown, encoding="utf-8")
        scans.append(scan)
    return scans


def write_manifest(scans: list[DocumentScan], path: Path) -> None:
    """Write a reviewable manifest of scans to a YAML file.
    ~~~
    - Persist scan.to_dict() rows in scan order
    - Overwrite any previous manifest at ``path``
    ~~~
    returns None
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"scanned": [scan.to_dict() for scan in scans]}
    import io

    stream = io.StringIO()
    YAML_ROUND.dump(data, stream)
    path.write_text(stream.getvalue(), encoding="utf-8")


def load_manifest(path: Path) -> list[dict]:
    """Load a manifest YAML file back into manifest rows.
    ~~~
    - Read YAML, return the 'scanned' list (empty on missing file)
    ~~~
    returns list[dict]: manifest rows
    """
    if not path.is_file():
        return []
    loaded = YAML_ROUND.load(path.read_text(encoding="utf-8")) or {}
    return list(loaded.get("scanned") or [])


def content_target(doc_type: str) -> str:
    """Map a document type to its content-library folder name.
    ~~~
    - Translate application→applications, posting→postings, cv→cvs, else→misc
    ~~~
    returns str: folder segment for the future profile-aware seed
    """
    return _CONTENT_TARGETS.get(doc_type, "misc")


def apply_manifest(manifest_path: Path, archive_root: Path) -> list[dict]:
    """Copy confirmed scans into a per-profile, per-type archive.
    ~~~
    - Read manifest rows; skip rows with an existing error
    - Copy original file to archive/<profile|unassigned>/originals/
    - Copy its scanned Markdown to archive/<profile|unassigned>/<target>/
    - Never overwrite an existing destination (skipped actions are noted)
    ~~~
    returns list[dict]: one action record per row (copied/skipped/failed)
    """
    actions: list[dict] = []
    for row in load_manifest(manifest_path):
        if row.get("error"):
            actions.append({"name": row.get("name"), "action": "failed", "reason": row.get("error")})
            continue
        profile = row.get("profile") or "unassigned"
        target = content_target(row.get("doc_type", "other"))
        source = Path(str(row.get("source", "")))
        if not source.is_file():
            actions.append({"name": row.get("name"), "action": "failed", "reason": "source missing"})
            continue

        originals = archive_root / profile / "originals"
        target_dir = archive_root / profile / target
        originals.mkdir(parents=True, exist_ok=True)
        target_dir.mkdir(parents=True, exist_ok=True)

        original_dest = originals / source.name
        slug = make_slug(Path(source.stem)) + f"-{row.get('sha256', '')[:8]}"
        md_dest = target_dir / f"{slug}.md"
        md_name = row.get("md_name")
        md_source = (manifest_path.parent / "scanned" / md_name) if md_name else None

        copied = 0
        skipped = 0
        if original_dest.exists():
            skipped += 1
        else:
            _copyfile_preserve(source, original_dest)
            copied += 1
        if md_dest.exists():
            skipped += 1
        elif md_source is not None and md_source.is_file():
            _copyfile_preserve(md_source, md_dest)
            copied += 1
        else:
            skipped += 1

        status = "copied" if copied else ("skipped" if skipped else "failed")
        actions.append(
            {
                "name": row.get("name"),
                "profile": profile,
                "target": target,
                "original": str(original_dest),
                "markdown": str(md_dest),
                "action": status,
            }
        )
    return actions


def _copyfile_preserve(source: Path, dest: Path) -> None:
    """Copy a file keeping its modification timestamp.
    ~~~
    - shutil.copy2 semantics, imported locally to keep imports light
    ~~~
    returns None
    """
    import shutil

    shutil.copy2(source, dest)


def print_scan_summary(scans: list[DocumentScan], out_dir: Path) -> None:
    """Print a human-readable table of scan results to stdout.
    ~~~
    - Column layout: file, type, profile, chars, status
    - Note where staged Markdown was written
    ~~~
    returns None
    """
    print(f"\nScanned {len(scans)} document(s) into {out_dir}/")
    for scan in scans:
        status = scan.error or f"{scan.doc_type}"
        profile = scan.profile or "-"
        print(f"  {scan.name!r:60} {status:>12}  profile={profile:<8}  {len(scan.text)} chars")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Construct the intake CLI argument parser.
    ~~~
    - Subcommands: scan, apply, status
    - Each command takes a defaults overridable via flags
    ~~~
    returns ArgumentParser: fully configured parser
    """
    parser = argparse.ArgumentParser(prog="intake", description="CV document intake helper")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="scan a folder of documents into staging")
    scan.add_argument("--folder", default=str(Path.home() / "Downloads"), help="folder to scan")
    scan.add_argument("--out-dir", default="data/intake/scanned", help="staging dir for markdown")
    scan.add_argument("--manifest", default="data/intake/manifest.yaml", help="manifest output path")

    apply = sub.add_parser("apply", help="apply a confirmed manifest into the archive")
    apply.add_argument("--manifest", default="data/intake/manifest.yaml", help="manifest to apply")
    apply.add_argument("--archive", default="data/intake/archive", help="archive root dir")

    status = sub.add_parser("status", help="summarise what is staged in the archive")
    status.add_argument("--archive", default="data/intake/archive", help="archive root dir")
    return parser


def cmd_status(archive_root: Path) -> int:
    """Print a per-profile, per-type count of archived documents.
    ~~~
    - Walk the archive tree, count Markdown blobs under typed target dirs
    - Ignore the 'originals' folder (source files, not library content)
    - Print a compact table, then a total
    ~~~
    returns int: 0 on success
    """
    total = 0
    print("\nArchived intake (data/intake/archive):")
    for profile_dir in sorted(p for p in archive_root.iterdir() if p.is_dir()):
        for target_dir in sorted(p for p in profile_dir.iterdir() if p.is_dir()):
            if target_dir.name == "originals":
                continue
            count = len(list(target_dir.glob("*.md")))
            if count:
                total += count
                print(f"  {profile_dir.name:<12} {target_dir.name:<14} {count} document(s)")
    print(f"  total: {total}")
    return 0


def main() -> int:
    """CLI entry point for the intake helper.
    ~~~
    - Dispatch scan/apply/status, print summaries to stdout
    - Return 0 on success, 1 on unsupported command
    ~~~
    returns int: process exit code
    """
    args = build_parser().parse_args()
    if args.command == "scan":
        folder = Path(args.folder)
        out_dir = Path(args.out_dir)
        manifest = Path(args.manifest)
        if not folder.is_dir():
            print(f"error: not a directory: {folder}")
            return 1
        scans = scan_folder(folder, out_dir)
        write_manifest(scans, manifest)
        print_scan_summary(scans, out_dir)
    elif args.command == "apply":
        actions = apply_manifest(Path(args.manifest), Path(args.archive))
        for action in actions:
            print(f"  {action['action']:>8}  {action.get('name', '?')}  -> {action.get('markdown', '')}")
        print(f"Applied {len(actions)} scan(s).")
    elif args.command == "status":
        return cmd_status(Path(args.archive))
    else:
        print("unknown command")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())