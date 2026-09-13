---
name: intake-documents
description: Ingest and manage a folder of CV/application Word, PDF, or Markdown documents into the CV Studio intake pipeline. Use when the user wants to "intake", "dump", "import", or "manage" CV/application files or documents from Downloads or another folder.
---

# Document intake (CV Studio)

Intake = turn a loose pile of application letters, job postings, and CVs
into a classified, staged, reviewable manifest before anything touches the
library or the CV.

## When to use

Trigger words: "intake", "dump these", "import docs", "word files",
"application files", "manage my documents", pointing at `~/Downloads`, a
folder, or named `.docx`/`.pdf` files of CV/application material.

Use the dedicated **intake** agent (`.opencode/agents/intake.md`) when one
is available, or follow this workflow directly.

## Workflow

1. **Scan.** From the repo root run:
   ```
   .venv/bin/python3 scripts/intake.py scan --folder <folder>
   ```
   Supported types: `.docx`, `.pdf`, `.pptx`, `.md`, `.markdown`, `.txt`.
   Output: staged Markdown in `data/intake/scanned/` and a review manifest
   at `data/intake/manifest.yaml`.

2. **Review with the user.** Show the per-file table. Classifications:
   - `application` — cover letters (markers: "jeg søger", "when you hire",
     "ansøgning"…)
   - `posting` — job ads ("ansvarsområder", "we offer", "ansøgningsfrist"…)
   - `cv` — résumés (filename or parsed work-history)
   - `other` — unrelated files, propose to skip
   Confirm the proposed profile (`jens` / `agnes` / `susanne`) for every
   unassigned row. Never guess a person from letter content.

3. **Apply** (only after user confirmation):
   ```
   .venv/bin/python3 scripts/intake.py apply --manifest data/intake/manifest.yaml
   ```
   Archives originals + Markdown into
   `data/intake/archive/<profile>/{originals,applications,postings,cvs,misc}/`
   ready for the future profile-aware library seed.

4. **Report** per profile what was archived/skipped.

## Constraints

- `data/intake/` is gitignored — personal documents stay out of git.
- Never touch the README; do not re-seed the snippet library from intake
  content without explicit permission.
- Reuse `src/cvbuilder/intake.py` and `src/cvbuilder/resume_extractor.py`;
  do not reimplement DOCX/PDF text extraction.