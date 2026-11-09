# DocForensIQ investigator workstation

Responsive React 19 + TypeScript frontend for evidence-led document review, now connected to the companion FastAPI service for authentication, workspace dashboard data, and investigation metadata. The established design system and forensic screens remain in place.

Signed-out visitors see the public landing page at `/`. After session restoration, signed-in users continue to see the authenticated workspace dashboard at the same route.

## Run locally

```sh
npm install
npm run dev
```

## Checks

```sh
npm run typecheck
npm run lint
npm run test
npm run build
```

Configure the API and registration policy in `.env.local` using `.env.example`. The backend/API setup, PostgreSQL Compose workflow, migrations, admin seed, security notes, and test commands are documented in the project-root [`README.md`](../../README.md).

## Demo boundaries

- The Home dashboard, account/session flow, investigation list, and investigation creation use the backend. Investigation workspaces support multi-file image/PDF uploads, OCR status and source-block review, and an API-backed relationship explorer.
- `VITE_USE_MOCK_API=true` explicitly enables the legacy local forensic demo. All seeded records, findings, scores, hashes, and source previews remain synthetic examples.
- With the demo flag disabled (the default), relationships use persisted extraction results and the Cross-document tab compares stored OCR results using exact typed identifiers. Findings, timeline, search, dossier, audit, and OCR evaluation use their corresponding backend APIs.
- Supported source files are uploaded to the backend with a 25 MiB per-file limit and SHA-256 metadata. Uploads run sequentially and report partial failures. Duplicate detection is unavailable.
- Legacy demo review actions update local UI state only. Cross-document candidate decisions are stored on the server with the reviewer, timestamp, reason, and a hash-chained audit event.
- Dossier downloads are local TXT or JSON summaries. No PDF renderer, signature, forensic verification, chain-of-custody service, security assessment, or compliance evidence is connected.
- Relationship model confidence is uncalibrated. OCR confidence and analysis confidence remain separate labels.

## Main routes

`/login`, optional `/register`, `/forgot-password`, `/reset-password`, `/`, `/investigations`, `/investigations/new`, and investigation-specific document, relationship, and cross-document routes. In explicit demo mode, the original case workspaces remain available, including processing, classification, structure/visual forensics, extracted data, evidence, comparison, relationships, findings, timeline, dossier, audit, custody, explainability, standards, security/privacy, lifecycle, limitations, and reports.
