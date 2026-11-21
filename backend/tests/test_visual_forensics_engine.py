"""TruFor engine adapter, coordinate mapping and honest unavailable-state checks."""
import pytest
from dataclasses import replace

from app.core.config import settings
from app.services import visual_forensics_engine as engine_module
from app.services import visual_forensics_pipeline as pipeline_module
from app.services.visual_coordinates import RenderGeometry
from app.services.visual_forensics_engine import TruForEngine, engine_health_payload


def test_engine_health_reports_unavailable_when_worker_url_missing():
    engine = TruForEngine(worker_url="", timeout_seconds=5)
    health = engine.health()
    assert health.available is False
    assert "TRUFOR_WORKER_URL" in health.reason
    payload = health.to_dict()
    assert payload["engine"] == "trufor" and payload["available"] is False


def test_engine_analyze_page_without_configuration_produces_no_model_output():
    from pathlib import Path

    engine = TruForEngine(worker_url="", timeout_seconds=5)
    result = engine.analyze_page(1, Path("unused.png"))
    assert result.status == "unavailable"
    assert result.error_code == "engine_not_configured"
    assert result.integrity_score is None
    assert result.localization_map_path is None
    assert result.reliability_map_path is None
    assert result.regions == []


def test_engine_health_payload_is_honest_when_feature_disabled():
    payload = engine_health_payload()
    assert payload["feature_enabled"] is False
    assert payload["available"] is False
    assert "VISUAL_FORENSICS_ENABLED" in payload["reason"]
    assert payload["model_version"] is None


def test_engine_license_gate_blocks_configured_worker_by_default(monkeypatch):
    monkeypatch.setattr(engine_module, "settings", replace(
        settings,
        visual_forensics_enabled=True,
        trufor_license_cleared=False,
        trufor_worker_url="http://worker.example",
    ))
    payload = engine_health_payload()
    assert payload["feature_enabled"] is False
    assert payload["available"] is False
    assert "TRUFOR_LICENSE_CLEARED=false" in payload["reason"]
    assert engine_module.configured_engine() is None


def test_engine_region_sanitization_keeps_only_well_formed_worker_regions():
    from app.services.visual_forensics_engine import _safe_regions

    malformed = [
        {"bbox_pixel": {"x0": 0, "y0": 0, "x1": -5, "y1": 10}},
        {"bbox_pixel": {"x0": 0, "y0": 0}},
        {"bbox_pixel": {"x0": 0, "y0": 0, "x1": 40, "y1": 40}, "mean_localization_score": 0.7, "postprocess_version": "cc_v1"},
        "not-a-dict",
    ]
    regions = _safe_regions(malformed)
    assert len(regions) == 1
    assert regions[0]["bbox_pixel"]["x1"] == 40
    assert regions[0]["mean_localization_score"] == 0.7
    assert regions[0]["postprocess_version"] == "cc_v1"


def test_render_geometry_maps_pixels_to_pdf_points_and_back():
    geometry = RenderGeometry(page_width_points=612.0, page_height_points=792.0, render_width_px=1700, render_height_px=2200)
    px, py = geometry.pixel_to_pdf_point(850, 1100)
    assert round(px, 1) == 306.0 and round(py, 1) == 396.0
    rx, ry = geometry.pdf_point_to_pixel(px, py)
    assert abs(rx - 850) < 1e-6 and abs(ry - 1100) < 1e-6
    box = geometry.box_pixel_to_pdf({"x0": 100, "y0": 200, "x1": 500, "y1": 600})
    assert box == {"x0": 36.0, "y0": 72.0, "x1": 180.0, "y1": 216.0}


def test_render_geometry_applies_crop_box_origin_offset():
    geometry = RenderGeometry(page_width_points=400.0, page_height_points=300.0, render_width_px=800, render_height_px=600, origin_x_points=20.0, origin_y_points=35.0)
    px, py = geometry.pixel_to_pdf_point(400, 300)
    assert px == 220.0 and py == 185.0
    back = geometry.pdf_point_to_pixel(px, py)
    assert back == (400.0, 300.0)


def test_render_geometry_rejects_invalid_dimensions_and_rotation():
    with pytest.raises(ValueError):
        RenderGeometry(page_width_points=612.0, page_height_points=792.0, render_width_px=0, render_height_px=100)
    with pytest.raises(ValueError):
        RenderGeometry(page_width_points=612.0, page_height_points=792.0, render_width_px=100, render_height_px=100, page_rotation=45)


def test_render_geometry_metadata_is_versioned_and_reversible():
    geometry = RenderGeometry(page_width_points=595.0, page_height_points=842.0, render_width_px=1653, render_height_px=2339, page_rotation=90)
    metadata = geometry.to_metadata()
    assert metadata["mapping_version"] == "pixel_to_pdf_v1"
    assert metadata["page_rotation"] == 90
    assert metadata["render_width_px"] == 1653


def test_ocr_candidate_association_requires_measured_box_overlap():
    assert pipeline_module._bbox_bounds([[10, 20], [30, 20], [30, 40], [10, 40]]) == (10.0, 20.0, 30.0, 40.0)
    assert pipeline_module._bbox_bounds(None) is None
    assert pipeline_module._overlap_ratio((10, 20, 50, 60), (15, 25, 45, 55)) == pytest.approx(0.5625)
    assert pipeline_module._overlap_ratio((10, 20, 50, 60), (100, 100, 120, 120)) == 0


@pytest.mark.asyncio
async def test_pipeline_reports_unavailable_engine_without_persisting_model_output(client, monkeypatch, tmp_path):
    from app.services import visual_forensics_pipeline

    enabled = replace(settings, visual_forensics_enabled=True, trufor_license_cleared=True, trufor_worker_url="", visual_forensics_artifact_dir=str(tmp_path / "artifacts"))
    monkeypatch.setattr(engine_module, "settings", enabled)
    monkeypatch.setattr(pipeline_module, "settings", enabled)
    from app.api.v1 import auth as auth_routes
    monkeypatch.setattr(auth_routes, "settings", replace(settings, self_registration_enabled=True))
    auth_routes._attempts.clear()
    assert (await client.post("/api/v1/auth/register", json={"full_name": "Engine Investigator", "email": "engine-status@example.com", "password": "StrongPassword123"})).status_code == 201
    headers = {"X-CSRF-Token": client.cookies.get("dfi_csrf")}
    case = await client.post("/api/v1/investigations", headers=headers, json={"title": "Engine status check"})
    case_id = case.json()["id"]

    import fitz

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((60, 100), "Engine status source document line", fontsize=11)
    payload = doc.tobytes()
    doc.close()
    uploaded = await client.post(f"/api/v1/investigations/{case_id}/documents", headers={**headers, "X-File-Name": "engine-status.pdf", "Content-Type": "application/pdf"}, content=payload)
    assert uploaded.status_code == 201
    document_id = uploaded.json()["id"]

    run = await client.post(f"/api/v1/investigations/{case_id}/documents/{document_id}/visual-analysis-runs", headers=headers)
    assert run.status_code == 201
    body = run.json()
    trufor_check = next(check for check in body["result"]["checks"] if check["check_type"] == "trufor_engine")
    assert trufor_check["status"] == "unavailable"
    assert "TRUFOR_WORKER_URL" in trufor_check["skipped"][0]["reason"]
    assert trufor_check["pages_analyzed"] == []
    assert body["result"]["engine"]["available"] is False

    pages = await client.get(f"/api/v1/investigations/{case_id}/visual-analysis-runs/{body['id']}/pages")
    assert pages.status_code == 200
    assert pages.json()["items"] == []

    health = await client.get(f"/api/v1/investigations/{case_id}/visual-forensics-engine")
    assert health.status_code == 200
    assert health.json()["feature_enabled"] is True
    assert health.json()["available"] is False
    assert "not configured" in health.json()["reason"]
