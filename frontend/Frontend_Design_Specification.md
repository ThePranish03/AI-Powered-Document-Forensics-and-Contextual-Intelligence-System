# DocForensIQ
## AI-Powered Document Forensics & Contextual Intelligence Platform — Frontend UX/UI Design Specification

**Version:** 2.0 (Expanded standards, evidence custody, explainability, security and ethics coverage)<br>
**Purpose:** Frontend implementation blueprint for an investigator-focused document forensics workstation.<br>
**Primary UX principle:** Every finding must make three things immediately clear: **what was found, where it was found, and why it matters**.

---

## 1. Product UX direction

The frontend is an **Investigator Workstation**, not a generic document management dashboard. It should support a complete investigation flow:

**Upload → Processing Progress → Extracted Data → Evidence Viewer → Cross-Document Compare → Analysis → Dossier**

The audit trail runs in the background throughout the workflow.

### Design goals
- Evidence-first: show original document, page and exact region for every finding.
- Explainable: provide reason, supporting evidence and calibrated confidence.
- Human-in-the-loop: investigators can Accept, Dismiss or mark Needs verification.
- Safe language: never show “Fraud confirmed.” Use only:
  - **Observed characteristic**
  - **Detected inconsistency**
  - **Potential manipulation**
- Honest failure states: communicate when a file cannot be reliably processed.
- Adaptive analysis: dynamically activate relevant checks based on detected relationships and discrepancies; do not imply that every check runs on every case.
- Traceability: preserve source document, page, bounding box, extracted values and audit events.

### Out of scope for this frontend
- OCR model training interface.
- General-purpose information extraction model training.
- Automatic final fraud verdict.
- Replacing investigator judgment.
- Presenting OCR confidence as proof of correctness.

---

## 2. Information architecture

### Main navigation
1. **Cases / Investigations**
2. **New Investigation**
3. **Analysis Workspace**
4. **Dossiers / Reports**
5. **Audit Trail**
6. **Limitations & System Status**
7. **Settings** (optional; role-gated)

### Investigation workspace tabs
- Overview
- Documents & Extracted Data
- Evidence Viewer
- Cross-Document Compare
- Relationship Map
- Analysis Findings
- Timeline
- Dossier
- Audit Trail

Keep the primary navigation compact. Tabs belong to a selected investigation.

---

## 3. User journey

1. Investigator creates or opens an investigation.
2. Uploads multiple PDF or image documents.
3. Reviews file list, file types, generated document IDs and file hashes.
4. Starts investigation.
5. Watches processing stages and per-document status.
6. Reviews extracted fields and OCR confidence.
7. Opens Evidence Viewer to inspect findings in context.
8. Compares values across documents.
9. Explores entity/document relationships and adaptive analysis findings.
10. Accepts, dismisses or requests verification of findings.
11. Generates and exports an evidence-linked dossier.
12. Audit trail records each action.

---

## 4. Screen specifications

## Screen 1 — Cases / Investigation List

### Purpose
Help investigators find, resume and create cases.

### Layout
- Header: product mark, global search, notifications, user/profile.
- Page title: “Investigations”.
- Primary CTA: **New Investigation**.
- Filter row: status, date range, investigator, finding label.
- Table/list:
  - Investigation ID
  - Title / subject
  - Document count
  - Processing status
  - Findings count
  - Last updated
  - Assigned investigator
  - Actions: Open

### States
- Empty state: “No investigations yet” + New Investigation CTA.
- Loading skeleton.
- Error state with retry.
- Search/filter no-results state.

---

## Screen 2 — Upload

### Purpose
Collect documents and make the input set transparent before processing.

### Layout
**Top:** Investigation title, short instructions, privacy/retention note if applicable.<br>
**Main:** Large drag-and-drop zone supporting PDF, PNG, JPG, JPEG, TIFF (only supported types should be shown).<br>
**Below:** Upload queue table.

### Upload queue columns
- File name
- File type
- Size
- Page count (when available)
- Generated Document ID
- SHA-256 hash (copy action; optionally truncated with expand)
- Upload status
- Remove (before investigation starts)

### Controls
- Add files
- Clear queue (confirm dialog)
- **Start Investigation** (disabled until at least one valid file exists)
- Cancel

### Validation and safe messages
- Unsupported type: “This file type is not supported. Upload PDF, PNG, JPG, JPEG or TIFF.”
- File too large: state the configured size limit.
- Corrupt/unreadable: “This file could not be read reliably. Try another copy.”
- Duplicate hash: “This file appears identical to a document already in this investigation.” Offer inspect/keep or remove; do not silently discard.
- Upload failure: show filename, reason and retry.

### UX details
- Show file-level upload progress.
- Do not claim that a hash proves authenticity; it is an integrity identifier.
- Generated IDs and hashes must be copyable.

---

## Screen 3 — Processing Progress

### Purpose
Make the processing pipeline understandable and reveal per-stage failures.

### Layout
- Investigation header and overall progress.
- Vertical stepper with stage state: Pending / Running / Done / Failed / Needs review.
- Document-level status table.
- Expandable stage details.

### Suggested 10 stages
1. File validation & integrity hash
2. PDF/page rendering and input normalization
3. Document classification
4. OCR text detection and recognition
5. OCR quality assessment
6. Information extraction
7. Entity resolution & relationship mapping
8. Adaptive cross-document analysis
9. Visual evidence examination
10. Findings aggregation & dossier preparation

### Stage detail
- Stage name
- Current state
- Start/end timestamp and duration
- Documents processed / total
- Short technical status
- Error message when failed
- Retry action where safe

### Status behavior
- Failed stage must not be shown as Done.
- Show partial results if some documents succeed.
- Low-quality OCR: “Text could not be reliably recognized on this page. Review the original or retry processing.”
- Avoid fake progress percentages. Use stage progress only when backend reports it.

---

## Screen 4 — Extracted Data

### Purpose
Allow the investigator to review extracted fields before relying on analysis.

### Layout
- Document cards/list on left or top.
- Selected document details on main panel.
- Page selector and source preview.
- Field table.

### Document card
- Document type (predicted; label as “Model prediction” if not confirmed)
- Document ID
- Pages
- OCR status
- Quality indicator
- Extracted field count
- Review status

### Field table columns
- Field name
- Extracted value
- Confidence (number + horizontal bar)
- Source page
- Source region / “View evidence”
- Review status

### Confidence presentation
- High: neutral/greenish indicator (threshold configurable and empirically calibrated)
- Medium: amber indicator
- Low: **Uncertain** badge
- Missing/unsupported: “Not extracted”
- Always explain that confidence is model confidence, not truth probability unless calibrated.

### Interactions
- Click field → open Evidence Viewer at source page and bounding box.
- Edit/correct value only with explicit permission; log original and revised values in audit trail.
- Filters: all fields, uncertain, missing, reviewed.

---

## Screen 5 — Evidence Viewer (Highest Priority)

### Purpose
The primary demo and investigator screen. A finding should navigate to its exact original evidence location.

### Desktop layout
Three regions:
1. **Document canvas** (left, about 55–60%)
2. **Findings list** (right top)
3. **Finding detail panel** (right bottom)

Responsive behavior: on narrow screens, switch to stacked panels with a persistent finding/document context header.

### Document canvas
- Original page image (preserve original; do not show only enhanced image).
- Page navigation: previous/next, page number.
- Zoom in/out, fit page, fit width.
- Overlay bounding boxes for finding regions.
- Selected finding box highlighted; other findings subdued.
- “Original / OCR-preprocessed” toggle when both are retained.
- Optional heatmap toggle only if a real model output exists; do not fabricate heatmaps.
- Click box to select finding.
- Click finding to jump to page/region and briefly pulse/highlight box.
- Region coordinates should be mapped accurately to rendered page dimensions.
- Provide a text alternative for accessibility.

### Findings list
Each row:
- Finding ID (F-001)
- Safe label
- Short title
- Severity/priority (only if defined by policy)
- Confidence score
- Review state
- Source page indicator

### Finding detail panel
- Finding title and ID
- Label: Observed characteristic / Detected inconsistency / Potential manipulation
- Confidence score + bar + short explanation of what score means
- “What was found” statement
- “Why it was flagged” explanation
- Evidence references:
  - Document name and ID
  - Page
  - Bounding box / region
  - Extracted text/value
  - Comparison value/document if relevant
- Analysis method/check name
- Limitations / alternative explanations
- Review actions:
  - **Accept**
  - **Dismiss**
  - **Needs verification**
- Optional investigator note

### Review action semantics
- Accept = investigator accepts the finding as relevant, not a fraud verdict.
- Dismiss = investigator marks it not relevant/false positive, with optional reason.
- Needs verification = investigator requests additional review.
- Actions require confirmation when they materially change case status.
- Record actor, timestamp, prior state, new state and reason in audit trail.

### Color coding
- Blue: Observed characteristic
- Amber: Detected inconsistency
- Orange-red: Potential manipulation
- Green: consistent/matched information (not a finding label)
- Gray: unreviewed/neutral

Do not rely on color alone; use text labels and icons.

---

## Screen 6 — Cross-Document Compare (High Priority)

### Purpose
Make document-to-document conflicts easy to understand and navigate to evidence.

### Layout
- Investigation context and selected entity/person.
- Compare matrix.
- Filters: all fields, conflicts, variations, matches, uncertain.
- Optional “Compare selected documents” selector.

### Matrix structure
- Rows: canonical fields (name, DOB, address, ID number, account number, employer, salary, dates, etc.; dynamically determined from extracted data)
- Columns: documents
- Cell: extracted value, confidence, review status
- Row summary: consistency status

### Status colors and labels
- Green: Consistent
- Yellow: Minor variation / possible normalization difference
- Red: Conflict
- Gray: Missing / not extracted / uncertain

### Interaction
- Click a cell → Evidence Viewer opens corresponding document/page/region.
- Click row → show comparison explanation and matching method.
- Show normalization transparently (e.g. “Rahul S. Sharma” compared with “Rahul Sharma”; normalized tokens).
- Never treat a missing field as a conflict.
- Avoid comparing semantically unrelated fields simply because their labels look similar.

### Useful compare features
- Side-by-side source snippets.
- “Why these values were compared” explanation.
- Entity match confidence (separate from OCR confidence).
- Investigator override with audit event.

---

## Screen 7 — Relationship Map

### Purpose
Show the document/entity network discovered from extracted information.

### Main visualization
Interactive graph:
- Nodes: person, organization, account, address, document, transaction, contract, event (only entities supported by project data).
- Edges: owns, issued by, submitted by, references, associated with, paid to, etc.
- Node/edge labels and evidence references.
- Filters by entity type, document, finding status and relationship type.
- Search and focus selected entity.
- Click node/edge → side panel with details and source evidence.
- Click source evidence → Evidence Viewer.

### Side panel
- Entity/relationship name
- Extracted attributes
- Connected documents
- Evidence citations (document, page, region)
- Matching confidence where applicable
- Related findings

### Graph usability
- Avoid a tangled graph by default; start with selected entity and 1-hop neighbors.
- Expand relationships on demand.
- Offer list/table alternative for accessibility and dense graphs.
- Graph relationships are inferred/observed links, not proof by themselves.

---

## Screen 8 — Adaptive Analysis Findings

### Purpose
Show what analysis was triggered, the reason for it, and the result.

### Summary cards
- Documents analysed
- Relationships identified
- Checks run
- Potential discrepancies
- Evidence-supported findings
- Inconclusive checks

### Finding list
- Finding ID
- Safe label
- Check category
- Short explanation
- Confidence
- Related documents
- Review state

### Adaptive analysis transparency panel
For each finding, display:
- Trigger condition (e.g. “Salary values differed across linked documents”)
- Checks activated
- Documents/fields compared
- Outcome of each check: Supported / Not supported / Inconclusive / Not run
- Reason a check was not run (when relevant)
- Evidence links
- Alternative explanations and limitations

### Categories
- Intra-document consistency
- Cross-document comparison
- Entity resolution
- Temporal consistency
- Duplicate detection
- Visual characteristics / potential manipulation
- OCR uncertainty (as extraction-quality issue, not fraud finding)

### Important
The UI must distinguish **model-generated suggestion**, **system-supported finding**, and **human-reviewed decision**. Never present an algorithmic result as a confirmed fraud verdict.

---

## Screen 9 — Timeline Analysis

### Purpose
Present dates/events extracted from related documents in chronological order.

### Timeline item
- Date/time (and timezone if applicable)
- Event label
- Source document/page/region
- Confidence
- Related entity
- Potential chronological inconsistency indicator

### Controls
- Chronological / reverse chronological
- Filter by entity or document
- Click event → Evidence Viewer

### Safe interpretation
A date that appears out of order is a detected inconsistency; there may be legitimate explanations such as issue date vs effective date.

---

## Screen 10 — Dossier / Investigation Report

### Purpose
Generate a professional, evidence-linked report for review and export.

### Dossier sections (6 primary sections)
1. **Overview**
   - Investigation ID, creation date, document count, status
   - Summary of findings using safe labels
2. **Extracted Information**
   - Document inventory and reviewed extracted fields
   - OCR uncertainty and missing fields
3. **Contextual Analysis**
   - Cross-document comparison
   - Relationship findings
   - Temporal checks and adaptive checks performed
4. **Visual Findings**
   - Finding label, explanation, confidence
   - Embedded evidence crop and original page reference
5. **Evidence**
   - Finding-to-source mapping
   - Document ID, hash, page, region and extracted values
6. **Summary & Limitations**
   - Human review state
   - Inconclusive checks
   - Supported scope, unsupported inputs and limitations
   - Explicit statement: report does not independently establish fraud

### Export
- **Export PDF** primary CTA
- Optional JSON export for structured case data
- Preview before export
- Include page numbers, investigation ID and generated timestamp
- Export should preserve evidence references and safe labels

### PDF design
- Clean white report pages even if application theme is dark.
- Clear hierarchy, readable tables, page headers/footers.
- Findings numbered consistently (F-001 etc.).
- Images should include captions: document ID, page and region.
- Avoid oversized dashboard charts in the dossier; use compact summary visuals.

---

## Screen 11 — Audit Trail

### Purpose
Provide immutable-style activity history from the user perspective. Backend must enforce append-only semantics; frontend alone cannot guarantee immutability.

### Timeline events
- Investigation created
- Document uploaded
- Hash calculated
- Processing stage started/completed/failed
- Finding generated
- Finding accepted/dismissed/marked needs verification
- Field corrected
- Dossier exported

### Columns / event details
- Timestamp
- Actor (user/system)
- Action
- Object (document/finding/investigation)
- Before/after state when relevant
- Reason/comment
- Event ID

### UX
- Filters by actor, action, date, document.
- Search.
- Expand event for detail.
- No edit/delete controls.
- Export audit log only if authorized.

---

## Screen 12 — Limitations & System Status

### Purpose
Clearly disclose capabilities and project maturity.

### Status categories
- **Implemented**
- **Partial**
- **Planned**
- **Unsupported**

### Suggested table columns
- Capability
- Status
- Current behavior
- Limitation
- Planned improvement
- Last tested / version

### Example capability rows
- PDF and image ingestion
- OCR on printed text
- OCR on handwriting
- OCR language support
- Text detection and bounding boxes
- Cross-document field comparison
- Relationship graph
- Visual alteration analysis
- Confidence calibration
- Evidence-linked PDF dossier
- GPU acceleration
- Large file handling

### System status panel
- OCR engine/version
- Model configuration
- CPU/GPU mode
- Queue status
- Last successful processing
- Known service issues

Do not mark a feature Implemented unless it has been demonstrated and tested.

---


---

## 6. Added screens — forensic completeness, standards, safety and security

The following screens extend the existing 12-screen workflow. These UI elements are intended to make the project requirements demonstrable to evaluators. Standards are shown as **alignment references**, not as certification or a claim of formal compliance. Only show statuses and outcomes that the backend actually supports.

## Screen 13 — Evidence Acquisition & Chain of Custody

**Purpose:** Demonstrate evidence identification, collection, acquisition and preservation (ISO/IEC 27037-aligned workflow).

**Layout:** Two-column evidence details and chronological custody event log, with a compact evidence inventory.

**Evidence details:**
- Evidence ID and investigation ID
- Original filename, media type, byte size and page count
- Source / collection method and authorization reference
- Acquisition timestamp (timezone explicit), operator / actor
- SHA-256 hash, hash calculation status and copy action
- Original-preservation status and working-copy status
- Storage reference/access policy (never expose secret paths or credentials)
- Duplicate detection and integrity-check outcome

**Chain-of-custody event list:** Event ID, timestamp, actor, action, evidence ID, from/to custody state, reason, integrity check, and linked audit event. Event types include acquired, hash calculated, preserved, transferred, accessed, processed, exported and integrity rechecked.

**Rules:** Hashes support integrity checking; they do not prove authenticity. Preserve originals read-only where architecture permits. Backend controls must enforce access, append-only records and custody changes; the frontend cannot promise immutability by itself. Failed or missing custody data must be shown as “Not recorded” / “Needs review,” not green.

## Screen 14 — Explainability & Investigation Methodology

**Purpose:** Show why a finding exists, how the method was selected, and how evidence supports the interpretation (ISO/IEC 27041 and 27042 alignment).

**Finding explanation panel:**
- Finding ID and safe finding category
- Question / rule / analysis check evaluated
- Trigger condition and reason this check was activated
- Method name, method version and applicable scope
- Input documents, fields and source regions
- Step-by-step reasoning summary based on system evidence (no fabricated chain-of-thought)
- Observed values and normalized values, side-by-side
- Supporting and contradicting evidence
- Result: Supported / Not supported / Inconclusive / Not run
- OCR confidence, entity-match confidence and analysis confidence shown as distinct values
- Calibration note and confidence meaning
- Alternative benign explanations and method limitations
- Investigator decision, reviewer identity, review timestamp and note

**Method suitability panel:** Intended use, accepted input types, validation dataset/version, known limitations, test summary, and reason the method is considered suitable for this case. Show “Not assessed” if such validation is not available. Never present a model score as proof or probability of fraud unless it has been appropriately calibrated for that meaning.

## Screen 15 — AI Security & Privacy

**Purpose:** Showcase applicable OWASP Machine Learning Security Top 10 considerations and privacy safeguards without implying complete coverage of all OWASP risks.

**Security cards / status matrix:**
- Input validation: format, file size, parser and page limits
- Untrusted document content / prompt-injection risk flag, where applicable
- Model and pipeline name/version
- Output validation and schema checks
- Sensitive-data detection and masking status
- Access control / role authorization status
- Data retention policy and expiry state
- Secure processing and encryption status, only when backend-confirmed
- Dependency/model integrity status, only when checked
- Security event references and audit linkage
- OWASP ML risk mapping: risk identifier, applicability, control, status, evidence and residual limitation

**Privacy controls:** Mask/unmask gated by role; warning before exporting sensitive data; authorized dataset confirmation; data minimization notice; retention explanation; no real customer/investor data without appropriate authorization. Show “Not evaluated” where control evidence is absent. Avoid displaying secrets, credentials, or full sensitive identifiers by default.

## Screen 16 — Document Structure & Visual Forensics

**Purpose:** Showcase structural understanding and visual characteristic examination beyond text OCR.

**Main view:** Original document canvas with selectable layers: text blocks, key-value regions, tables, rows/cells, reading order, signatures/stamps, images and detected layout regions. The original page remains available at all times.

**Visual checks (show only implemented checks):**
- Font/style inconsistency indicators
- Alignment / spacing / baseline irregularities
- Image-region boundaries and possible compositing indicators
- Metadata discrepancies
- Repeated/duplicated visual regions
- OCR-text versus visible-source mismatch
- Table structure and reading-order anomalies

**Each visual observation includes:** check name/version, region coordinates, source page, observed characteristic, confidence and limitations. Use neutral labels such as “Observed characteristic” or “Potential manipulation”; do not claim alteration based on a single indicator. Include a comparison mode for original and processing derivative, with provenance of each derivative.

## Screen 17 — Standards, Safety, Ethics & Limitations

**Purpose:** Make the project’s standards references, responsible-use safeguards, and capability boundaries explicit to judges.

**Standards alignment matrix:**
- ISO/IEC 27037 — identification, collection, acquisition and preservation of digital evidence
- ISO/IEC 27041 — suitability and adequacy of investigative methods
- ISO/IEC 27042 — analysis and interpretation of digital evidence
- NIST SP 800-86 — forensic techniques integrated into incident response
- OWASP Machine Learning Security Top 10 — applicable AI/ML security risks

Matrix columns: requirement/reference, related system workflow or control, implementation state (Implemented / Partial / Planned / Not assessed), evidence or demonstration link, last validated version, and limitation. Include disclaimer: “Standards alignment is a project mapping; it is not a certification or independent conformity assessment.”

**Safety and ethics checklist:**
- Authorized documents/datasets only
- No real customer/investor data without appropriate authorization
- Sensitive information protected through access controls and masking where implemented
- OCR output is not verified ground truth
- Anomaly is not automatically confirmed fraud
- Findings explainable and traceable to source regions
- Human oversight for consequential decisions
- Processing/investigation activities recorded

**Required investigator acknowledgment:** Confirm authorization and data-handling responsibilities before processing. Store acknowledgment with actor, timestamp and investigation ID. Do not create a bypass that silently accepts the acknowledgement.

**Limitations panel:** Non-supported formats, language limitations, handwriting limitations, OCR uncertainty, unimplemented visual checks, calibration caveats, incomplete evidence links, and known failure modes. Provide an explicit “What this system cannot conclude” section.

## Screen 18 — Incident Response / Forensic Lifecycle

**Purpose:** Demonstrate how forensic techniques fit into an investigation process (NIST SP 800-86 reference).

Display a lifecycle with stage evidence and state: preparation / authorization, collection and preservation, examination and extraction, analysis and correlation, reporting, review / response handoff. The product may map its internal workflow to these stages but must not imply that it replaces incident response or legal procedures.

For each stage show responsible actor, action log, related evidence IDs, outputs, open issues and handoff. Keep response actions outside the system unless specifically authorized and implemented.

---

## 7. Requirement-to-UI traceability matrix

| Requirement | UI evidence / screen | Backend evidence required |
|---|---|---|
| Reliable information extraction | Extracted Data; Structure & Visual Forensics | OCR/extraction evaluation results, field provenance and confidence handling |
| Document structure understanding | Structure & Visual Forensics | Layout regions, table/reading order outputs |
| Visual anomaly identification | Evidence Viewer; Visual Forensics | Supported checks, region coordinates and validation evidence |
| Context-aware analysis | Adaptive Analysis Findings | Trigger logic, context and check outcome records |
| Cross-document correlation | Compare; Relationship Map | Entity linking, comparison provenance and match confidence |
| Evidence-backed findings | Evidence Viewer; Explainability | Source document/page/region and supporting/contradicting references |
| Observations vs manipulation | Findings; Dossier | Controlled taxonomy and safe language enforcement |
| Explainability | Explainability & Methodology | Method version, input references, outcome and limitations |
| Multiple formats | Upload; Limitations | Actual parser support and format-level test coverage |
| Transparent uncertainty | Extracted Data; Findings | Separate calibrated scores or explicitly labeled uncalibrated confidence |
| Dossier generation | Dossier; Reports | Reproducible evidence references, export metadata |
| ISO/IEC 27037 | Acquisition & Chain of Custody | Hash, acquisition metadata, preservation controls and custody events |
| ISO/IEC 27041 | Explainability & Methodology | Method suitability/validation documentation |
| ISO/IEC 27042 | Evidence Viewer; Explainability | Analysis and interpretation provenance |
| NIST SP 800-86 | Forensic Lifecycle | Stage evidence and response handoff mapping |
| OWASP ML Security Top 10 | AI Security & Privacy | Applicable risk mapping and control test results |
| Authorization/privacy | Upload; AI Security & Privacy | AuthZ, data retention, masking and security controls |
| Human oversight | Evidence Viewer; Findings | Review state, actor, timestamp and audit history |
| Auditability | Audit Trail; Chain of Custody | Append-only event storage and access controls |

## 8. Frontend acceptance criteria — evaluator demo

The UI is ready for an evaluator demonstration when a presenter can:
1. Create an investigation and visibly acknowledge document authorization.
2. Upload multiple authorized sample files and inspect file IDs, types, sizes and hash states.
3. Follow processing stages, including failures or inconclusive results without falsely marking them complete.
4. Open extracted fields and navigate to exact source-page regions.
5. Compare the same canonical field across documents and inspect evidence for each cell.
6. Open a finding and view its trigger, method, sources, supporting/contradicting evidence, confidence types, alternative explanations and limitations.
7. Explore document/entity links back to evidence.
8. Review acquisition and custody events without editing/deleting historical events.
9. Review relevant standards mapping and AI security/privacy controls with true implementation states.
10. Mark a finding Accept / Dismiss / Needs verification with a recorded reason and actor.
11. Generate a dossier containing safe labels, evidence mapping, uncertainty, human review status and limitations.
12. Export an audit trail/report only with authorized permissions.

**Demo data guidance:** Use synthetic or explicitly authorized documents. Sample names, account numbers, salary values and other data in mockups are illustrative and must not be treated as real case evidence.


## 9. Design system

### Visual direction
- Product identity: **DocForensIQ** — “Beyond OCR. Evidence-led document intelligence.”
- Landing page must communicate the project itself: document forensics, contextual intelligence, cross-document correlation, visual evidence examination, explainable findings, and dossier generation. Avoid generic tech/consumer-product hero imagery.
- Dark, premium, investigator-focused interface inspired by the supplied electric-blue flower / neon-lime reference.
- Use deep midnight navy/black backgrounds, luminous lime for primary actions and progress, electric blue for active navigation and evidence, and semantic amber/orange-red for findings.
- Inter font for UI; monospace for IDs, hashes, coordinates and numeric values.
- Surface hierarchy through spacing, borders and subtle contrast. Glows are restrained and reserved for focus/active controls; avoid distracting animations and gamification.

### Suggested palette (design tokens)
| Token | Suggested value | Use |
|---|---|---|
| App background | `#020814` | Midnight canvas / landing background |
| Primary surface | `#071426` | Panels and cards |
| Elevated surface | `#0D2038` | Popovers and detail panels |
| Border | `#1E4568` | Fine blue borders and dividers |
| Primary text | `#F3F7FF` | Main content |
| Secondary text | `#9FB4CC` | Metadata |
| Electric blue | `#3D9BFF` | Active navigation, evidence links, observed characteristics |
| Neon lime | `#B7FF00` | Primary CTA, progress, selected states |
| Consistent green | `#36D98A` | Match/consistent |
| Amber | `#FFC247` | Detected inconsistency / caution |
| Orange-red | `#FF684D` | Potential manipulation |
| Neutral gray | `#7B8DA4` | Pending/unknown |

Use semantic colors consistently. Ensure WCAG AA contrast where practical. Avoid color-only indicators.

### Typography
- Font: Inter (fallback system sans-serif).
- Page title: 24–28 px.
- Section heading: 16–20 px.
- Body: 14–16 px.
- Metadata: 12–13 px.
- IDs/hash: monospace 12–13 px.
- Use tabular numerals for metrics and confidence values.

### Spacing and components
- 4 px base spacing scale.
- Cards: 8–12 px radius.
- Inputs/buttons: 8 px radius.
- Primary button height: 40–44 px.
- Tables: compact but legible; sticky header for long data.
- Keep evidence viewer controls visible during scroll.
- Tooltips explain technical terms such as CER, WER, confidence and entity matching.

### Icons
Use a consistent line-icon set. Icons supplement labels; they do not replace them.

### Motion
- Subtle transitions only (100–180 ms).
- Selected evidence region may pulse once on navigation.
- Respect reduced-motion settings.
- No continuous decorative animation.

---

## 6. Shared components

Build reusable components:
- `InvestigationHeader`
- `StatusBadge`
- `ConfidenceBar`
- `DocumentCard`
- `DocumentUploadQueue`
- `ProcessingStepper`
- `ExtractedFieldTable`
- `EvidenceCanvas`
- `BoundingBoxOverlay`
- `FindingListItem`
- `FindingDetailPanel`
- `ReviewActionBar`
- `CrossDocumentMatrix`
- `RelationshipGraph`
- `AnalysisCheckCard`
- `TimelineEvent`
- `DossierSection`
- `AuditEvent`
- `EmptyState`
- `ErrorState`
- `LoadingSkeleton`
- `LimitationsTable`

### Shared data model (frontend contract)
```ts
type FindingLabel =
  | "Observed characteristic"
  | "Detected inconsistency"
  | "Potential manipulation";

type ReviewState =
  | "Unreviewed"
  | "Accepted"
  | "Dismissed"
  | "Needs verification";

type EvidenceReference = {
  documentId: string;
  documentName: string;
  pageNumber: number;
  boundingBox?: { x: number; y: number; width: number; height: number };
  extractedText?: string;
  fieldName?: string;
};

type Finding = {
  id: string;
  label: FindingLabel;
  title: string;
  explanation: string;
  confidence: number; // 0–1; display as percent only if calibration semantics are disclosed
  reviewState: ReviewState;
  evidence: EvidenceReference[];
  analysisMethod: string;
  limitations?: string[];
};
```

Confidence must have a documented meaning and be calibrated where it is presented as a probability. OCR confidence, entity-match confidence and finding confidence must remain separate metrics.

---

## 7. Responsive layout

### Desktop (primary demo target, ≥ 1280 px)
- Full navigation rail (about 220–240 px).
- Workspace header.
- Evidence Viewer: canvas + findings/detail split.
- Compare matrix uses horizontal scrolling with sticky field column.

### Laptop (1024–1279 px)
- Collapsible navigation.
- Evidence canvas around half width.
- Details panel narrower but scrollable.

### Tablet (768–1023 px)
- Navigation collapses to icons or drawer.
- Evidence canvas and findings switch to tabs/stack.
- Compare matrix scrolls horizontally.

### Mobile (< 768 px)
- Drawer navigation.
- Evidence Viewer becomes:
  1. Document canvas
  2. Findings list
  3. Finding details
- Sticky selected finding header and review actions.
- Do not attempt to show full wide comparison matrix without horizontal scroll.

The evaluation/demo should be optimized for desktop because evidence traceability and cross-document comparison are the key visual proof points.

---

## 8. Accessibility and usability

- Keyboard navigation for tabs, tables, graph controls and review actions.
- Visible focus indicators.
- Screen-reader labels for zoom, page navigation and review buttons.
- Text labels accompany semantic colors.
- Provide list/table alternative to relationship graph.
- Provide text transcript of highlighted evidence region.
- Do not use confidence color without numeric or verbal label.
- Confirmation dialog for destructive or consequential review actions.
- Use clear language; explain acronym on first use.

---

## 9. Empty, loading, error and partial-result states

### Empty
- No documents: “Upload documents to begin an investigation.”
- No findings: “No findings were generated by the checks performed.” Do not say “fraud-free.”
- No relationships: “No supported relationships were identified.”
- No timeline events: “No dates/events could be reliably extracted.”

### Loading
- Skeletons for tables and panels.
- Stage-based processing state.
- Preserve access to completed results while remaining stages run.

### Error
- Filename/document ID and human-readable reason.
- Retry where possible.
- Keep successful document results visible.
- Avoid exposing stack traces or secrets.

### Partial result
- Clearly mark documents/pages that failed.
- Show the analysis coverage (e.g. “4 of 5 documents processed”).
- Do not imply case-wide completeness when a page or document was unavailable.

---

## 10. Data visualization guidance

Use charts only when they help an investigator understand evidence or coverage.

### Useful charts
- Finding distribution by safe label.
- Processing status by document.
- Analysis coverage by check category.
- OCR quality summary (CER/WER only on labeled evaluation set; per-document OCR confidence can be shown separately).
- Timeline view.
- Relationship graph.

### Avoid
- Decorative donut charts without a decision purpose.
- “Fraud score” gauges.
- Unsupported risk percentages.
- Combining OCR confidence with anomaly confidence.
- Charts that imply an investigation conclusion without showing evidence.

---

## 11. Demo flow for judges (recommended 4–6 minutes)

1. **Upload** 3–5 sample documents; show generated IDs and hashes.
2. **Progress**: reveal stage-by-stage processing, including OCR and adaptive analysis.
3. **Extracted Data**: show fields and uncertain OCR values.
4. **Evidence Viewer**: click a finding; demonstrate exact page/region jump and highlighted box.
5. **Compare**: show a field match and a discrepancy across documents; click cell to navigate to source.
6. **Relationship Map**: focus on a selected person/entity and connected source documents.
7. **Analysis**: show which checks were triggered, why, and whether they were supported/inconclusive.
8. **Human review**: mark one finding Needs verification; show audit event.
9. **Dossier**: preview and export a report with evidence references and limitations.

The strongest demo moment is **finding → exact source region → comparison evidence → explanation → human review → audit trail**.

---

## 12. Priority plan

### P0 — Must build
1. Upload + validation + file inventory
2. Processing progress
3. Extracted data table with confidence and uncertainty
4. Evidence Viewer with bounding boxes, page navigation and finding details
5. Cross-Document Compare matrix with evidence links
6. Review actions and audit event creation

### P1 — Strongly recommended
7. Investigation overview summary
8. Adaptive Analysis Findings panel
9. Relationship Map
10. Dossier preview + PDF export
11. Limitations & System Status

### P2 — If time permits
12. Timeline analysis
13. Advanced graph filtering
14. Audit log export
15. User preferences and saved views

### Build order
Evidence Viewer first; then Compare matrix; then Upload/Extracted Data; then Analysis/Relationship Map; then Dossier; finally Progress, Audit and Limitations.

---

## 13. Acceptance criteria

Frontend is ready for demo when:
- Multi-file upload works and invalid files receive clear feedback.
- Every document has a stable ID and visible hash.
- Processing stages show truthful status.
- Extracted fields show confidence and uncertainty.
- Selecting a finding navigates to the exact original document page and region.
- Every finding displays one of the three permitted labels.
- Every finding includes explanation, confidence and evidence references.
- Accept / Dismiss / Needs verification actions work and are audit logged.
- Compare matrix distinguishes match, variation, conflict and missing/uncertain.
- Relationship graph nodes/edges open their supporting evidence.
- Dossier export contains source references and limitations.
- No UI states or language declare fraud confirmed.
- Partial failures are explicit and do not hide successful results.
- The system distinguishes OCR confidence, entity match confidence and finding confidence.

---

## 14. Final design recommendation

Treat the frontend as an **evidence-led investigation workstation**. Prioritize the Evidence Viewer and Cross-Document Compare because they make the project's value immediately visible. Keep every finding navigable to its source, every explanation transparent, every confidence score clearly defined, and every consequential decision under human review.

The user should never have to ask:
- What did the system find?
- Where is the source?
- Why was it flagged?
- How confident is the system?
- What can I do next?
