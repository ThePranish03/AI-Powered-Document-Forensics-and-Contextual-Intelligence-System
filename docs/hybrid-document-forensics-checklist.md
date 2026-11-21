# DocForensIQ hybrid document-forensics upgrade checklist

Repository audit: 2026-11-20  
Branch: `chore/incremental-project-publication`  
Audited commit: `d7d022f` (`testing the visual forensic feature`)

## Baseline

- [x] Confirmed this is the existing DocForensIQ checkout and the requested branch.
- [x] Confirmed the Git worktree was clean before the upgrade (`git status --short --branch`).
- [x] Recorded recent history: `d7d022f`, `5803b57`, `b6f34eb`, `2670f3a`, `1dcf477`.
- [x] Read the attached reference pack as historical context only; its reconstructed audit is not proof of current behavior and does not override the user's request.
- [x] No user changes needed preserving were present in the initial working tree.

## Existing implementation found

- Evidence upload already streams to private storage, enforces a 25 MiB limit, checks file signatures, records upload metadata and SHA-256, and checks the source hash on retrieval.
- PDF is an accepted upload format. PyMuPDF is a production dependency; page previews and deterministic PDF/image screening already exist.
- OCR results and source-linked evidence pages/spans are persisted. Visual review stores runs, artifacts, page geometry, candidate regions, reviewer dispositions and audit events.
- TruFor has a feature-gated worker adapter and explicit unavailable states. The current checkout does not contain a TruFor worker or verified checkpoint, and no genuine TruFor output is available.
- Findings, cross-document correlation, investigation overview, immutable dossier snapshots, JSON/HTML/PDF exports and an audit chain already exist.
- Alembic currently ends at `0010_visual_region_review`.

## Gaps against the requested target

- [x] Add a persistent, source-hash-bound PDF structural analysis record with authenticated API entry points and audit events.
- [x] Inspect parser-visible PDF structure, page text geometry, fonts, image objects/placements and hashes, annotations, widgets, attachments, metadata, revision count and parser-recovery state; label unsupported or unverified checks.
- [x] Include PDF structure coverage in the investigation overview and the report in immutable dossier snapshots/exports.
- [x] Add synthetic clean, incremental-save, hash-mismatch and encrypted-PDF service tests.
- [x] Preserve eligible embedded image extractions as separately hashed, source-linked private artifacts; enforce pixel and byte limits and hash-check on access.
- [ ] Compare native text against OCR using validated page geometry and persist explainable discrepancy observations.
- [ ] Correlate PDF structural, text/OCR, visual and cross-document observations with source provenance.
- [x] Add PDF structural analysis coverage and page counts to the investigation overview.
- [x] Add the new structural analysis record to versioned dossier snapshots and JSON/HTML/PDF exports.
- [ ] Add the full requested synthetic PDF fixture matrix, malformed/oversized cases and benchmark reporting. Current service tests cover only a subset and are not benchmark results.
- [ ] Review all upload and parser paths for resource limits, timeouts and format detection gaps.
- [ ] Revisit the four investigation areas for distinct workflows, coverage states and provenance links.
- [x] Document the new analysis workflow, API routes, migration, provenance, limits, licensing and remaining gaps in the README and technical reference.

## Implementation record

### Stage 0 — audit

Complete. The detailed baseline and known gaps are recorded above. Existing features are being extended rather than rebuilt.

### Stage 1 — evidence and provenance

- Existing source hash, private storage key, upload status, source metadata, ownership checks and audit events are reused.
- [x] Bind each PDF forensic run to the document ID and immutable source SHA-256.
- [x] Expose persisted structural reports only through investigation ownership-checked routes.
- [x] Keep prior structural runs immutable; latest reports do not replace older records.
- [x] Persist extracted images under private run-scoped storage and verify their hashes on retrieval.
- [x] Require an explicit default-off TruFor license-clearance setting before any worker call.

### Stage 2 — PDF structure

- [x] Persist a bounded structural report and parser status.
- [x] Record supported facts and explicit limitations without treating an incremental update or metadata value as proof of manipulation.
- [x] Report encrypted PDFs as unavailable when no password is supplied.
- [ ] Revision-by-revision object diffs and cryptographic signature verification remain unavailable until a suitable, verified implementation is added.

### Stages 3–9 — remaining work

Not complete. Continue from the unchecked items above; do not report acceptance criteria as met until each is implemented and verified.

### Verification record

- Backend regression suite: **75 passed**.
- Frontend: **typecheck, lint, 26 tests and production build passed**.
- Migrations: fresh isolated SQLite `upgrade head` through `0012_pdf_forensic_artifacts` succeeded.
- Browser: dossier version 2 JSON export completed; audit chain verified **8 of 8 events**.
- Benchmark: naive “any incremental object change means manipulated” heuristic, n=5: precision 0.50, recall 1.00, FPR 0.667 (TP=2, FP=2, FN=0, TN=1). pyHanko invalid-signature detection on signed subset, n=2: precision 1.00, recall 1.00, FPR 0.00 (TP=1, FP=0, FN=0, TN=1). Fixture-level results only.
- `git diff --check`: passed; Git emitted only LF-to-CRLF working-copy notices.

## TruFor gate

The official [TruFor LICENSE.txt](https://github.com/grip-unina/TruFor/blob/main/test_docker/LICENSE.txt) allows use only for informational and nonprofit purposes and prohibits unauthorized industrial or profit-oriented activities. This checkout has no cleared inference worker or model checkpoint. The adapter now requires both `VISUAL_FORENSICS_ENABLED=true` and `TRUFOR_LICENSE_CLEARED=true`, which defaults false. Keep the gate closed unless intended use and checkpoint terms are explicitly cleared; never synthesize output maps or scores.
