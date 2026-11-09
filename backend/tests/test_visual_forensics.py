"""Algorithm integration checks against generated source PDF/image bytes (not UI screenshots)."""
import numpy as np
import pytest
from dataclasses import replace

fitz = pytest.importorskip("fitz")
cv2 = pytest.importorskip("cv2")

from app.services.visual_forensics import analyze_file
from app.api.v1 import auth as auth_routes, investigations as investigations_routes
from app.core.config import settings

PASSWORD = "StrongPassword123"


def _pdf(path, unusual_font: bool):
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    for index in range(8):
        size = 11 if not unusual_font or index != 7 else 4
        page.insert_text((60, 80 + index * 35), f"Repeated invoice field value line {index} with enough text", fontsize=size)
    doc.save(path)
    doc.close()


def test_pdf_font_outlier_is_measured_and_no_match_clean_pdf(tmp_path):
    inconsistent = tmp_path / "stored-source-inconsistent.pdf"
    clean = tmp_path / "stored-source-clean.pdf"
    _pdf(inconsistent, True)
    _pdf(clean, False)

    flagged = analyze_file(inconsistent, "doc-inconsistent", "application/pdf")
    baseline = analyze_file(clean, "doc-clean", "application/pdf")

    matches = [item for item in flagged["observations"] if item["finding_type"] == "visual_font_size_outlier"]
    assert flagged["status"] == "completed"
    assert matches
    assert matches[0]["page_number"] == 1
    assert matches[0]["metrics"]["font_size_pt"] == 4
    assert baseline["status"] == "completed"
    assert not [item for item in baseline["observations"] if item["finding_type"] == "visual_font_size_outlier"]
    assert flagged["checks"][0]["metrics"]["text_spans_inspected"] >= 8
    assert flagged["checks"][1]["metrics"]["keypoints_detected"] > 0
    compression_check = next(c for c in flagged["checks"] if c["check_type"] == "compression_resampling")
    assert compression_check["status"] == "unavailable"
    assert compression_check["skipped"]
    assert flagged["checks"][0]["pages_analyzed"] == [1]


def test_image_analysis_handles_stored_png_and_reports_executed_checks(tmp_path):
    image = np.full((640, 480), 255, dtype=np.uint8)
    cv2.putText(image, "SOURCE DOCUMENT", (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 1, 0, 2)
    for y in range(140, 540, 45):
        cv2.line(image, (20, y), (450, y), 110, 2)
    path = tmp_path / "stored-image.png"
    assert cv2.imwrite(str(path), image)
    result = analyze_file(path, "doc-image", "image/png")
    assert result["status"] == "completed"
    assert result["page_count"] == 1
    assert {item["check_type"] for item in result["checks"]} == {"font_baseline", "copy_move", "compression_resampling", "pdf_objects"}
    assert next(c for c in result["checks"] if c["check_type"] == "font_baseline")["status"] == "unavailable"
    assert all("metrics" in item and "limitations" in item for item in result["observations"])


def test_multipage_tiff_analyzes_every_page(tmp_path):
    path = tmp_path / "two-page-source.tiff"
    pages = [np.full((240, 240), 255, dtype=np.uint8), np.full((240, 240), 235, dtype=np.uint8)]
    cv2.putText(pages[0], "PAGE ONE", (10, 80), cv2.FONT_HERSHEY_SIMPLEX, 1, 0, 2)
    cv2.putText(pages[1], "PAGE TWO", (10, 80), cv2.FONT_HERSHEY_SIMPLEX, 1, 0, 2)
    assert cv2.imwritemulti(str(path), pages)
    result = analyze_file(path, "doc-tiff", "image/tiff")
    copy_check = next(check for check in result["checks"] if check["check_type"] == "copy_move")
    assert result["page_count"] == 2
    assert copy_check["pages_analyzed"] == [1, 2]
    assert copy_check["metrics"]["pages_rendered"] == 2


def test_copy_move_check_finds_transformed_duplicate_regions():
    rng = np.random.default_rng(42)
    image = rng.integers(0, 65, size=(900, 900), dtype=np.uint8)
    patch = np.full((260, 260), 220, dtype=np.uint8)
    for index in range(14):
        x, y = int(rng.integers(12, 235)), int(rng.integers(12, 235))
        cv2.circle(patch, (x, y), int(rng.integers(4, 20)), int(rng.integers(0, 180)), 2)
    cv2.putText(patch, "ORIGINAL EVIDENCE", (10, 140), cv2.FONT_HERSHEY_COMPLEX, 0.8, 15, 2)
    image[70:330, 70:330] = patch
    matrix = cv2.getRotationMatrix2D((130, 130), 2.0, 0.98)
    duplicate = cv2.warpAffine(patch, matrix, (260, 260), borderValue=220)
    image[520:780, 520:780] = duplicate
    from app.services.visual_forensics import _image_regions
    observations = _image_regions(image, 1, "source-copy-move")
    assert observations
    assert observations[0]["metrics"]["ransac_inliers"] >= 16
    assert len(observations[0]["regions"]) == 2


def test_localized_high_frequency_region_is_reported_with_measured_region(tmp_path):
    rng = np.random.default_rng(15)
    image = np.full((640, 640), 128, dtype=np.uint8)
    # Comparable textured peer tiles with one strong local texture outlier.
    for y in range(0, 640, 128):
        for x in range(0, 640, 128):
            image[y:y + 128, x:x + 128] = np.clip(128 + rng.normal(0, 8, (128, 128)), 0, 255).astype(np.uint8)
    image[256:384, 256:384] = rng.integers(0, 256, (128, 128), dtype=np.uint8)
    path = tmp_path / "localized-texture-source.png"
    assert cv2.imwrite(str(path), image)
    found = analyze_file(path, "doc-local", "image/png")
    candidates = [item for item in found["observations"] if item["check_type"] == "compression_resampling"]
    assert candidates
    assert candidates[0]["metrics"]["tile_laplacian_variance"] > candidates[0]["metrics"]["page_median"]


def test_jpeg_localized_recompression_metric_detects_region_without_clean_false_positive(tmp_path):
    from app.services.visual_forensics import _jpeg_blockiness_candidates
    rng = np.random.default_rng(18)
    source = rng.integers(70, 190, (512, 512), dtype=np.uint8)
    _, clean_bytes = cv2.imencode('.jpg', source, [cv2.IMWRITE_JPEG_QUALITY, 90])
    clean = cv2.imdecode(clean_bytes, cv2.IMREAD_GRAYSCALE)
    assert _jpeg_blockiness_candidates(clean, 1) == []
    patch = source[128:384, 128:384].copy()
    _, compressed_bytes = cv2.imencode('.jpg', patch, [cv2.IMWRITE_JPEG_QUALITY, 15])
    source[128:384, 128:384] = cv2.imdecode(compressed_bytes, cv2.IMREAD_GRAYSCALE)
    _, mixed_bytes = cv2.imencode('.jpg', source, [cv2.IMWRITE_JPEG_QUALITY, 90])
    path = tmp_path / "localized-jpeg-recompression.jpg"
    path.write_bytes(mixed_bytes.tobytes())
    result = analyze_file(path, "doc-jpeg", "image/jpeg")
    candidates = [item for item in result["observations"] if item["finding_type"] == "visual_local_jpeg_blockiness"]
    assert candidates
    assert candidates[0]["metrics"]["block_boundary_excess"] >= 4


def test_clean_pdf_does_not_emit_visual_observations(tmp_path):
    clean = tmp_path / "clean-layout.pdf"
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((50, 60), "MONTHLY VENDOR LEDGER", fontsize=16)
    for row in range(8):
        page.draw_line((50, 100 + row * 30), (550, 100 + row * 30), color=(0.7, 0.7, 0.7))
        page.insert_text((60, 120 + row * 30), f"Vendor item {row + 1:02d} amount 125.00", fontsize=10)
    doc.save(clean)
    doc.close()
    result = analyze_file(clean, "clean", "application/pdf")
    assert result["status"] == "completed"
    assert result["observations"] == []


def test_pdf_object_check_reports_overlapping_distinct_source_images(tmp_path):
    first = np.zeros((120, 160, 3), dtype=np.uint8)
    first[:, :80] = (0, 180, 240)
    second = np.zeros((120, 160, 3), dtype=np.uint8)
    second[:, 80:] = (220, 60, 10)
    first_path, second_path = tmp_path / "first.png", tmp_path / "second.png"
    assert cv2.imwrite(str(first_path), first)
    assert cv2.imwrite(str(second_path), second)
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_image(fitz.Rect(80, 100, 380, 330), filename=str(first_path))
    page.insert_image(fitz.Rect(100, 110, 390, 340), filename=str(second_path))
    pdf_path = tmp_path / "overlapping-objects.pdf"
    doc.save(pdf_path)
    doc.close()
    result = analyze_file(pdf_path, "source-objects", "application/pdf")
    objects = [item for item in result["observations"] if item["finding_type"] == "visual_overlapping_pdf_objects"]
    assert objects
    assert objects[0]["metrics"]["overlap_fraction_of_smaller"] > 0.8
    assert len(objects[0]["regions"]) == 2
    assert next(c for c in result["checks"] if c["check_type"] == "pdf_objects")["metrics"]["image_placements_inspected"] == 2


def test_pdf_transparency_overlay_is_structurally_reported(tmp_path):
    overlay = np.zeros((100, 240, 4), dtype=np.uint8)
    overlay[:, :, 0] = 20
    overlay[:, :, 1] = 220
    overlay[:, :, 2] = 30
    overlay[:, :, 3] = 90
    image_path = tmp_path / "transparent-overlay.png"
    assert cv2.imwrite(str(image_path), overlay)
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((80, 150), "Existing source text behind overlay", fontsize=16)
    page.insert_image(fitz.Rect(70, 100, 350, 180), filename=str(image_path))
    pdf_path = tmp_path / "transparent-overlay.pdf"
    doc.save(pdf_path)
    doc.close()
    result = analyze_file(pdf_path, "source-overlay", "application/pdf")
    findings = [item for item in result["observations"] if item["finding_type"] == "visual_transparent_object_overlay"]
    assert findings
    assert findings[0]["metrics"]["soft_mask_present"] is True
    assert findings[0]["page_number"] == 1


@pytest.mark.asyncio
async def test_visual_api_persists_runs_findings_evidence_and_audit(client, monkeypatch, tmp_path):
    monkeypatch.setattr(auth_routes, "settings", replace(settings, self_registration_enabled=True))
    monkeypatch.setattr(investigations_routes, "settings", replace(settings, document_storage_path=str(tmp_path)))
    auth_routes._attempts.clear()
    assert (await client.post("/api/v1/auth/register", json={"full_name": "Visual Investigator", "email": "visual-api@example.com", "password": PASSWORD})).status_code == 201
    headers = {"X-CSRF-Token": client.cookies.get("dfi_csrf")}
    case = await client.post("/api/v1/investigations", headers=headers, json={"title": "Source visual check"})
    case_id = case.json()["id"]

    def source_pdf(unusual):
        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        for index in range(8):
            page.insert_text((60, 80 + index * 35), f"Stored source field {index} with comparable text", fontsize=4 if unusual and index == 7 else 11)
        data = doc.tobytes()
        doc.close()
        return data

    uploaded = []
    for filename, is_unusual in (("inconsistent.pdf", True), ("clean.pdf", False)):
        response = await client.post(f"/api/v1/investigations/{case_id}/documents", headers={**headers, "X-File-Name": filename, "Content-Type": "application/pdf"}, content=source_pdf(is_unusual))
        assert response.status_code == 201
        uploaded.append(response.json())

    results = []
    for document in uploaded:
        response = await client.post(f"/api/v1/investigations/{case_id}/documents/{document['id']}/visual-analysis-runs", headers=headers)
        assert response.status_code == 201
        results.append(response.json())
    assert results[0]["status"] == "partial"
    assert results[1]["status"] == "partial"
    assert results[0]["result"]["unavailable_checks"] == ["compression_resampling"]
    flagged = [item for item in results[0]["result"]["observations"] if item["finding_type"] == "visual_font_size_outlier"]
    clean = [item for item in results[1]["result"]["observations"] if item["finding_type"] == "visual_font_size_outlier"]
    assert flagged and not clean
    assert flagged[0]["evidence_ref"]["document_id"] == uploaded[0]["id"]
    assert flagged[0]["evidence_ref"]["bounding_box"]
    runs = await client.get(f"/api/v1/investigations/{case_id}/visual-analysis-runs")
    assert {item["id"] for item in runs.json()["items"]} == {item["id"] for item in results}
    findings = await client.get(f"/api/v1/investigations/{case_id}/findings")
    candidate = next(item for item in findings.json()["items"] if item["finding_type"] == "visual_font_size_outlier")
    assert candidate["status"] == "candidate"
    assert candidate["evidence_refs"][0]["document_id"] == uploaded[0]["id"]
    retry = await client.post(f"/api/v1/investigations/{case_id}/documents/{uploaded[0]['id']}/visual-analysis-runs", headers=headers)
    assert retry.status_code == 201
    assert retry.json()["result"]["run_kind"] == "retry"
    assert retry.json()["status"] == "partial"
    audit = await client.get(f"/api/v1/investigations/{case_id}/audit")
    assert audit.json()["verification"]["valid"]
    assert any(item["action"] == "visual_analysis.completed" for item in audit.json()["items"])
