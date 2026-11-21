"""Generate and benchmark a small, labeled, synthetic PDF forensic dataset.

Run: python scripts/evaluate_synthetic_pdf_forensics.py [output-dir]
The fixtures are synthetic and contain no real documents or model outputs.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import fitz
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.sign import signers
from pyhanko.sign.signers.pdf_signer import PdfSignatureMetadata

from app.services.pdf_forensics import analyze_pdf


def _write_base(path: Path) -> None:
    document = fitz.open()
    page = document.new_page(width=612, height=792)
    page.insert_text((72, 72), "SYNTHETIC INVOICE TOTAL 100.00", fontsize=12)
    document.set_metadata({"title": "DocForensIQ synthetic fixture", "creator": "synthetic-evaluation-v1"})
    document.save(path, deflate=False)
    document.close()


def _signed_fixture(unsigned: bytes) -> bytes:
    key_material = int.from_bytes(hashlib.sha256(b"DocForensIQ synthetic test signing key v1").digest(), "big")
    key = ec.derive_private_key(key_material, ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Synthetic Evaluation Fixture")])
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(2026112001).not_valid_before(start).not_valid_after(datetime(2027, 1, 1, tzinfo=timezone.utc))
        .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=True, key_encipherment=False, data_encipherment=False, key_agreement=False, key_cert_sign=False, crl_sign=False, encipher_only=None, decipher_only=None), critical=True)
        .sign(key, hashes.SHA256()))
    temp = Path(__file__).resolve().parents[1] / "demo_fixtures" / "synthetic_pdf_v1"
    temp.mkdir(parents=True, exist_ok=True)
    key_path, cert_path = temp / ".fixture-key.pem", temp / ".fixture-cert.pem"
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    try:
        signer = signers.SimpleSigner.load(str(key_path), str(cert_path))
        writer = IncrementalPdfFileWriter(__import__("io").BytesIO(unsigned))
        output = __import__("io").BytesIO()
        signers.PdfSigner(PdfSignatureMetadata(field_name="SyntheticSignature"), signer).sign_pdf(writer, output=output)
        return output.getvalue()
    finally:
        key_path.unlink(missing_ok=True)
        cert_path.unlink(missing_ok=True)


def _metrics(rows: list[dict], prediction_key: str, truth_key: str = "manipulated") -> dict:
    tp = sum(row[prediction_key] and row[truth_key] for row in rows)
    fp = sum(row[prediction_key] and not row[truth_key] for row in rows)
    fn = sum(not row[prediction_key] and row[truth_key] for row in rows)
    tn = sum(not row[prediction_key] and not row[truth_key] for row in rows)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    fpr = fp / (fp + tn) if fp + tn else None
    return {"n": len(rows), "tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": precision, "recall": recall, "false_positive_rate": fpr}


def generate(output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    base = output_dir / "clean.pdf"
    _write_base(base)
    cases = [{"id": "clean", "path": base, "manipulated": False, "expected": {"incremental_update": False, "signature_validity": "not_applicable"}}]

    benign = output_dir / "benign_incremental_metadata.pdf"
    benign.write_bytes(base.read_bytes())
    document = fitz.open(benign)
    document.set_metadata({"title": "Benign incremental metadata update"})
    document.save(benign, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
    document.close()
    cases.append({"id": "benign_incremental_metadata", "path": benign, "manipulated": False, "expected": {"incremental_update": True, "signature_validity": "not_applicable"}})

    altered = output_dir / "known_text_replacement.pdf"
    altered.write_bytes(base.read_bytes())
    document = fitz.open(altered)
    page = document[0]
    page.add_redact_annot(fitz.Rect(68, 54, 274, 81), fill=(1, 1, 1))
    page.apply_redactions()
    page.insert_text((72, 72), "SYNTHETIC INVOICE TOTAL 900.00", fontsize=12)
    document.save(altered, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
    document.close()
    cases.append({"id": "known_text_replacement", "path": altered, "manipulated": True, "expected": {"incremental_update": True, "signature_validity": "not_applicable", "known_manipulated_region_pdf_points": {"x0": 68, "y0": 54, "x1": 274, "y1": 81}}})

    unsigned = output_dir / "signed_original.pdf"
    unsigned.write_bytes(_signed_fixture(base.read_bytes()))
    cases.append({"id": "signed_original", "path": unsigned, "manipulated": False, "expected": {"incremental_update": True, "signature_validity": "valid"}})
    tampered = output_dir / "signed_byte_range_mutation.pdf"
    signed_bytes = unsigned.read_bytes()
    if b"synthetic fixture" not in signed_bytes:
        raise RuntimeError("Synthetic signed fixture metadata could not be located for controlled byte-range mutation")
    tampered.write_bytes(signed_bytes.replace(b"synthetic fixture", b"tampered! fixture", 1))
    cases.append({"id": "signed_byte_range_mutation", "path": tampered, "manipulated": True, "expected": {"incremental_update": True, "signature_validity": "invalid"}})

    outcomes = []
    for case in cases:
        path = case["path"]
        report = analyze_pdf(path, hashlib.sha256(path.read_bytes()).hexdigest())
        revisions = report["revision_inventory"].get("revisions", [])
        incremental_change = any(any(revision.get("changes", {}).get(key) for key in ("added", "updated", "freed")) for revision in revisions[1:])
        signature_values = [signature.get("cryptographic_integrity") for signature in report.get("signature_verification", {}).get("signatures", [])]
        outcomes.append({
            "id": case["id"], "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "manipulated": case["manipulated"], "expected": case["expected"],
            "observed": {"revision_inventory_status": report["revision_inventory"]["status"], "revision_count": len(revisions), "incremental_change": incremental_change, "signature_cryptographic_integrity": signature_values},
            "revision_change_heuristic_prediction": bool(incremental_change),
            "signature_validity_prediction": "valid" if "valid" in signature_values else "invalid" if "invalid" in signature_values else "not_applicable",
        })
    revision_rows = [{"manipulated": row["manipulated"], "prediction": row["revision_change_heuristic_prediction"]} for row in outcomes]
    signed_rows = [{"manipulated": row["expected"].get("signature_validity") == "invalid", "prediction": row["signature_validity_prediction"] == "invalid"} for row in outcomes if row["expected"].get("signature_validity") in {"valid", "invalid"}]
    result = {
        "dataset_version": "synthetic_pdf_forensics_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "fixture_count": len(outcomes),
        "fixtures": outcomes,
        "metrics": {
            "any_incremental_object_change_as_manipulation": _metrics(revision_rows, "prediction"),
            "pyhanko_signature_integrity_invalidity_on_signed_subset": _metrics(signed_rows, "prediction"),
        },
        "limitations": [
            "Synthetic controlled fixtures only; sample size is too small for general accuracy claims.",
            "Revision changes are evaluated as a deliberately naive manipulation heuristic to measure its false positives; they are not classified as fraud by the application.",
            "Signature test uses a self-signed synthetic certificate and measures cryptographic integrity only, not real-world trust or identity.",
            "OCR and visual inference performance are not benchmarked because this script does not run an OCR engine or a cleared TruFor model.",
        ],
    }
    (output_dir / "evaluation_report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {"dataset_version": result["dataset_version"], "fixtures": [{"id": row["id"], "sha256": row["sha256"], "manipulated": row["manipulated"], "expected": row["expected"]} for row in outcomes]}
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "demo_fixtures" / "synthetic_pdf_v1"
    print(json.dumps(generate(target), ensure_ascii=False, indent=2))
