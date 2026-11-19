# Grand Finale: synthetic investigation walkthrough

This walkthrough uses two generated, plainly labeled synthetic PDFs. They contain fictional parties, identifiers, and amounts. The invoice has a second page and a deliberately small type marker so page navigation and the implemented font-size check have an observable, reproducible input.

## Prepare and start

From `backend/`:

```powershell
.\.venv\Scripts\python.exe -m scripts.generate_grand_finale_demo --output demo_fixtures/grand_finale
```

Start the app using the repository's [local startup instructions](../README.md#run-the-application-locally). Log in or register, then create an investigation named `Grand Finale · Synthetic vendor records`.

## Walk the evidence

1. Upload both PDFs from `backend/demo_fixtures/grand_finale/`.
2. Wait for the configured OCR worker to finish. Confirm the OCR blocks and page metadata in **Evidence viewer**. If OCR is not configured or errors, record that state; do not enter OCR text by hand as if it came from the engine.
3. Open **Cross-document intelligence** and run correlation after OCR. Inspect any identifier links and value comparisons. Open each citation to verify it against the source page.
4. Open **Visual forensics**, choose the two-page invoice, and navigate between pages. Run the implemented checks. Inspect the separately named methods and their diagnostics; select any measured region and follow its OCR association when geometry and overlap support one.
5. Inspect the **TruFor** engine status. With no approved, configured, reachable model worker, localization and reliability views must remain unavailable. Never call the deterministic checks TruFor output.
6. Generate a dossier. Confirm the snapshot includes source hashes, visual run status, per-method results, page artifacts/coordinates when present, dispositions, methodology, and limitations. Export HTML or PDF and verify the digest from the report history/API.
7. Open **Audit trail** and verify that uploads, analysis, region review, artifact access, and dossier generation are recorded.

## Reproducible local check output

The generated PDFs were run through the repository's actual `analyze_file` implementation on 17 November 2026. This is a deterministic-check output only; it is not a stored investigation, OCR run, or TruFor inference:

| Source | Status | Pages | Checks | Candidate observations |
|---|---:|---:|---|---|
| `SYNTHETIC_Apex_Invoice.pdf` | completed | 2 | font/baseline executed; copy-move executed; PDF objects executed; compression/resampling unavailable | page 1 font-size outlier: Helvetica 4 pt vs page median 11 pt |
| `SYNTHETIC_Northstar_Ledger.pdf` | completed | 1 | font/baseline executed; copy-move executed; PDF objects executed; compression/resampling unavailable | page 1 font-size outlier: Helvetica 4 pt vs page median 11 pt |

The deliberately small marker causes the measured size observation. It is an authored property of these demo inputs, not evidence about a real document. The source text includes different invoice/ledger amounts for a comparison exercise. The OCR and comparison results must be whatever the running OCR and correlation services actually return.

## Capability boundary

The local API endpoint could not be reached from this constrained session, and no authorized TruFor worker/checkpoint execution was available. The full GUI flow therefore remains a runbook for the operator to execute against a started application. The generated source PDFs and deterministic checks are real; OCR, cross-document outputs, persisted dossier, and audit entries are deliberately not represented here as already run. TruFor's official project describes the whole-image integrity score alongside localization and reliability maps; the score is reported as a model output and not converted into a fraud probability or verdict. See [the official TruFor project](https://grip-unina.github.io/TruFor/).
