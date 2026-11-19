"""Engine-backed visual analysis pipeline: render, infer, persist, map coordinates.

Honesty rules:
- When the engine is disabled or unreachable, the pipeline records an explicit
  unavailable check and persists nothing that could be mistaken for model output.
- Page-level failures (render limits, engine errors, malformed responses) become
  explicit failed page rows; they never downgrade silently into success.
"""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.entities import Document, EvidencePage, EvidenceSpan, ExtractionRun, VisualAnalysisArtifact, VisualAnalysisPage, VisualCandidateRegion
from app.services.visual_coordinates import RenderGeometry, geometry_from_page
from app.services.visual_forensics_engine import configured_engine, engine_health_payload

TRUFOR_CHECK = {
    "check_type": "trufor_engine",
    "algorithm": "TruFor (Guillaro et al., CVPR 2023) RGB+Noiseprint++ fusion via isolated inference worker",
}


def _bbox_bounds(value):
    """Normalize stored OCR boxes without inventing geometry for unknown formats."""
    if isinstance(value, dict):
        keys = ("x0", "y0", "x1", "y1")
        if all(isinstance(value.get(key), (int, float)) for key in keys):
            return tuple(float(value[key]) for key in keys)
        return None
    if not isinstance(value, (list, tuple)):
        return None
    if len(value) == 4 and all(isinstance(item, (int, float)) for item in value):
        x0, y0, x1, y1 = map(float, value)
        return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)
    if value and all(isinstance(item, (list, tuple)) and len(item) >= 2 for item in value):
        points = [(float(item[0]), float(item[1])) for item in value]
        return min(x for x, _ in points), min(y for _, y in points), max(x for x, _ in points), max(y for _, y in points)
    return None


def _overlap_ratio(first, second) -> float:
    x0, y0, x1, y1 = first
    a0, b0, a1, b1 = second
    intersection = max(0.0, min(x1, a1) - max(x0, a0)) * max(0.0, min(y1, b1) - max(y0, b0))
    area = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    return intersection / area if area else 0.0


def _artifact_root() -> Path:
    root = Path(settings.visual_forensics_artifact_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _store_artifact(root: Path, *, investigation_id, document_id, run_id, page_id, artifact_type: str, payload: bytes, width_px: int | None, height_px: int | None, transform: dict, derived_from=None) -> VisualAnalysisArtifact:
    import uuid as uuid_module

    digest = hashlib.sha256(payload).hexdigest()
    storage_key = uuid_module.uuid4().hex
    (root / f"{storage_key}.png").write_bytes(payload)
    return VisualAnalysisArtifact(
        investigation_id=investigation_id,
        document_id=document_id,
        run_id=run_id,
        page_id=page_id,
        artifact_type=artifact_type,
        storage_key=storage_key,
        mime_type="image/png",
        width_px=width_px,
        height_px=height_px,
        sha256=digest,
        derived_from_artifact_id=derived_from,
        transform_json=transform,
    )


async def run_engine_pipeline(db: AsyncSession, run, document: Document) -> dict:
    """Run the engine over the document's pages and persist page/artifact/region rows.

    Returns the engine check section to merge into the deterministic run result.
    """
    health = engine_health_payload()
    engine = configured_engine()
    if engine is None or not health.get("available"):
        reason = health.get("reason") or "Engine unavailable."
        return {
            **TRUFOR_CHECK,
            "status": "unavailable",
            "pages_analyzed": [],
            "metrics": {},
            "engine_health": health,
            "skipped": [{"reason": reason}],
            "explanation": "No TruFor model output exists for this document.",
        }

    if document.content_type != "application/pdf":
        reason = "The engine pipeline currently supports PDF pages only; raster-only sources are not wired."
        return {**TRUFOR_CHECK, "status": "unavailable", "pages_analyzed": [], "metrics": {}, "engine_health": health, "skipped": [{"reason": reason}]}

    import fitz

    root = _artifact_root()
    path = Path(_stored_source_path(document))
    doc = fitz.open(path)
    try:
        pages_analyzed: list[int] = []
        statuses: list[str] = []
        for pindex, page in enumerate(doc):
            page_no = pindex + 1
            page_row = VisualAnalysisPage(
                run_id=run.id,
                page_number=page_no,
                status="running",
                engine="trufor",
                render_dpi=settings.trufor_render_dpi,
                transform_json={},
                diagnostics_json={},
            )
            db.add(page_row)
            await db.flush()
            try:
                render_scale = settings.trufor_render_dpi / 72
                pix = page.get_pixmap(matrix=fitz.Matrix(render_scale, render_scale), alpha=False)
                if pix.width * pix.height > settings.trufor_max_image_pixels:
                    page_row.status = "failed"
                    page_row.diagnostics_json = {"error_code": "exceeds_max_image_pixels", "error_detail": f"Rendered page exceeds TRUFOR_MAX_IMAGE_PIXELS ({settings.trufor_max_image_pixels})."}
                    statuses.append("failed")
                    continue
                geometry = geometry_from_page(page, pix.width, pix.height)
                ocr_page = await db.scalar(
                    select(EvidencePage)
                    .join(ExtractionRun, ExtractionRun.id == EvidencePage.extraction_run_id)
                    .where(EvidencePage.document_id == document.id, EvidencePage.page_number == page_no, ExtractionRun.status == "completed")
                    .order_by(ExtractionRun.run_number.desc())
                    .limit(1)
                )
                ocr_spans = (await db.scalars(select(EvidenceSpan).where(EvidenceSpan.page_id == ocr_page.id))) if ocr_page else []
                ocr_spans = list(ocr_spans)
                render_payload = pix.tobytes("png")
                source_artifact = _store_artifact(
                    root,
                    investigation_id=run.investigation_id,
                    document_id=document.id,
                    run_id=run.id,
                    page_id=page_row.id,
                    artifact_type="source_render",
                    payload=render_payload,
                    width_px=pix.width,
                    height_px=pix.height,
                    transform={"render_dpi": settings.trufor_render_dpi, "mapping": geometry.to_metadata(), "source_sha256": document.sha256},
                )
                db.add(source_artifact)
                await db.flush()
                rendered_path = root / f"{source_artifact.storage_key}.png"
                engine_result = await asyncio.to_thread(engine.analyze_page, page_no, rendered_path)
                page_row.status = engine_result.status if engine_result.status in {"completed", "partial", "failed", "unavailable"} else "failed"
                page_row.engine_version = health.get("model_version")
                page_row.checkpoint_sha256 = health.get("checkpoint_sha256")
                page_row.device = health.get("device")
                page_row.source_page_width_points = geometry.page_width_points
                page_row.source_page_height_points = geometry.page_height_points
                page_row.render_width_px = pix.width
                page_row.render_height_px = pix.height
                page_row.page_rotation = geometry.page_rotation
                page_row.transform_json = {"render_dpi": settings.trufor_render_dpi, "mapping": geometry.to_metadata()}
                page_row.integrity_score = engine_result.integrity_score
                page_row.diagnostics_json = dict(engine_result.diagnostics)
                if engine_result.error_code:
                    page_row.diagnostics_json = {**page_row.diagnostics_json, "error_code": engine_result.error_code, "error_detail": engine_result.error_detail}
                for artifact_type, map_path, target in (
                    ("localization_map", engine_result.localization_map_path, "localization_artifact_id"),
                    ("reliability_map", engine_result.reliability_map_path, "reliability_artifact_id"),
                ):
                    if map_path is None:
                        continue
                    map_payload = map_path.read_bytes()
                    artifact = _store_artifact(
                        root,
                        investigation_id=run.investigation_id,
                        document_id=document.id,
                        run_id=run.id,
                        page_id=page_row.id,
                        artifact_type=artifact_type,
                        payload=map_payload,
                        width_px=engine_result.map_width_px,
                        height_px=engine_result.map_height_px,
                        transform={"render_dpi": settings.trufor_render_dpi, "mapping": geometry.to_metadata(), "source_sha256": document.sha256},
                        derived_from=source_artifact.id,
                    )
                    db.add(artifact)
                    await db.flush()
                    setattr(page_row, target, artifact.id)
                    map_path.unlink(missing_ok=True)
                rendered_path.unlink(missing_ok=True)
                for index, region in enumerate(engine_result.regions):
                    bbox_pdf = geometry.box_pixel_to_pdf(region["bbox_pixel"])
                    region_box = region["bbox_pixel"]
                    linked_blocks = []
                    if ocr_page and ocr_page.width and ocr_page.height:
                        for span in ocr_spans:
                            bounds = _bbox_bounds(span.bounding_box)
                            if bounds is None:
                                continue
                            ocr_box_render = (
                                bounds[0] * pix.width / ocr_page.width,
                                bounds[1] * pix.height / ocr_page.height,
                                bounds[2] * pix.width / ocr_page.width,
                                bounds[3] * pix.height / ocr_page.height,
                            )
                            if _overlap_ratio((region_box["x0"], region_box["y0"], region_box["x1"], region_box["y1"]), ocr_box_render) >= 0.15:
                                linked_blocks.append(span.block_id)
                    db.add(VisualCandidateRegion(
                        page_id=page_row.id,
                        region_index=index,
                        bbox_pixel=region["bbox_pixel"],
                        bbox_pdf_points=bbox_pdf,
                        area_px=int((region["bbox_pixel"]["x1"] - region["bbox_pixel"]["x0"]) * (region["bbox_pixel"]["y1"] - region["bbox_pixel"]["y0"])),
                        mean_localization_score=region.get("mean_localization_score"),
                        max_localization_score=region.get("max_localization_score"),
                        mean_reliability_value=region.get("mean_reliability_value"),
                        threshold_config={},
                        postprocess_version=region.get("postprocess_version", "worker_unspecified"),
                        linked_ocr_block_ids=linked_blocks,
                    ))
                pages_analyzed.append(page_no)
                statuses.append(page_row.status)
            except Exception as exc:
                page_row.status = "failed"
                page_row.diagnostics_json = {**page_row.diagnostics_json, "error_code": "pipeline_exception", "error_detail": str(exc)[:2000]}
                statuses.append("failed")
        completed = sum(1 for status in statuses if status == "completed")
        overall = "unavailable" if not statuses else ("completed" if all(s == "completed" for s in statuses) else ("failed" if all(s in {"failed", "unavailable"} for s in statuses) else "partial"))
        return {
            **TRUFOR_CHECK,
            "status": overall,
            "pages_analyzed": pages_analyzed,
            "metrics": {"pages_completed": completed, "pages_failed": statuses.count("failed"), "pages_unavailable": statuses.count("unavailable")},
            "engine_health": health,
            "skipped": [],
        }
    finally:
        doc.close()


def _stored_source_path(document: Document) -> str:
    """Resolve the stored source file path for the document (owned by the API layer)."""
    from app.api.v1.investigations import stored_document_path

    return str(stored_document_path(document))
