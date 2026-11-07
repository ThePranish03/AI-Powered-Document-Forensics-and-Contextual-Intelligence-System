# DocForensIQ

The project has a standalone FastAPI backend in `backend/` and an authenticated React workstation in `frontend/DocForensIQ-frontend/`. The backend owns accounts, sessions, investigation metadata, document upload/storage, queued PaddleOCR processing, optional Grok relationship extraction, and deterministic evidence rules for findings, timelines, audit, dossier snapshots, and supplied-text OCR metrics.

The current limitations and supported workflows are summarized below. Automated visual-forensics checks and evidence-grounded language-model search are not implemented.

## Local setup

### One-command local run (Windows)

After installing Node.js, `uv`, and the sibling PaddleOCR environment, run this from the `DataOps` project root:

```powershell
powershell -ExecutionPolicy Bypass -File .\run-local.ps1
```

The launcher applies backend migrations, starts the API, starts the PaddleOCR worker, starts Vite, opens the frontend, and writes service logs under `logs/`. It creates the local backend and frontend environment files from their examples if they are missing. Add a Vercel AI Gateway key (`vck_...`) or a direct xAI key to `backend/.env` and set `RELATIONSHIP_EXTRACTION_ENABLED=true` to enable Grok extraction. Press Ctrl+C in the launcher terminal to stop its services.

The PaddleOCR environment must be installed once in `../extra_things/PaddleOcr/ocr_evaluation`; the launcher checks it before starting so uploads are not left queued without a worker. If setup is needed, follow that project's README and set `PADDLEOCR_PYTHON` in `backend/.env` to its `Scripts\python.exe`.

### Docker Compose (PostgreSQL + API + frontend + OCR worker)

From the project root:

```powershell
Copy-Item .env.example .env
Copy-Item backend/.env.example backend/.env
docker compose up -d --build
```

Open the frontend at `http://localhost:5173` and the development API docs at `http://localhost:8000/docs`. Compose applies Alembic migrations, starts the isolated PaddleOCR worker, and keeps the processes in the background. View service logs with `docker compose logs -f api ocr-worker`. Stop the project with `docker compose down`. If self-registration is enabled, create the first user from the registration screen.

To provision an administrator instead, set your chosen `ADMIN_EMAIL` and `ADMIN_PASSWORD` in the project-root `.env` (the same directory as `docker-compose.yml`), then run:

```powershell
docker compose exec api python -m scripts.seed_admin
```

The example files leave these credentials blank intentionally. Copying an example file does not create an account; run the seed command to create it. In Docker Compose, `backend/.env` is not loaded into the API container, so set the values in the project-root `.env`. When run again for an existing account, the seed command sets the configured password and revokes existing refresh sessions. Use a unique password of at least 12 characters.

### Run the frontend and API separately

1. Copy `frontend/DocForensIQ-frontend/.env.example` to `.env.local` in that frontend directory and configure the API URL, self-registration policy, and optional demo flag.
2. In `backend/`, install uv, sync the locked backend and test dependencies, then configure the environment. The backend `.env.example` uses SQLite for this direct local workflow; edit `DATABASE_URL` if using a local PostgreSQL instance.

```powershell
cd backend
py -m pip install uv
uv sync --python 3.12 --extra test --locked
Copy-Item .env.example .env
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

3. In a separate terminal, install frontend dependencies and run Vite:

```powershell
cd frontend\DocForensIQ-frontend
npm ci
npm run dev
```

The default backend for local non-Compose execution uses SQLite. PostgreSQL is used by Compose and is required in production. Migrations are mandatory; the application does not create schema tables at startup.

### OCR worker and relationship extraction

Document uploads accept JPEG, PNG, TIFF, and PDF files up to 25 MiB. The API validates the declared extension/MIME against file signatures; PDF intake also requires a trailing `%%EOF` marker. This is format screening, not full PDF parsing or malware scanning. The PP-StructureV3 runner accepts PDF input. Create an isolated Python 3.10–3.12 environment in `../../extra_things/PaddleOcr/ocr_evaluation` and install its pinned Paddle extra as described in that project's README. Set `PADDLEOCR_PYTHON` in `backend/.env` if the environment uses a different location. Start the API, then run `uv run python -m scripts.run_ocr_worker --watch` from `backend/`.

To enable relationship extraction in Compose, configure the provider and set `RELATIONSHIP_EXTRACTION_ENABLED=true` in `backend/.env`, then run `docker compose up -d --build api ocr-worker`. Use either a Vercel AI Gateway key (`XAI_BASE_URL=https://ai-gateway.vercel.sh/v1`, `XAI_MODEL=spacexai/grok-4.7`) or a direct xAI key (`XAI_BASE_URL=https://api.x.ai/v1`, `XAI_MODEL=grok-4.7`). Vercel AI Gateway requires a credit card on the Vercel team before inference requests are served. The API key stays server-side. Extraction uses structured output, validates source block references, and only auto-resolves exact normalized names of the same entity type.

## Environment configuration

- Backend: see [`backend/.env.example`](backend/.env.example). `JWT_SECRET` must be a high-entropy secret with at least 32 characters in production. `FRONTEND_ORIGIN` must be the exact allowed HTTPS origin in production. `COOKIE_SECURE` is forced on in production.
- Compose: copy [`.env.example`](.env.example) to `.env`; its PostgreSQL password is development-only.
- Frontend: see [`frontend/DocForensIQ-frontend/.env.example`](frontend/DocForensIQ-frontend/.env.example). Never put secrets in `VITE_*` variables.
- `SELF_REGISTRATION_ENABLED=false` disables `/auth/register`; provision users with the configurable admin seed command. The admin role can list users but investigation access remains creator-owned.

## Development email sink

Password reset requests always return a generic response. In local development, reset emails are appended to the file configured by `EMAIL_SINK_FILE` (Compose uses a private named volume at `/app/mailbox`). The file contains the email delivery payload and reset link; it is not application logging and must be treated as secret-bearing local development data. Production email delivery is not implemented.

## Security and access policy

- Login and refresh tokens use HttpOnly, SameSite=Strict cookies. Access JWTs expire after 15 minutes; refresh tokens are opaque, stored only as SHA-256 hashes, rotated on refresh, and revoked on logout/password reset.
- State-changing authenticated requests use a double-submit CSRF cookie/header check. CORS allows only `FRONTEND_ORIGIN`; production cookies require HTTPS.
- Passwords use Argon2id through `argon2-cffi`. Registration/reset enforce 12+ characters including uppercase, lowercase, and digits.
- Login, registration, and password reset use a per-process, in-memory 10-attempt/minute limiter. It is suitable only for a single API worker; deploy behind a shared Redis-backed limiter before scaling horizontally.
- Investigations are creator-owned. Every list, detail, update, and document-metadata route scopes reads/writes by the authenticated creator. Role permissions are centralized in `app/api/deps.py`.
- Security headers and parameterized SQLAlchemy statements are enabled. User-agent metadata is retained only for refresh sessions. No client IP is persisted.
- Refresh cookies are SameSite=Strict and the CSRF token is double-submit protected; deployments on unrelated registrable domains may require a deliberate SameSite=None/Secure deployment change.
- API access tokens remain valid until their short expiry if copied before logout; logout revokes refresh sessions and clears browser cookies.

## Demo boundary

`VITE_USE_MOCK_API=true` in a Vite development build deliberately exposes the existing legacy synthetic forensic screens. They are still labeled as demo data and are not used by login, Home, investigation listing, or investigation creation. The flag defaults to false and is ignored by production builds. With the flag off, an investigation opens the live workspace: upload, OCR review, relationship graph, rule-based findings, timeline, limited visual signals, evidence search, dossier snapshots, audit, and OCR evaluation from supplied ground truth.

## Tests

```powershell
cd backend
uv sync --python 3.12 --extra test --locked
uv run pytest
uv run alembic upgrade head
```

```powershell
cd frontend\DocForensIQ-frontend
npm run typecheck
npm run lint
npm run test
npm run build
```

The tests cover session cookies, password hashing, refresh rotation, logout, reset-token single use, authorization scoping, dashboard aggregates, route protection, session restoration, API errors, investigation creation, document upload and hashes, PDF signature checks, and OCR block normalization.

## Current limitations

- No email verification or production email provider.
- Rate limiting is process-local; OCR processing uses a database-polled worker rather than a distributed queue.
- No workspace membership model; access is creator-only.
- Upload supports JPEG, PNG, TIFF, and PDF; Office documents are not accepted. The upload PDF checks are not complete document parsing or malware scanning.
- The OCR worker stores provider output and normalized source blocks. Stale jobs are marked failed, with up to three explicit retries per document. Run one worker for SQLite development. The API can score engine confidence and compute CER, WER, exact match, and detection F1 when you supply ground truth. It does not run a second OCR engine, and it does not report latency or GPU numbers for that supplied-text comparison.
- Relationship graph shows Grok extraction when that worker is configured, plus identifier nodes written by the deterministic rules. Shared identifiers are candidate or identifier matches, not confirmed parties.
- Findings, timeline events, review notes, hash-chained audit events, and HTML/JSON dossier snapshots are stored. Visual review is manual; automated visual-forensics checks are unavailable. Duplicate hashes and low OCR confidence are separate candidate signals, not visual analysis. Font, copy-move, compression, and forgery checks are explicitly unavailable. Image preprocessing beyond the PaddleOCR worker is not part of the API process.
- Evidence search lists matching OCR blocks and findings. It does not call a language model, even when an xAI key is configured for relationship extraction.
- MCP exposure is not configured.
- Migration and API are scaffolded for PostgreSQL, but production deployment infrastructure and operational secret management are not included.
