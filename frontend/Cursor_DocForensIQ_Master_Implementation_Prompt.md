# Cursor Master Implementation Prompt — DocForensIQ
## S-Rank Frontend Build Brief | Read this entire prompt before coding

You are a senior product designer, staff frontend engineer, accessibility specialist, and design-systems architect. Build **DocForensIQ**, an investigator-grade, polished, responsive web application for AI-powered document forensics and contextual intelligence.

Your output must be a working frontend codebase—not just a plan, a static screenshot, or a collection of disconnected components. Prioritize a visually exceptional, cohesive and usable product. Implement the complete interface and user flows using realistic mock data and a clean API adapter layer where backend services are not yet available. Do not fabricate backend success or forensic verification.

---

# 1. Source materials and order of authority

Use these project materials:
1. `Frontend_Design_Specification.md` — primary UX specification. Follow its screen descriptions, workflow, terminology, safety language, and requirements.
2. `DocForensIQ_Final_UI_Screens.zip` — visual reference images. Inspect the overview and individual screen references; use them as visual direction, not as pixel-perfect authority where they conflict with usability/accessibility.
3. This Cursor prompt — technical implementation conventions and quality bar.

If the assets are not in the repository, ask the user to add them or inspect the available files. Do not pretend you inspected unavailable files. Do not replace the product brief with a generic admin template.

# 2. Product objective

DocForensIQ transforms document processing from simple OCR into evidence-driven investigation. It should visibly demonstrate:
- Multi-document intake and authorization
- File validation and integrity identifiers
- Document classification and structural understanding
- OCR and information extraction with uncertainty
- Original-document evidence review with exact page/region anchors
- Visual forensic observations
- Context-aware and adaptive analysis
- Cross-document comparison and entity correlation
- Relationship graph and timeline
- Explainable, evidence-backed findings
- Human-in-the-loop decisions
- Evidence acquisition and chain-of-custody record display
- Standards/methodology mapping
- Privacy and AI-security controls
- Audit trail
- Professional dossier/report export
- Honest limitations and system status

This is a decision-support investigation workstation. It is not a general file manager, not merely an OCR dashboard, and it must never present an automated fraud verdict.

# 3. Recommended technology stack

Use the following stack unless the repository already has a coherent established stack; preserve existing choices where sensible and explain deviations:
- **React 18+**
- **Vite**
- **TypeScript** with `strict: true`
- **Tailwind CSS**
- **shadcn/ui** (Radix primitives) for accessible building blocks
- **Lucide React** for icons; do not use emoji as interface icons
- **React Router** for route-based navigation
- **TanStack Query** for API/server state (even if mock adapter is used initially)
- **Zustand** for small shared client state (selected case, workspace state)
- **React Hook Form + Zod** for validated forms
- **Recharts** for simple data charts
- **React Flow (@xyflow/react)** for the entity/document relationship graph
- **PDF.js / react-pdf** for PDF document preview where dependency compatibility allows; implement an image/document preview fallback
- **date-fns** for date formatting
- **Vitest + React Testing Library** for unit/component tests
- **Playwright** for essential end-to-end flows
- ESLint + Prettier

Do not add large or redundant libraries without a concrete reason. Check the package manager and project conventions before installation. Pin compatible package versions as appropriate.

# 4. Required routes and screens

Implement these pages with a shared application shell, consistent side navigation, header, case context and breadcrumb behavior. Use route-level pages, not one giant conditional component.

1. `/` — Product landing / workspace entry
2. `/investigations` — Investigation list, search, filters, empty/loading/error states
3. `/investigations/new` — Create case + authorized multi-file upload
4. `/investigations/:caseId` — Case overview
5. `/investigations/:caseId/processing` — Stage-by-stage progress, document status, retry-safe failures
6. `/investigations/:caseId/classification` — Document type prediction and structure overview
7. `/investigations/:caseId/extracted-data` — Fields, confidence, source page/region, review state
8. `/investigations/:caseId/evidence` — Evidence viewer, source document, page controls, highlighted region, finding detail and review actions
9. `/investigations/:caseId/compare` — Cross-document field comparison table, filters and linked evidence
10. `/investigations/:caseId/relationships` — Interactive entity/document graph with selected entity detail
11. `/investigations/:caseId/findings` — Findings overview, severity/type filters, explanation and evidence links
12. `/investigations/:caseId/timeline` — Event/document chronology with source and confidence
13. `/investigations/:caseId/dossier` — Dossier sections, preview, completeness and export actions
14. `/investigations/:caseId/audit` — Append-only timeline, actor, timestamp, action and details
15. `/investigations/:caseId/chain-of-custody` — Acquisition, hash, source, custody events and preservation status
16. `/investigations/:caseId/explainability` — Finding-specific method, evidence, reasoning, confidence, alternatives and reviewer notes
17. `/investigations/:caseId/security-privacy` — Security, data protection and AI security controls/status
18. `/investigations/:caseId/standards` — Standards and methodology mapping with honest implementation status
19. `/investigations/:caseId/limitations` — Implemented / partial / planned / unsupported capability matrix
20. `/reports` — Reports list and export
21. `/settings` — Optional role-gated settings

Use a compact sidebar with groups:
- Workspace: Investigations, New Investigation, Reports
- Selected case: Overview, Processing, Extracted Data, Evidence Viewer, Compare, Relationship Map, Findings, Timeline, Dossier
- Governance: Audit Trail, Chain of Custody, Explainability, Standards & Methodology, Security & Privacy, Limitations

Do not overload the sidebar; support collapsed state and responsive mobile navigation.

# 5. Suggested repository structure

Create a clean, scalable structure similar to:

```text
docforensiq/
├── public/
│   ├── favicon.svg
│   └── assets/
├── src/
│   ├── app/
│   │   ├── App.tsx
│   │   ├── router.tsx
│   │   ├── providers.tsx
│   │   └── query-client.ts
│   ├── assets/
│   ├── components/
│   │   ├── ui/                    # shadcn/ui primitives
│   │   ├── layout/
│   │   │   ├── AppShell.tsx
│   │   │   ├── Sidebar.tsx
│   │   │   ├── Topbar.tsx
│   │   │   ├── Breadcrumbs.tsx
│   │   │   └── CaseHeader.tsx
│   │   ├── shared/
│   │   │   ├── PageHeader.tsx
│   │   │   ├── EmptyState.tsx
│   │   │   ├── ErrorState.tsx
│   │   │   ├── LoadingSkeleton.tsx
│   │   │   ├── StatusBadge.tsx
│   │   │   ├── ConfidenceMeter.tsx
│   │   │   ├── SourceAnchor.tsx
│   │   │   ├── EvidenceChip.tsx
│   │   │   └── ConfirmDialog.tsx
│   │   └── investigation/
│   │       ├── DocumentUploadDropzone.tsx
│   │       ├── UploadQueueTable.tsx
│   │       ├── ProcessingStepper.tsx
│   │       ├── ExtractedFieldsTable.tsx
│   │       ├── DocumentPreview.tsx
│   │       ├── EvidenceRegionOverlay.tsx
│   │       ├── FindingCard.tsx
│   │       ├── ComparisonMatrix.tsx
│   │       ├── RelationshipGraph.tsx
│   │       ├── InvestigationTimeline.tsx
│   │       ├── ChainOfCustodyTimeline.tsx
│   │       ├── DossierSectionList.tsx
│   │       └── AuditEventList.tsx
│   ├── features/
│   │   ├── investigations/
│   │   │   ├── pages/
│   │   │   ├── components/
│   │   │   ├── hooks/
│   │   │   ├── schemas/
│   │   │   └── api/
│   │   ├── evidence/
│   │   ├── findings/
│   │   ├── reports/
│   │   └── governance/
│   ├── data/
│   │   ├── mock/
│   │   │   ├── investigations.ts
│   │   │   ├── documents.ts
│   │   │   ├── extracted-fields.ts
│   │   │   ├── findings.ts
│   │   │   ├── comparison.ts
│   │   │   ├── relationships.ts
│   │   │   ├── timeline.ts
│   │   │   ├── audit-events.ts
│   │   │   └── chain-of-custody.ts
│   │   └── fixtures/
│   ├── lib/
│   │   ├── utils.ts
│   │   ├── formatters.ts
│   │   ├── confidence.ts
│   │   ├── file-validation.ts
│   │   └── constants.ts
│   ├── services/
│   │   ├── api-client.ts
│   │   ├── investigation-service.ts
│   │   ├── document-service.ts
│   │   ├── findings-service.ts
│   │   ├── reports-service.ts
│   │   └── adapters/
│   │       └── mock-adapter.ts
│   ├── store/
│   │   ├── app-store.ts
│   │   └── investigation-store.ts
│   ├── types/
│   │   ├── investigation.ts
│   │   ├── document.ts
│   │   ├── evidence.ts
│   │   ├── finding.ts
│   │   ├── audit.ts
│   │   └── report.ts
│   ├── styles/
│   │   ├── globals.css
│   │   └── design-tokens.css
│   ├── test/
│   │   ├── setup.ts
│   │   └── fixtures/
│   └── main.tsx
├── tests/
│   └── e2e/
├── docs/
│   ├── Frontend_Design_Specification.md
│   └── implementation-notes.md
├── .env.example
├── components.json
├── eslint.config.js
├── index.html
├── package.json
├── playwright.config.ts
├── postcss.config.js
├── tailwind.config.ts
├── tsconfig.json
├── vite.config.ts
└── README.md
```

Adapt this structure if the current repo differs. Do not create empty decorative folders or placeholder files just to match a tree. Every created module should have a clear use.

# 6. Design system — premium “S-rank” visual quality

## Theme
Use the supplied blue-orchid / black / electric-lime visual direction:
- Background: deep ink / near-black navy, not flat pure black everywhere
- Surface: layered navy-blue cards
- Primary accent: vivid electric lime
- Secondary accent: electric/cobalt blue
- Supporting colors: cyan for info, amber for uncertainty/warning, orange-red for potential manipulation, green for verified consistent state
- Fine blue-gray borders, controlled glow only on selected/primary elements
- Strong contrast, crisp typography, restrained visual effects

Suggested tokens (adjust for accessibility and visual consistency):
```css
:root {
  --background: #050A13;
  --surface-1: #091321;
  --surface-2: #0D1B2C;
  --surface-3: #12243A;
  --border: #213B57;
  --border-strong: #2F5B80;
  --text-primary: #F3F7FC;
  --text-secondary: #9FB2C8;
  --text-muted: #71869E;
  --accent-lime: #B7FF00;
  --accent-blue: #3D9BFF;
  --accent-cyan: #4DE2F2;
  --warning: #FFC247;
  --danger: #FF704F;
  --success: #45D483;
}
```

Typography:
- Use Inter (or a close modern sans-serif) for UI; consider a tasteful display face only for the landing hero.
- Consistent type scale, readable data tables, clear hierarchy.
- Avoid excessively tiny labels; critical content must be comfortably readable.

Visual craft:
- Use deliberate spacing, grid alignment, consistent radii, 1px borders, layered surfaces.
- Use subtle gradient background and optional restrained abstract blue-floral/forensic-document illustration in landing hero. Do not let imagery compete with task content.
- Avoid glow overload, excessive gradients, random card grids, generic dashboard boilerplate, stock-photo-heavy layouts, and ornamental charts without purpose.
- Make interactions feel considered: hover/focus/active/disabled/loading, keyboard focus rings, transitions around 150–220ms.
- Design responsive desktop first for investigator workstation, then tablet/mobile. Tables may transform to cards or use deliberate horizontal scroll.
- All UI copy should be product-specific and polished.
- Use Lucide icons with consistent size/stroke.

## Landing page content
The hero must communicate the project—not headphones, retail, or unrelated lifestyle imagery:
**DocForensIQ**
“AI-Powered Document Forensics & Contextual Intelligence”
Supporting copy: “Go beyond OCR. Investigate documents, detect inconsistencies, correlate evidence, and generate explainable, evidence-backed findings.”
CTA: “Start Investigation” and secondary “Explore Workflow”
Show product feature highlights: Intelligent Extraction, Visual Forensics, Cross-Document Analysis, Evidence Traceability, Standards-Aligned Methodology.
Use a relevant document + magnifier/fingerprint/relationship graph visual.

# 7. Critical domain semantics and UI behavior

## Findings labels (mandatory)
Only use:
- **Observed characteristic**
- **Detected inconsistency**
- **Potential manipulation**

Never use:
- “Fraud confirmed”
- “Fraud detected” as a final authoritative judgment
- “Guilty”
- Unsupported certainty or accusatory wording

Every finding should have:
- Finding ID and title
- Classification label
- Severity (informational/low/medium/high; configurable)
- Confidence value with definition and calibration disclaimer
- Summary in plain language
- Detection method
- Supporting evidence and source anchors
- Documents/pages/regions affected
- Alternative plausible explanation where applicable
- Status: Needs review / Verified by investigator / Dismissed / Resolved
- Investigator notes and action history

OCR confidence and analysis confidence are separate fields and separate visual labels. Confidence must never be presented as truth.

## Evidence region source anchor
Every finding and extracted field should link to:
- document ID and name
- page index (display human page number)
- normalized bounding box coordinates
- text/image snippet or source region reference
- source hash (where available)
- extraction method/model version
- provenance record

Evidence Viewer:
- document canvas and zoom/page controls
- visible highlighted region overlay
- side panel containing finding explanation and source references
- actions: Accept/Verify, Dismiss, Needs Verification
- keyboard accessible controls
- clicking a source anchor navigates to page/region
- region overlays must not obscure original source

## Human oversight
Use clear review statuses and record all reviewer decisions in audit trail. Confirm destructive or consequential actions. No automated final verdict.

## Chain of custody / standards
Show metadata/events as backend-provided values. Do not imply that a UI alone creates a legally valid chain of custody. Label standards coverage as “Aligned design consideration”, “Implemented”, “Partially implemented”, “Planned”, or “Not supported” only when status is grounded.

Show the standards in the spec:
- ISO/IEC 27037 — identification, collection, acquisition and preservation
- ISO/IEC 27041 — suitability/adequacy of investigative methods
- ISO/IEC 27042 — analysis and interpretation of digital evidence
- NIST SP 800-86 — forensic techniques integrated with incident response
- OWASP ML Security Top 10 — AI/ML security, wherever applicable
Do not assert certification or formal compliance without evidence.

## Privacy and AI security
Design visibly for:
- authorized documents/datasets only
- no real customer/investor data without appropriate authorization
- sensitive-data masking and retention visibility
- input validation and supported-file checks
- suspicious embedded instruction / prompt injection indicator for AI document processing
- output validation and model/pipeline version display
- role-gated review actions
- secure error handling
- audit logging
Do not invent a “passed” security status; use unknown/not configured/mock when the backend is not connected.

# 8. Mock data and API boundary

Build meaningful mock data that shows a multi-document investigation, for example:
- Identity document
- Salary slip
- Bank statement
- Loan application
Use fictional names and values only. Clearly label mock/demo case data. Never use actual private customer or investor data.

Create TypeScript interfaces for investigation, document, extracted field, source anchor, finding, relationship, timeline event, audit event, chain-of-custody event, dossier section, and system capability.

All pages must read from a service/adapter layer rather than hardcoding values inside JSX. Provide an easy way to switch mock adapter to real API later. Include loading, empty, error, partial-result, and unavailable states.

Do not simulate processing with a misleading timer or claim backend operations succeeded. Mock mode can present a clearly labeled deterministic demo sequence.

# 9. Functionality required (no dead controls)

Implement:
- Sidebar navigation and mobile nav
- Investigation search/filter/sort
- Create investigation and upload queue
- Supported format/size validation (configurable)
- Remove queued files, duplicate-hash warning (mockable)
- Start investigation demo action
- Processing stage details and retry UI (mock behavior labeled)
- Document selection and extracted-field filtering
- Click field/source → evidence viewer opens at anchor
- Evidence viewer page selection, zoom controls and selected finding
- Finding Verify / Dismiss / Needs Review actions updating local state and audit feed
- Cross-document comparison filtering and opening evidence
- Relationship graph selection and entity details
- Timeline filtering
- Dossier section completion preview and export action
- Audit trail search/filter
- Chain-of-custody details
- Standards coverage filters/status descriptions
- Security/privacy controls display (non-mutating unless implemented)
- Report export options
- Limitations matrix
- Global search where meaningful
- Toasts for real UI actions
- Confirm dialogs for destructive/review actions
- Persist demo state only when reasonable; clearly state local/demo scope

If an action is not functional, make it visibly disabled with an explanation instead of a fake button.

# 10. Accessibility and responsive requirements

Target WCAG 2.2 AA where practical:
- semantic headings and landmarks
- keyboard navigation
- visible focus
- accessible labels for icon-only controls
- color not sole means of communicating status
- text contrast
- reduced-motion support
- screen-reader-friendly tables and dialogs
- meaningful alt text
- proper dialog focus management
- avoid tiny text in data-heavy views

At breakpoints:
- Desktop: persistent/collapsible sidebar, dense investigator workspace
- Tablet: compact navigation, split panels can stack
- Mobile: drawer nav, stacked cards, source viewer controls remain usable
- Preserve task order and contextual details when reflowing

# 11. Quality bar and definition of “S-rank”

Do not stop after creating a landing page. The S-rank bar is:
1. **Visual consistency:** all routes share a cohesive design system and brand.
2. **Complete workflows:** investigator can traverse the entire demo journey without dead ends.
3. **Information hierarchy:** evidence, finding status, source location and confidence are easy to scan.
4. **Domain authenticity:** realistic forensics terminology, provenance and reviewer flow.
5. **Responsive:** excellent desktop, tablet and mobile behavior.
6. **Accessibility:** keyboard/focus/contrast/semantic behavior.
7. **Robustness:** sensible loading, empty, error, partial and invalid states.
8. **Maintainability:** modular components, typed domain data, no giant page component.
9. **Performance:** lazy-load route-heavy modules; avoid unnecessary rerenders and oversized assets.
10. **Trustworthiness:** no fabricated verification, false compliance, fake confidence calibration or fraud verdict.
11. **Polish:** micro-interactions, refined spacing, high quality tables, meaningful charts, intentional visual details.
12. **Testability:** tests for core interaction and routes.

# 12. Build process (follow in order)

1. Inspect repository, package manager, existing files, source spec and image references.
2. Report a concise implementation plan and any major stack conflicts.
3. Set up/confirm Vite, React, TypeScript, Tailwind, shadcn/ui, routing and design tokens.
4. Build AppShell, nav, typography, surfaces, common states and reusable domain components first.
5. Implement domain types + realistic mock fixtures + service adapter.
6. Implement the required routes and workflows.
7. Wire all controls; remove dead/inert buttons.
8. Add responsive behavior and accessibility pass.
9. Add tests for route rendering and critical investigator flow.
10. Run typecheck, lint, tests and production build. Fix errors; do not just report them.
11. Inspect the result in browser if browser tooling is available; adjust layout, overflow and visual hierarchy.
12. Update README with install, dev, test, build, mock mode, env vars and architecture.
13. Summarize completed pages, implemented interactions, tests/build status, known limitations and next backend integration points.

# 13. Required deliverables

- Complete frontend source code
- Shared design system and reusable components
- All route pages above
- Typed mock data and API adapter boundary
- Responsive behavior
- Core automated tests
- README and `.env.example`
- `docs/implementation-notes.md` describing API integration seams and mock behavior
- Honest list of unimplemented or backend-dependent features

# 14. Final instruction

Begin by inspecting the actual repository and the provided Markdown/image references. Then implement. Do not ask the user to restate requirements that are already in the supplied specification. When a detail is unspecified, make a sensible, accessible, consistent product decision and document it briefly. Do not falsely claim a backend, forensic capability, legal compliance, preservation status, or AI-security control exists unless it is implemented and verified.
