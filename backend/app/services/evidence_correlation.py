"""Deterministic cross-method links with source-bound geometry references."""
from __future__ import annotations


def _box(value: object) -> tuple[float, float, float, float] | None:
    if not isinstance(value, dict):
        return None
    try:
        box = tuple(float(value[key]) for key in ("x0", "y0", "x1", "y1"))
    except (KeyError, TypeError, ValueError):
        return None
    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    return box


def _overlap_ratio(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    width = max(0.0, min(left[2], right[2]) - max(left[0], right[0]))
    height = max(0.0, min(left[3], right[3]) - max(left[1], right[1]))
    intersection = width * height
    denominator = min((left[2] - left[0]) * (left[3] - left[1]), (right[2] - right[0]) * (right[3] - right[1]))
    return intersection / denominator if denominator > 0 else 0.0


def correlate_pdf_text_and_visual(pdf_runs: list[dict], visual_runs: list[dict]) -> dict:
    """Link overlapping OCR/native-text discrepancies and visual candidates.

    A link is a navigation aid and remains a candidate. It does not imply that
    either underlying method is correct or that the source is manipulated.
    """
    links = []
    for pdf_run in sorted(pdf_runs, key=lambda run: (run.get("created_at", ""), run.get("id", ""))):
        pdf_result = pdf_run.get("result") if isinstance(pdf_run.get("result"), dict) else {}
        comparison = pdf_result.get("text_comparison") if isinstance(pdf_result.get("text_comparison"), dict) else {}
        document_id = pdf_run.get("document_id")
        for discrepancy in comparison.get("discrepancies", []):
            page_number = discrepancy.get("page_number")
            native_ref = discrepancy.get("native_source_ref", {})
            ocr_ref = discrepancy.get("ocr_source_ref", {})
            left_box = _box(native_ref.get("bbox_pdf_points"))
            for visual_run in visual_runs:
                if visual_run.get("document_id") != document_id:
                    continue
                visual_result = visual_run.get("result") if isinstance(visual_run.get("result"), dict) else {}
                for observation in visual_result.get("observations", []):
                    if observation.get("page_number") != page_number:
                        continue
                    for region in observation.get("regions", []):
                        right_box = _box(region)
                        if not left_box or not right_box:
                            continue
                        overlap = _overlap_ratio(left_box, right_box)
                        if overlap < 0.15:
                            continue
                        links.append({
                            "id": f"pdf-visual:{pdf_run.get('id')}:{visual_run.get('id')}:{page_number}:{len(links) + 1}",
                            "document_id": document_id,
                            "source_sha256": pdf_run.get("source_sha256"),
                            "page_number": page_number,
                            "overlap_ratio": round(overlap, 4),
                            "basis": "same document and page with at least 0.15 intersection over the smaller source region",
                            "methods": ["native_pdf_text_versus_ocr", observation.get("check_type", "visual_observation")],
                            "source_references": [
                                {"method": "native_pdf_text", "run_id": pdf_run.get("id"), **native_ref},
                                {"method": "ocr", "run_id": pdf_run.get("id"), **ocr_ref},
                                {"method": "visual", "run_id": visual_run.get("id"), "evidence_ref": observation.get("evidence_ref"), "region_pdf_points": region},
                            ],
                            "interpretation": "Correlated candidate observations for navigation and review. This link is not a fraud probability or authenticity conclusion.",
                        })
    return {
        "schema_version": "pdf_ocr_visual_correlation_v1",
        "method": "same_document_page_and_pdf_point_bbox_overlap_v1",
        "links": links,
        "limitations": ["Only saved PDF OCR discrepancies and visual observations with PDF-point bounding boxes are considered.", "Correlation shows geometric co-location only; it does not confirm either method's output or infer manipulation."],
    }
