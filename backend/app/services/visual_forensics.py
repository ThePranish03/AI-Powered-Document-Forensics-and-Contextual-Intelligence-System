"""Measured, conservative visual document checks. Results are observations, never authenticity judgments."""
from __future__ import annotations

import os
import statistics
from pathlib import Path

try:
    import cv2
    import fitz
    import numpy as np
except ImportError:  # The API can start before optional vision wheels are installed.
    cv2 = fitz = np = None


CHECKS = {
    "font_baseline": "PDF text font and baseline comparison",
    "copy_move": "Within-page duplicated image-region feature matching",
    "compression_resampling": "Local rendered texture variation (screening signal; not compression attribution)",
    "pdf_objects": "PDF image/text placement and transparency inspection",
}


def _configured_threshold(name: str, default: float, lower: float, upper: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if lower <= value <= upper else default


FONT_SIZE_OUTLIER_RATIO = _configured_threshold("VISUAL_FONT_SIZE_OUTLIER_RATIO", 0.62, 0.2, 0.95)
BASELINE_DEVIATION_PT = _configured_threshold("VISUAL_BASELINE_DEVIATION_PT", 2.5, 0.5, 12.0)
COPY_MOVE_INLIER_RATIO = _configured_threshold("VISUAL_COPY_MOVE_INLIER_RATIO", 0.40, 0.2, 0.95)
JPEG_BLOCKINESS_MIN_EXCESS = _configured_threshold("VISUAL_JPEG_BLOCKINESS_MIN_EXCESS", 4.0, 1.0, 32.0)


def _jpeg_blockiness_candidates(gray: np.ndarray, page_number: int):
    """Screen raster JPEGs for spatially unusual 8x8 block-boundary energy.

    This is deliberately a conservative screening metric: content edges can align to
    JPEG blocks, so it only reports a region when it is a robust peer outlier.
    """
    h, w = gray.shape[:2]
    tile = 128
    measurements = []
    for y in range(0, h - tile + 1, tile):
        for x in range(0, w - tile + 1, tile):
            patch = gray[y:y + tile, x:x + tile].astype(np.float32)
            if float(np.std(patch)) < 5:
                continue
            vertical = np.abs(patch[:, 8:tile:8] - patch[:, 7:tile - 1:8]).mean()
            horizontal = np.abs(patch[8:tile:8, :] - patch[7:tile - 1:8, :]).mean()
            inner_v = np.abs(patch[:, 1:] - patch[:, :-1]).mean()
            inner_h = np.abs(patch[1:, :] - patch[:-1, :]).mean()
            blockiness = float((vertical + horizontal) / 2 - (inner_v + inner_h) / 2)
            measurements.append((x, y, blockiness))
    if len(measurements) < 8:
        return []
    values = np.asarray([entry[2] for entry in measurements])
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    threshold = max(3.0 * mad, JPEG_BLOCKINESS_MIN_EXCESS)
    results = []
    for x, y, score in measurements:
        if score - median >= threshold:
            results.append({"finding_type": "visual_local_jpeg_blockiness", "check_type": "compression_resampling", "algorithm": "8x8 JPEG boundary gradient excess compared with same-image tile median/MAD", "page_number": page_number, "regions": [{"x0": x, "y0": y, "x1": x + tile, "y1": y + tile}], "metrics": {"block_boundary_excess": round(score, 3), "image_tile_median": round(median, 3), "image_tile_mad": round(mad, 3), "tile_size_px": tile}, "explanation": "This raster image region has stronger 8-pixel boundary gradients than peer textured regions.", "limitations": "Content edges, resizing, image compositing, and JPEG encoding can produce this signal. It does not identify which process caused it or establish alteration."})
            if len(results) >= 4:
                break
    return results


def _box(rect):
    return {"x0": round(float(rect[0]), 2), "y0": round(float(rect[1]), 2), "x1": round(float(rect[2]), 2), "y1": round(float(rect[3]), 2)}


def _image_regions(gray: np.ndarray, page_number: int, document_id: str, diagnostics: dict | None = None) -> list[dict]:
    """Conservative ORB+RANSAC copy-move candidates with spatial and match-count gates."""
    h, w = gray.shape[:2]
    if min(h, w) < 180:
        if diagnostics is not None: diagnostics.update({"status": "skipped", "reason": "Rendered image is below minimum 180px dimension", "keypoints_detected": 0, "candidate_matches": 0, "ransac_inliers": 0})
        return []
    orb = cv2.ORB_create(nfeatures=1800, scaleFactor=1.2, nlevels=8, edgeThreshold=19)
    keypoints, descriptors = orb.detectAndCompute(gray, None)
    if descriptors is None or len(keypoints) < 40:
        if diagnostics is not None: diagnostics.update({"status": "executed_no_features", "reason": "Fewer than 40 ORB keypoints", "keypoints_detected": len(keypoints), "candidate_matches": 0, "ransac_inliers": 0})
        return []
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    pairs = matcher.knnMatch(descriptors, descriptors, k=min(12, len(descriptors)))
    points_a, points_b = [], []
    separation = min(h, w) * 0.18
    for pair in pairs:
        if not pair:
            continue
        query_index = pair[0].queryIdx
        a = np.array(keypoints[query_index].pt)
        distant = [match for match in pair if match.queryIdx != match.trainIdx and np.linalg.norm(a - np.array(keypoints[match.trainIdx].pt)) >= separation]
        if len(distant) < 2:
            continue
        first, second = distant[:2]
        if first.distance >= 0.72 * second.distance:
            continue
        points_a.append(a)
        points_b.append(np.array(keypoints[first.trainIdx].pt))
    if len(points_a) < 18:
        if diagnostics is not None: diagnostics.update({"status": "executed_no_match", "keypoints_detected": len(keypoints), "candidate_matches": len(points_a), "ransac_inliers": 0})
        return []
    matrix, inliers = cv2.findHomography(np.float32(points_a), np.float32(points_b), cv2.RANSAC, 4.0)
    inlier_count = int(inliers.sum()) if inliers is not None else 0
    if diagnostics is not None: diagnostics.update({"status": "executed", "keypoints_detected": len(keypoints), "candidate_matches": len(points_a), "ransac_inliers": inlier_count, "inlier_ratio": round(inlier_count / len(points_a), 4)})
    # Repeated line items often pass looser ratios as accidental self-similarity.
    # Controlled copied-region fixtures retain a stronger consensus (>0.40).
    if matrix is None or inlier_count < 24 or inlier_count / len(points_a) < COPY_MOVE_INLIER_RATIO:
        return []
    a = np.float32(points_a)[inliers.ravel() == 1]
    b = np.float32(points_b)[inliers.ravel() == 1]
    def bounds(pts):
        return [max(0, float(pts[:, 0].min())), max(0, float(pts[:, 1].min())), min(w, float(pts[:, 0].max())), min(h, float(pts[:, 1].max()))]
    ba, bb = bounds(a), bounds(b)
    if min(ba[2] - ba[0], ba[3] - ba[1], bb[2] - bb[0], bb[3] - bb[1]) < 24:
        return []
    return [{
        "finding_type": "visual_copy_move_candidate", "check_type": "copy_move", "algorithm": "ORB descriptors, Lowe ratio filtering, and RANSAC homography",
        "page_number": page_number, "regions": [dict(zip(("x0", "y0", "x1", "y1"), [round(x, 2) for x in ba])), dict(zip(("x0", "y0", "x1", "y1"), [round(x, 2) for x in bb]))],
        "metrics": {"keypoint_matches": len(points_a), "ransac_inliers": inlier_count, "inlier_ratio": round(inlier_count / len(points_a), 4)},
        "explanation": "Two spatially separated page regions share local visual features under a fitted geometric transform.",
        "limitations": "Repeated logos, stamps, table cells, and templates can produce similar matches. This candidate requires investigator review and does not establish copying or alteration.",
    }]


def _texture_candidates(gray: np.ndarray, page_number: int):
    h, w = gray.shape[:2]
    tile = max(48, min(128, min(h, w) // 5))
    measurements = []
    for y in range(0, h - tile + 1, tile):
        for x in range(0, w - tile + 1, tile):
            patch = gray[y:y + tile, x:x + tile]
            if float(np.std(patch)) < 5:
                continue
            lap = float(cv2.Laplacian(patch, cv2.CV_64F).var())
            measurements.append((x, y, lap))
    if len(measurements) < 8:
        return []
    vals = np.array([m[2] for m in measurements])
    median = float(np.median(vals))
    mad = float(np.median(np.abs(vals - median)))
    if median <= 0:
        return []
    cut = max(4.5 * mad, median * 1.5)
    result = []
    for x, y, val in measurements:
        ratio = max(val, median) / max(min(val, median), 1e-6)
        if abs(val - median) >= cut and ratio >= 2.8:
            result.append({
                "finding_type": "visual_local_texture_variation", "check_type": "compression_resampling", "algorithm": "OpenCV tiled Laplacian-variance outlier against page median/MAD",
                "page_number": page_number, "regions": [{"x0": x, "y0": y, "x1": x + tile, "y1": y + tile}],
                "metrics": {"tile_laplacian_variance": round(val, 3), "page_median": round(median, 3), "page_mad": round(mad, 3), "ratio_to_median": round(ratio, 3)},
                "explanation": "This tile's high-frequency texture differs materially from other textured tiles on the same page.",
                "limitations": "Text, photographs, scan resolution, illumination, and ordinary page content all affect texture. This weak signal alone does not establish editing or authenticity.",
            })
            if len(result) >= 6:
                break
    return result


def _check_result(check_type: str, status: str, algorithm: str, *, pages: list[int] | None = None, metrics: dict | None = None, skipped: list[dict] | None = None) -> dict:
    return {"check_type": check_type, "status": status, "algorithm": algorithm, "pages_analyzed": pages or [], "metrics": metrics or {}, "skipped": skipped or []}


def analyze_file(path: Path, document_id: str, content_type: str) -> dict:
    if cv2 is None or fitz is None or np is None:
        raise RuntimeError("Visual analysis dependencies are missing; install the backend PyMuPDF and opencv-python-headless dependencies")
    observations: list[dict] = []
    checks = []
    if content_type == "application/pdf" or path.suffix.lower() == ".pdf":
        doc = fitz.open(path)
        try:
            check_pages = {key: [] for key in CHECKS}
            check_metrics = {"font_baseline": {"text_spans_inspected": 0, "font_outliers": 0, "baseline_outliers": 0}, "copy_move": {"pages_rendered": 0, "keypoints_detected": 0, "candidate_matches": 0, "ransac_inliers": 0}, "pdf_objects": {"image_placements_inspected": 0, "overlap_candidates": 0, "transparent_overlays": 0}}
            check_skips = {key: [] for key in CHECKS}
            for pindex, page in enumerate(doc):
                page_no = pindex + 1
                text = page.get_text("dict")
                spans = []
                line_groups = []
                for block in text.get("blocks", []):
                    for line in block.get("lines", []):
                        line_spans = []
                        for span in line.get("spans", []):
                            if span.get("text", "").strip():
                                spans.append(span)
                                line_spans.append(span)
                        if len(line_spans) > 1:
                            line_groups.append(line_spans)
                font_sizes = [float(s.get("size", 0)) for s in spans if float(s.get("size", 0)) > 0]
                check_pages["font_baseline"].append(page_no)
                check_metrics["font_baseline"]["text_spans_inspected"] += len(spans)
                if not spans:
                    check_skips["font_baseline"].append({"page_number": page_no, "reason": "No extractable PDF text spans; scanned content is not OCR text in the PDF object layer."})
                body = [s for s in spans if float(s.get("size", 0)) > 0 and len(str(s.get("text", "")).strip()) >= 5]
                if len(body) >= 6:
                    baseline_size = statistics.median(font_sizes)
                    for span in body:
                        size = float(span.get("size", 0))
                        # Ignore likely headings and isolated large text, only report conspicuously small peers.
                        if size < baseline_size * FONT_SIZE_OUTLIER_RATIO and len(span.get("text", "").strip()) >= 12:
                            check_metrics["font_baseline"]["font_outliers"] += 1
                            observations.append({"finding_type": "visual_font_size_outlier", "check_type": "font_baseline", "algorithm": "PyMuPDF span font-size peer comparison (page median)", "page_number": page_no, "regions": [_box(span["bbox"])], "block_id": None, "quoted_text": span["text"][:300], "metrics": {"font": span.get("font"), "font_size_pt": round(size, 2), "font_flags": span.get("flags"), "page_median_font_size_pt": round(baseline_size, 2)}, "explanation": "A text span is substantially smaller than the page's other non-empty text spans.", "limitations": "Footnotes, labels, tables, and intentional typographic hierarchy may explain this difference; no authenticity conclusion is implied."})
                for line_spans in line_groups:
                    comparable = [s for s in line_spans if len(s.get("text", "").strip()) >= 2 and float(s.get("size", 0)) > 0]
                    if len(comparable) < 3:
                        continue
                    baselines = [float(s.get("origin", [0, 0])[1]) for s in comparable]
                    median_baseline = statistics.median(baselines)
                    for span, baseline in zip(comparable, baselines):
                        if abs(baseline - median_baseline) >= BASELINE_DEVIATION_PT and abs(float(span.get("size", 0)) - statistics.median(float(s.get("size", 0)) for s in comparable)) <= 1.0:
                            check_metrics["font_baseline"]["baseline_outliers"] += 1
                            observations.append({"finding_type": "visual_baseline_outlier", "check_type": "font_baseline", "algorithm": "PyMuPDF text-span origin comparison within extracted line", "page_number": page_no, "regions": [_box(span["bbox"])], "quoted_text": span["text"][:300], "metrics": {"baseline_y_pt": round(baseline, 2), "peer_median_baseline_y_pt": round(median_baseline, 2), "deviation_pt": round(abs(baseline - median_baseline), 2), "font": span.get("font"), "font_size_pt": round(float(span.get("size", 0)), 2)}, "explanation": "A span's baseline differs from other similarly sized spans on the same extracted line.", "limitations": "Superscripts, subscripts, equations, and deliberate line styling may explain baseline differences. This observation alone does not establish alteration."})
                # PDF object/page placement inventory; observations only for measurable overlaps.
                images = page.get_images(full=True)
                placements = []
                for image in images:
                    xref = int(image[0])
                    for rect in page.get_image_rects(xref):
                        placements.append((xref, rect, image))
                check_pages["pdf_objects"].append(page_no)
                check_metrics["pdf_objects"]["image_placements_inspected"] += len(placements)
                for i, (xref, rect, image) in enumerate(placements):
                    for xref2, rect2, image2 in placements[i + 1:]:
                        intersection = rect & rect2
                        if intersection.is_empty:
                            continue
                        smaller = min(rect.get_area(), rect2.get_area())
                        overlap = intersection.get_area() / smaller if smaller else 0
                        if overlap > 0.8 and xref != xref2:
                            check_metrics["pdf_objects"]["overlap_candidates"] += 1
                            observations.append({"finding_type": "visual_overlapping_pdf_objects", "check_type": "pdf_objects", "algorithm": "PyMuPDF embedded image placement rectangle overlap", "page_number": page_no, "regions": [_box(rect), _box(rect2)], "metrics": {"xref_a": xref, "xref_b": xref2, "overlap_fraction_of_smaller": round(overlap, 4)}, "explanation": "Two distinct embedded image objects occupy substantially overlapping page coordinates.", "limitations": "Layered graphics, masks, and designed compositions commonly overlap. Inspect the original PDF object structure before interpreting."})
                # inspect images with alpha and dimensions; alpha itself is reported only when meaningfully placed over text.
                text_rects = [fitz.Rect(s["bbox"]) for s in spans]
                for xref, rect, image in placements:
                    try:
                        extracted = doc.extract_image(xref)
                        has_alpha = bool(extracted.get("smask", 0))
                    except Exception:
                        has_alpha = False
                    if has_alpha and any((rect & tr).get_area() > 0 for tr in text_rects):
                        check_metrics["pdf_objects"]["transparent_overlays"] += 1
                        observations.append({"finding_type": "visual_transparent_object_overlay", "check_type": "pdf_objects", "algorithm": "PyMuPDF image soft-mask and text bounding-box intersection", "page_number": page_no, "regions": [_box(rect)], "metrics": {"image_xref": xref, "width_px": image[2], "height_px": image[3], "soft_mask_present": True}, "explanation": "A soft-masked image object intersects extracted text geometry on this page.", "limitations": "Transparency and text/image overlap are common design and scan behaviors; this is a structural observation only."})
                # 200 DPI page-space rendering retains small details while
                # keeping regions reported below in PDF points for the source viewer.
                render_scale = 200 / 72
                pix = page.get_pixmap(matrix=fitz.Matrix(render_scale, render_scale), colorspace=fitz.csGRAY, alpha=False)
                gray = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
                copy_metrics = {}
                copy_observations = _image_regions(gray, page_no, document_id, copy_metrics)
                check_metrics["copy_move"]["pages_rendered"] += 1
                for metric in ("keypoints_detected", "candidate_matches", "ransac_inliers"):
                    check_metrics["copy_move"][metric] += copy_metrics.get(metric, 0)
                check_pages["copy_move"].append(page_no)
                # A PDF page raster mixes text, vector art, photographs and scans.
                # Do not attribute page-wide texture variation to compression.
                for item in copy_observations:
                    item["regions"] = [{k: round(v / render_scale, 2) for k, v in region.items()} for region in item["regions"]]
                    observations.append(item)
            checks = [_check_result(key, "executed", value, pages=check_pages[key], metrics=check_metrics[key], skipped=check_skips[key]) for key, value in CHECKS.items() if key != "compression_resampling"]
            checks.append(_check_result("compression_resampling", "unavailable", CHECKS["compression_resampling"], skipped=[{"reason": "Page raster combines text, vector art, photographs, and scan content; no source-image codec region can be isolated reliably."}]))
            return {"status": "completed", "page_count": len(doc), "checks": checks, "observations": observations}
        finally:
            doc.close()
    if content_type not in {"image/jpeg", "image/png", "image/tiff"} and path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".tif", ".tiff"}:
        return {"status": "not_implemented", "page_count": 0, "checks": [], "observations": [], "reason": "Unsupported source document format."}
    encoded = np.fromfile(str(path), dtype=np.uint8)
    image_pages = []
    if path.suffix.lower() in {".tif", ".tiff"} or content_type == "image/tiff":
        decoded, frames = cv2.imdecodemulti(encoded, cv2.IMREAD_GRAYSCALE)
        if decoded:
            image_pages = list(frames)
    if not image_pages:
        image = cv2.imdecode(encoded, cv2.IMREAD_GRAYSCALE)
        if image is not None:
            image_pages = [image]
    if not image_pages:
        raise ValueError("Stored image could not be decoded")
    jpeg_source = path.suffix.lower() in {".jpg", ".jpeg"} or content_type == "image/jpeg"
    image_copy_metrics = {"pages_rendered": len(image_pages), "keypoints_detected": 0, "candidate_matches": 0, "ransac_inliers": 0}
    for page_number, image in enumerate(image_pages, start=1):
        page_copy_metrics = {}
        observations.extend(_image_regions(image, page_number, document_id, page_copy_metrics))
        for metric in ("keypoints_detected", "candidate_matches", "ransac_inliers"):
            image_copy_metrics[metric] += page_copy_metrics.get(metric, 0)
        observations.extend(_texture_candidates(image, page_number))
        if jpeg_source:
            observations.extend(_jpeg_blockiness_candidates(image, page_number))
    checks = [
        _check_result("font_baseline", "unavailable", CHECKS["font_baseline"], skipped=[{"reason": "Source is a raster image; no PDF text spans exist."}]),
        _check_result("copy_move", "executed", CHECKS["copy_move"], pages=list(range(1, len(image_pages) + 1)), metrics=image_copy_metrics),
        _check_result("compression_resampling", "executed" if jpeg_source else "partial", CHECKS["compression_resampling"], pages=list(range(1, len(image_pages) + 1)), metrics={"jpeg_blockiness_observations": sum(1 for item in observations if item["finding_type"] == "visual_local_jpeg_blockiness"), "texture_observations": sum(1 for item in observations if item["finding_type"] == "visual_local_texture_variation")}, skipped=[] if jpeg_source else [{"reason": "JPEG block-boundary analysis requires a JPEG source; texture screening alone cannot identify compression."}]),
        _check_result("pdf_objects", "unavailable", CHECKS["pdf_objects"], skipped=[{"reason": "Source is a raster image; PDF object metadata does not apply."}]),
    ]
    return {"status": "completed", "page_count": len(image_pages), "checks": checks, "observations": observations}
