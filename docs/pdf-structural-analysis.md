# PDF structural analysis

This implementation adds a bounded, source-hash-bound PDF inspection report to the existing investigation workflow. It extends the current upload, evidence storage, audit, visual review and dossier features.

## Use

The Visual Forensics page offers **Analyze PDF structure** for a selected PDF. The report remains bound to the original document ID and recorded SHA-256. Re-running creates another immutable analysis record. The investigation Overview displays analyzed PDF and page counts.

Authenticated API routes:

- `POST /api/v1/investigations/{investigation_id}/documents/{document_id}/pdf-forensics` — inspect and persist a new report. Requires the normal update permission and CSRF token.
- `GET /api/v1/investigations/{investigation_id}/documents/{document_id}/pdf-forensics/latest` — return the most recent saved report.
- `GET /api/v1/investigations/{investigation_id}/pdf-forensics/summary` — return persisted coverage counts and the latest run per PDF.
- `GET /api/v1/investigations/{investigation_id}/pdf-forensic-artifacts/{artifact_id}` — retrieve an extracted image after ownership and hash checks; access is audited.

All routes enforce investigation ownership. Analysis verifies the stored source hash before parsing. The original source is not modified. Completed and partial outputs are included in later versioned dossier snapshots and JSON, HTML and PDF exports.

## Recorded facts

- Header and catalog PDF version where available; page count, page sizes, rotation, media box and crop box.
- Parser version, cross-reference entry count, parser-reported revision count, encryption and recovery state.
- Metadata exposed by PyMuPDF and whether an XMP metadata stream is present.
- Indirect object number, parser-exposed type and stream flag, with a bounded inventory.
- Native text span, font name/size, baseline origin and page-space bounding box.
- Font resource details and whether font bytes could be extracted by the parser; embedding status remains unavailable when extraction could not determine it.
- Embedded image dimensions, filter, color-space label, hash of extracted image bytes, object reference and page placements/transforms. Eligible image bytes are saved as separately hashed private artifacts and can be previewed in the workspace.
- Annotations, form widgets, embedded file names/sizes and PDF object references resembling JavaScript names.
- Detected signature objects are labeled `present_unverified`.

Object contents and document actions are never executed. Image artifacts have their own database row, private storage key, MIME type, byte size, dimensions and SHA-256, and point back to the source hash, document, PDF object number and analysis run. Retrieval checks investigation ownership and rehashes the stored bytes before returning them. The UI never receives a private filesystem path.

## States and limits

- `completed`: the parser opened the PDF without reporting recovery.
- `partial`: PyMuPDF reports parser recovery; detailed recovery diagnostics are not exposed.
- `unavailable`: the PDF is encrypted and no password was supplied.
- Malformed input or parser errors return an API error; no forensic observation is fabricated.

The analysis is limited to 250 pages and 50,000 indirect objects. Per-page text spans are capped at 500 and 20,000 per document; image references are capped at 500 per page and 2,000 per document, placements at 200 per object and 10,000 per document, and the recorded object inventory at 10,000 entries. Embedded image hashing is skipped above 40 million pixels; extracted images are capped at 25 MiB each and 100 MiB per run. The uploaded source has the existing 25 MiB limit. Analysis currently runs in a worker thread; hard process-level parser timeouts are not implemented.

PyMuPDF is not represented as a byte-perfect PDF validator. Revision count does not include revision-by-revision object diffs, newly introduced/replaced/freed object lists, or revision-specific metadata comparisons. Signature cryptographic verification, byte-range coverage, certificate-chain validation, and signer identity/trust checks are unavailable. Metadata values, object overlaps, revision changes and parser recovery are descriptive facts and do not establish tampering or intent.

PDF OCR currently lacks persisted pixel geometry for reliable native-text alignment. The application does not manufacture OCR/text overlap by scaling coordinates without that evidence. A later stage should extend the OCR worker to report verified rendered dimensions and coordinate transforms before adding disagreement observations.

## Provenance and research basis

The report records its schema version, parser name/version and source SHA-256. It is stored separately from the immutable source and included in the immutable dossier snapshot. Review the PDF Association's [forensic analysis challenges](https://pdfa.org/challenges-in-the-forensic-analysis-of-pdf-files) when interpreting parser behavior, cross-reference data, incremental updates and signatures. Evidence retention follows the preservation goal in [NISTIR 8387](https://nvlpubs.nist.gov/nistpubs/ir/2022/NIST.IR.8387.pdf): retain the original and trace derived results to it.

TruFor is a separate optional image-forensics component. Its official [LICENSE.txt](https://github.com/grip-unina/TruFor/blob/main/test_docker/LICENSE.txt) permits use only for informational and nonprofit purposes and prohibits unauthorized industrial or profit-oriented use. This application does not include a TruFor worker or model weights. The runtime now also requires both `VISUAL_FORENSICS_ENABLED=true` and an explicit `TRUFOR_LICENSE_CLEARED=true` attestation before contacting a worker; checkpoint terms and the intended use must be cleared before setting that gate.
