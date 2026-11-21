"""Bounded PDF structure inspection that reports facts and parser limitations."""
from __future__ import annotations

import hashlib
import json
import multiprocessing
import re
import tempfile
import unicodedata
import uuid
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path


MAX_PAGES = 250
MAX_XREFS = 50_000
MAX_TEXT_SPANS_PER_PAGE = 500
MAX_TEXT_SPANS_PER_DOCUMENT = 20_000
MAX_IMAGES_PER_DOCUMENT = 2_000
MAX_IMAGES_PER_PAGE = 500
MAX_IMAGE_PLACEMENTS_PER_PAGE = 200
MAX_IMAGE_PLACEMENTS_PER_DOCUMENT = 10_000
MAX_IMAGE_PIXELS_TO_HASH = 40_000_000
MAX_EXTRACTED_IMAGE_BYTES = 25 * 1024 * 1024
MAX_EXTRACTED_IMAGE_BYTES_PER_RUN = 100 * 1024 * 1024
MAX_REVISION_XREF_ENTRIES = 50_000


def _box(rect) -> dict[str, float]:
    return {"x0": round(float(rect.x0), 3), "y0": round(float(rect.y0), 3), "x1": round(float(rect.x1), 3), "y1": round(float(rect.y1), 3)}


def _clean(value, limit: int = 512) -> str | None:
    if value is None:
        return None
    return str(value)[:limit]


def _classic_revision_inventory(path: Path) -> dict:
    """Read classic xref tables via /Prev without resolving object contents.

    Xref streams and hybrid-reference files are explicitly reported unsupported.
    Each revision is an inventory of object numbers, generations and byte offsets;
    offsets are not evidence of manipulation on their own.
    """
    data = path.read_bytes()
    starts = list(re.finditer(rb"startxref\s+(\d+)\s+%%EOF", data))
    if not starts:
        return {"status": "unavailable", "revisions": [], "limitations": ["No classic startxref marker was found."]}
    offset = int(starts[-1].group(1))
    revisions = []
    visited: set[int] = set()
    total_revision_entries = 0
    while offset and len(revisions) < 100:
        if offset in visited or offset >= len(data):
            return {"status": "partial", "revisions": revisions, "limitations": ["The xref predecessor chain is cyclic or points outside the source bytes."]}
        visited.add(offset)
        cursor = offset
        while data[cursor:cursor + 1] in b" \t\r\n":
            cursor += 1
            if cursor >= len(data):
                break
        if not data[cursor:cursor + 4] == b"xref":
            return {"status": "unsupported", "revisions": revisions, "limitations": ["At least one revision uses an xref stream; object-level revision diff is not implemented for xref streams."]}
        cursor += 4
        inventory: dict[int, dict] = {}
        entries_seen = 0
        while True:
            while data[cursor:cursor + 1] in b" \t\r\n":
                cursor += 1
            if data[cursor:cursor + 7] == b"trailer":
                cursor += 7
                break
            header = re.match(rb"(\d+)\s+(\d+)\s*", data[cursor:cursor + 80])
            if not header:
                return {"status": "partial", "revisions": revisions, "limitations": ["Malformed classic xref subsection header."]}
            first, count = map(int, header.groups())
            cursor += header.end()
            if count + entries_seen > MAX_REVISION_XREF_ENTRIES:
                return {"status": "partial", "revisions": revisions, "limitations": ["Revision xref entry limit reached."]}
            entries_seen += count
            total_revision_entries += count
            if total_revision_entries > MAX_REVISION_XREF_ENTRIES:
                return {"status": "partial", "revisions": revisions, "limitations": ["Total revision xref entry limit reached."]}
            for object_number in range(first, first + count):
                line = data[cursor:cursor + 32]
                match = re.match(rb"\s*(\d{1,20})\s+(\d{1,8})\s+([nf])", line)
                if not match:
                    return {"status": "partial", "revisions": revisions, "limitations": ["Malformed classic xref entry."]}
                byte_offset, generation, state = match.groups()
                inventory[object_number] = {
                    "generation": int(generation),
                    "state": "in_use" if state == b"n" else "free",
                    "byte_offset": int(byte_offset),
                }
                newline = data.find(b"\n", cursor, cursor + 64)
                if newline < 0:
                    return {"status": "partial", "revisions": revisions, "limitations": ["Truncated xref entry."]}
                cursor = newline + 1
        dictionary_start = data.find(b"<<", cursor, min(len(data), cursor + 100_000))
        dictionary_end = data.find(b">>", dictionary_start + 2, min(len(data), cursor + 100_000)) if dictionary_start >= 0 else -1
        if dictionary_start < 0 or dictionary_end < 0:
            return {"status": "partial", "revisions": revisions, "limitations": ["Classic xref trailer dictionary could not be read."]}
        trailer = data[dictionary_start:dictionary_end + 2]
        if re.search(rb"/XRefStm\s+\d+", trailer):
            return {"status": "unsupported", "revisions": revisions, "limitations": ["Hybrid-reference PDFs contain an xref stream; complete revision inventory is unsupported."]}
        previous = re.search(rb"/Prev\s+(\d+)", trailer)
        revisions.append({"revision_index_from_latest": len(revisions), "xref_offset": offset, "object_entries": inventory})
        offset = int(previous.group(1)) if previous else 0
    if offset:
        return {"status": "partial", "revisions": revisions, "limitations": ["Revision count exceeds the 100-revision analysis limit."]}
    revisions.reverse()
    active: dict[int, dict] = {}
    for index, revision in enumerate(revisions):
        changes = {"added": [], "updated": [], "freed": [], "unchanged": []}
        for object_number, entry in sorted(revision["object_entries"].items()):
            if object_number == 0:
                continue
            previous = active.get(object_number)
            if entry["state"] == "free":
                changes["freed" if previous and previous.get("state") == "in_use" else "updated"].append(object_number)
            elif previous is None or previous.get("state") != "in_use":
                changes["added"].append(object_number)
            elif previous.get("generation") != entry["generation"] or previous.get("byte_offset") != entry["byte_offset"]:
                changes["updated"].append(object_number)
            else:
                changes["unchanged"].append(object_number)
            active[object_number] = entry
        revision["revision_number"] = index + 1
        revision["object_inventory"] = [
            {"object_number": object_number, **entry}
            for object_number, entry in sorted(revision["object_entries"].items())
        ]
        revision["changes"] = changes
        del revision["object_entries"]
    return {"status": "completed", "revisions": revisions, "limitations": ["Inventory is limited to classic xref tables; offset or object changes alone do not establish manipulation."]}


def _normalized_text(text: object) -> str:
    decomposed = unicodedata.normalize("NFKC", str(text or "")).casefold()
    return " ".join(re.findall(r"[\w]+", decomposed, flags=re.UNICODE))


def _ocr_bbox(box: object) -> dict[str, float] | None:
    try:
        if isinstance(box, dict):
            if all(key in box for key in ("x0", "y0", "x1", "y1")):
                values = [float(box[key]) for key in ("x0", "y0", "x1", "y1")]
            else:
                return None
        elif isinstance(box, (list, tuple)) and len(box) == 4 and all(isinstance(item, (int, float)) for item in box):
            values = [float(item) for item in box]
        elif isinstance(box, (list, tuple)) and len(box) >= 4 and all(isinstance(item, (list, tuple)) and len(item) >= 2 for item in box):
            xs, ys = [float(item[0]) for item in box], [float(item[1]) for item in box]
            values = [min(xs), min(ys), max(xs), max(ys)]
        else:
            return None
        x0, y0, x1, y1 = values
        if not all(value == value and abs(value) < 1e9 for value in values) or x1 <= x0 or y1 <= y0:
            return None
        return {"x0": x0, "y0": y0, "x1": x1, "y1": y1}
    except (TypeError, ValueError, OverflowError):
        return None


def compare_pdf_native_text_to_ocr(pdf_report: dict, ocr_pages: list[dict], ocr_blocks: list[dict]) -> dict:
    """Compare text only on pages and boxes with a persisted renderer mapping."""
    page_map = {int(page.get("page_number", 0)): page for page in ocr_pages if isinstance(page, dict)}
    blocks_by_page: dict[int, list[dict]] = defaultdict(list)
    for block in ocr_blocks:
        if isinstance(block, dict) and isinstance(block.get("page_index"), int):
            blocks_by_page[block["page_index"] + 1].append(block)
    comparisons, skipped = [], []
    for pdf_page in pdf_report.get("pages", []):
        page_number = int(pdf_page.get("page_number", 0))
        ocr_page = page_map.get(page_number)
        geometry = next((block.get("page_geometry") for block in blocks_by_page[page_number] if isinstance(block.get("page_geometry"), dict)), None)
        if not ocr_page or not geometry:
            skipped.append({"page_number": page_number, "reason": "validated_render_geometry_unavailable"})
            continue
        render_width, render_height = geometry.get("render_width_px"), geometry.get("render_height_px")
        page_width, page_height = geometry.get("page_width_points"), geometry.get("page_height_points")
        if (ocr_page.get("width") != render_width or ocr_page.get("height") != render_height or
                not all(isinstance(item, (int, float)) and item > 0 for item in (render_width, render_height, page_width, page_height)) or
                abs(page_width - float(pdf_page.get("width_points", 0))) > 1.0 or abs(page_height - float(pdf_page.get("height_points", 0))) > 1.0 or
                geometry.get("mapping_version") != "ocr_pixel_to_pdf_v1"):
            skipped.append({"page_number": page_number, "reason": "render_geometry_does_not_match_pdf_page"})
            continue
        origin_x, origin_y = float(geometry.get("origin_x_points", 0)), float(geometry.get("origin_y_points", 0))
        native_spans = [span for span in pdf_page.get("text_spans", []) if _normalized_text(span.get("text")) and _ocr_bbox(span.get("bbox"))]
        ocr_items = []
        for block in blocks_by_page[page_number]:
            if block.get("bbox_space") != "rendered_pixels_top_left" or block.get("page_geometry") != geometry:
                continue
            box = _ocr_bbox(block.get("engine_bbox"))
            text = block.get("text")
            if not box or not isinstance(text, str) or not _normalized_text(text):
                continue
            if box["x0"] < -2 or box["y0"] < -2 or box["x1"] > render_width + 2 or box["y1"] > render_height + 2:
                continue
            mapped = {"x0": origin_x + box["x0"] * page_width / render_width, "y0": origin_y + box["y0"] * page_height / render_height, "x1": origin_x + box["x1"] * page_width / render_width, "y1": origin_y + box["y1"] * page_height / render_height}
            ocr_items.append((block, mapped))
        if not ocr_items:
            skipped.append({"page_number": page_number, "reason": "validated_ocr_boxes_unavailable"})
            continue
        for block, mapped in ocr_items:
            for span_index, span in enumerate(native_spans):
                native_box = span["bbox"]
                intersection_w = max(0, min(mapped["x1"], native_box["x1"]) - max(mapped["x0"], native_box["x0"]))
                intersection_h = max(0, min(mapped["y1"], native_box["y1"]) - max(mapped["y0"], native_box["y0"]))
                intersection = intersection_w * intersection_h
                area = min((mapped["x1"] - mapped["x0"]) * (mapped["y1"] - mapped["y0"]), (native_box["x1"] - native_box["x0"]) * (native_box["y1"] - native_box["y0"]))
                overlap = intersection / area if area > 0 else 0
                if overlap < 0.15:
                    continue
                ocr_text, native_text = _normalized_text(block["text"]), _normalized_text(span.get("text"))
                if ocr_text == native_text:
                    continue
                ratio = round(SequenceMatcher(None, ocr_text, native_text, autojunk=False).ratio(), 4)
                comparisons.append({
                    "kind": "overlapping_text_disagreement", "page_number": page_number,
                    "overlap_ratio": round(overlap, 4), "text_similarity": ratio,
                    "native_text": span.get("text"), "ocr_text": block.get("text"),
                    "native_source_ref": {"document_sha256": pdf_report.get("source_sha256"), "page_number": page_number, "span_index": span_index, "bbox_pdf_points": native_box},
                    "ocr_source_ref": {"block_id": block.get("block_id"), "source_reference": block.get("source_path"), "page_number": page_number, "bbox_rendered_pixels": block.get("engine_bbox"), "bbox_pdf_points": {key: round(value, 3) for key, value in mapped.items()}, "mapping": geometry},
                    "interpretation": "OCR and native text disagree in overlapping geometry. This is an extraction discrepancy for review, not evidence of alteration.",
                })
    return {"schema_version": "native_text_ocr_comparison_v1", "status": "partial" if skipped else "completed", "method": "validated_pixel_to_pdf_bbox_overlap_and_normalized_text_comparison", "discrepancies": comparisons, "skipped_pages": skipped, "limitations": ["OCR and native text disagreements may be caused by OCR error, hidden text, reading order, or clipping; they do not establish manipulation."]}


def verify_pdf_signatures(path: Path) -> dict:
    """Verify signature byte-range integrity with pyHanko, without asserting trust."""
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.validation import ValidationContext, validate_pdf_signature

    signatures = []
    limitations = ["Certificate trust is evaluated without configured trust roots or network revocation fetching; trusted=False is not a conclusion about signer identity."]
    with path.open("rb") as stream:
        reader = PdfFileReader(stream, strict=False)
        context = ValidationContext(trust_roots=[], allow_fetching=False, revocation_mode="soft-fail")
        for embedded in reader.embedded_signatures:
            field_name = str(getattr(embedded, "field_name", "unknown"))[:256]
            try:
                status = validate_pdf_signature(embedded, context, skip_diff=False)
                coverage = getattr(status.coverage, "name", str(status.coverage)) if status.coverage is not None else None
                signatures.append({
                    "field_name": field_name,
                    "cryptographic_integrity": "valid" if status.intact else "invalid",
                    "certificate_validation": "trusted" if status.trusted else "untrusted_or_unavailable",
                    "validity_summary": str(status.summary),
                    "signature_coverage": coverage,
                    "modification_level": str(status.modification_level) if status.modification_level is not None else None,
                    "docmdp_ok": status.docmdp_ok,
                    "signer_reported_time": status.signer_reported_dt.isoformat() if status.signer_reported_dt else None,
                    "digest_algorithm": status.md_algorithm,
                    "signature_algorithm": status.pkcs7_signature_mechanism,
                })
            except Exception as exc:
                signatures.append({"field_name": field_name, "cryptographic_integrity": "verification_error", "certificate_validation": "unavailable", "error_type": type(exc).__name__, "error": str(exc)[:400]})
    from importlib.metadata import version
    return {"library": "pyHanko", "library_version": version("pyHanko"), "status": "completed" if signatures else "no_signatures", "signatures": signatures, "limitations": limitations}


def analyze_pdf(path: Path, source_sha256: str, *, artifact_dir: Path | None = None) -> dict:
    """Return a deterministic structural report for a stored PDF.

    PyMuPDF is intentionally used as an application parser, not represented as a
    byte-perfect PDF validator. Recovery details, per-revision object diffs, and
    signature verification are explicitly identified as unsupported here.
    """
    import fitz

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != source_sha256:
        raise ValueError("Stored source SHA-256 does not match the recorded evidence hash")

    with path.open("rb") as stream:
        header = stream.read(1024)
    header_match = re.search(rb"%PDF-(\d\.\d)", header)
    report: dict = {
        "schema_version": "pdf_forensics_v1",
        "source_sha256": source_sha256,
        "parser": {"name": "PyMuPDF", "version": fitz.VersionBind},
        "document": {},
        "pages": [],
        "objects": {"inventory": [], "inventory_truncated": False},
        "revision_inventory": _classic_revision_inventory(path),
        "limitations": [
            "Parser recovery behavior is reported only as a boolean; detailed recovery diagnostics are not exposed by this parser.",
            "Revision changes are reconstructed only for classic xref tables; xref streams and hybrid-reference PDFs remain unsupported.",
            "Digital signature integrity is checked with pyHanko. Certificate trust is evaluated without configured trust roots or live revocation fetching.",
            "Metadata and structural anomalies are descriptive facts; neither establishes document alteration or intent.",
        ],
    }

    stored_artifacts: list[dict] = []
    artifact_bytes_written = 0
    with fitz.open(path) as doc:
        if bool(getattr(doc, "needs_pass", False)):
            report["status"] = "unavailable"
            report["document"] = {
                "header_version": header_match.group(1).decode("ascii") if header_match else None,
                "encrypted": True,
                "page_count": None,
                "revision_count": int(getattr(doc, "version_count", 1) or 1),
                "parser_repaired": bool(getattr(doc, "is_repaired", False)),
            }
            report["limitations"].append("The PDF is encrypted and no password was supplied; page, object, text, and signature inspection was not performed.")
            report["signature_verification"] = {"library": "pyHanko", "status": "unavailable_encrypted", "signatures": [], "limitations": ["No password was supplied."]}
            return report
        page_count = doc.page_count
        xref_count = max(0, int(doc.xref_length()) - 1)
        if page_count > MAX_PAGES:
            raise ValueError(f"PDF exceeds the structural analysis limit of {MAX_PAGES} pages")
        if xref_count > MAX_XREFS:
            raise ValueError(f"PDF exceeds the structural analysis limit of {MAX_XREFS} indirect objects")

        metadata = dict(doc.metadata or {})
        catalog_xref = int(doc.pdf_catalog())
        catalog_version = doc.xref_get_key(catalog_xref, "Version")
        trailer = doc.pdf_trailer()
        report["document"] = {
            "header_version": header_match.group(1).decode("ascii") if header_match else None,
            "catalog_version": _clean(catalog_version[1]) if catalog_version[0] != "null" else None,
            "page_count": page_count,
            "xref_entry_count": xref_count,
            "revision_count": int(getattr(doc, "version_count", 1) or 1),
            "parser_repaired": bool(getattr(doc, "is_repaired", False)),
            "encrypted": bool(doc.is_encrypted),
            "metadata": {key: _clean(value) for key, value in metadata.items() if value not in (None, "")},
            "xmp_present": bool(doc.get_xml_metadata()),
            "trailer_keys_present": sorted(set(re.findall(r"/(Root|Info|ID|Size|Prev|XRefStm)\b", trailer))),
            "catalog_version_entry_type": catalog_version[0],
        }

        image_records: dict[int, dict] = {}
        signature_xrefs: list[int] = []
        object_types: dict[str, int] = {}
        javascript_reference_count = 0
        for xref in range(1, xref_count + 1):
            try:
                raw = doc.xref_object(xref, compressed=False)
                type_match = re.search(r"/Type\s*/([A-Za-z0-9]+)", raw)
                object_type = type_match.group(1) if type_match else "Unspecified"
                object_types[object_type] = object_types.get(object_type, 0) + 1
                if re.search(r"/Type\s*/Sig\b", raw):
                    signature_xrefs.append(xref)
                if re.search(r"/(?:JavaScript|JS)\b", raw):
                    javascript_reference_count += 1
                record = {"object_number": xref, "type": object_type, "stream": bool(doc.xref_is_stream(xref))}
                if len(report["objects"]["inventory"]) < 10_000:
                    report["objects"]["inventory"].append(record)
                else:
                    report["objects"]["inventory_truncated"] = True
            except Exception:
                object_types["Unreadable"] = object_types.get("Unreadable", 0) + 1

        report["objects"]["type_counts"] = object_types
        report["document"]["signature_objects"] = [{"object_number": xref, "status": "present_unverified"} for xref in signature_xrefs]
        report["document"]["javascript_reference_count"] = javascript_reference_count
        report["document"]["embedded_file_count"] = int(doc.embfile_count())
        report["document"]["embedded_files"] = [
            {"index": index, "filename": _clean(doc.embfile_info(index).get("filename"), 255), "size": doc.embfile_info(index).get("size")}
            for index in range(min(doc.embfile_count(), 200))
        ]

        remaining_text_spans = MAX_TEXT_SPANS_PER_DOCUMENT
        remaining_image_placements = MAX_IMAGE_PLACEMENTS_PER_DOCUMENT
        remaining_page_images = MAX_IMAGES_PER_DOCUMENT
        for page_index in range(page_count):
            page = doc.load_page(page_index)
            fonts = []
            for font in page.get_fonts(full=True):
                xref, extension, kind, basefont, resource_name, encoding = font[:6]
                embedded = None
                if xref:
                    try:
                        embedded = bool(doc.extract_font(int(xref))[3])
                    except Exception:
                        embedded = None
                fonts.append({"object_number": int(xref), "extension": _clean(extension, 64), "type": _clean(kind, 64), "base_font": _clean(basefont, 256), "resource_name": _clean(resource_name, 128), "encoding": _clean(encoding, 128), "embedded": embedded})

            spans = []
            text_dict = page.get_text("dict", sort=False)
            total_page_spans = sum(len(line.get("spans", [])) for block in text_dict.get("blocks", []) for line in block.get("lines", []))
            page_span_limit = min(MAX_TEXT_SPANS_PER_PAGE, remaining_text_spans)
            for block in text_dict.get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        if len(spans) >= page_span_limit:
                            break
                        spans.append({
                            "text": _clean(span.get("text"), 512),
                            "bbox": {key: round(float(value), 3) for key, value in zip(("x0", "y0", "x1", "y1"), span.get("bbox", ()))},
                            "font": _clean(span.get("font"), 256),
                            "font_size": round(float(span.get("size", 0)), 3),
                            "origin": {"x": round(float(span.get("origin", (0, 0))[0]), 3), "y": round(float(span.get("origin", (0, 0))[1]), 3)},
                            "color": span.get("color"),
                            "flags": span.get("flags"),
                        })
            remaining_text_spans -= len(spans)

            page_images = []
            page_image_refs = page.get_images(full=True)
            page_image_limit = min(MAX_IMAGES_PER_PAGE, remaining_page_images)
            for image in page_image_refs[:page_image_limit]:
                xref, smask, width, height, bpc, colorspace, alt_colorspace, name, filter_name, referencer = image[:10]
                rects = []
                transforms = []
                placements = page.get_image_rects(int(xref), transform=True)
                placement_limit = min(MAX_IMAGE_PLACEMENTS_PER_PAGE, remaining_image_placements)
                selected_placements = placements[:placement_limit]
                for rect, matrix in selected_placements:
                    rects.append(_box(rect))
                    transforms.append([round(float(value), 6) for value in matrix])
                remaining_image_placements -= len(selected_placements)
                remaining_page_images -= 1
                record = image_records.setdefault(int(xref), {
                    "object_number": int(xref), "width": int(width), "height": int(height), "bits_per_component": int(bpc),
                    "color_space": _clean(colorspace, 64), "filter": _clean(filter_name, 128), "soft_mask_object_number": int(smask) if smask else None,
                    "sha256": None, "artifact_id": None,
                })
                if record["sha256"] is None and int(width) * int(height) > MAX_IMAGE_PIXELS_TO_HASH:
                    record["extraction_status"] = "unavailable_size_limit"
                elif record["sha256"] is None:
                    artifact_path = None
                    try:
                        extracted = doc.extract_image(int(xref))
                        image_bytes = extracted["image"]
                        record["sha256"] = hashlib.sha256(image_bytes).hexdigest()
                        if artifact_dir is not None:
                            if len(image_bytes) > MAX_EXTRACTED_IMAGE_BYTES or artifact_bytes_written + len(image_bytes) > MAX_EXTRACTED_IMAGE_BYTES_PER_RUN:
                                record["extraction_status"] = "not_stored_size_limit"
                            else:
                                artifact_id = uuid.uuid4()
                                storage_key = uuid.uuid4().hex
                                extension = str(extracted.get("ext") or "bin").lower()
                                mime_type = {
                                    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                                    "jp2": "image/jp2", "jpx": "image/jpx", "tif": "image/tiff",
                                    "tiff": "image/tiff", "jbig2": "image/jbig2", "ppm": "image/x-portable-pixmap",
                                }.get(extension, "application/octet-stream")
                                artifact_dir.mkdir(parents=True, exist_ok=True)
                                artifact_path = artifact_dir / storage_key
                                with artifact_path.open("xb") as target:
                                    target.write(image_bytes)
                                artifact_bytes_written += len(image_bytes)
                                record["artifact_id"] = str(artifact_id)
                                record["extraction_status"] = "stored"
                                stored_artifacts.append({
                                    "id": str(artifact_id), "object_number": int(xref), "storage_key": storage_key,
                                    "mime_type": mime_type, "byte_size": len(image_bytes), "width_px": int(width),
                                    "height_px": int(height), "sha256": record["sha256"],
                                })
                    except Exception:
                        if artifact_path is not None:
                            artifact_path.unlink(missing_ok=True)
                        record["extraction_status"] = "unavailable"
                page_images.append({**record, "resource_name": _clean(name, 128), "referencer_object_number": int(referencer) if referencer else None, "placements": rects, "placement_matrices": transforms, "placements_truncated": len(placements) > len(selected_placements)})

            annotations = []
            for annotation in page.annots() or []:
                annotations.append({"object_number": int(annotation.xref), "type": _clean(annotation.type[1], 64), "rect": _box(annotation.rect)})
            widgets = []
            for widget in page.widgets() or []:
                widgets.append({"object_number": int(widget.xref), "field_name": _clean(widget.field_name, 256), "field_type": _clean(widget.field_type_string, 64), "rect": _box(widget.rect)})

            report["pages"].append({
                "page_number": page_index + 1,
                "width_points": round(float(page.rect.width), 3),
                "height_points": round(float(page.rect.height), 3),
                "rotation": int(page.rotation),
                "media_box": _box(page.mediabox),
                "crop_box": _box(page.cropbox),
                "fonts": fonts,
                "text_spans": spans,
                "text_spans_truncated": total_page_spans > len(spans),
                "images_truncated": len(page_image_refs) > page_image_limit,
                "image_placements_truncated": remaining_image_placements == 0,
                "images": page_images,
                "annotations": annotations,
                "form_widgets": widgets,
            })
        report["document"]["embedded_images"] = list(image_records.values())[:MAX_IMAGES_PER_DOCUMENT]
        if len(image_records) > MAX_IMAGES_PER_DOCUMENT:
            report["document"]["embedded_images_truncated"] = True
        report["status"] = "partial" if report["document"]["parser_repaired"] else "completed"
    report["artifacts"] = stored_artifacts
    report["signature_verification"] = verify_pdf_signatures(path)
    return report


def _analysis_process_entry(path: str, source_sha256: str, artifact_dir: str | None, result_path: str) -> None:
    try:
        if __import__("os").name != "nt":
            try:
                import resource
                resource.setrlimit(resource.RLIMIT_AS, (2 * 1024 * 1024 * 1024, 2 * 1024 * 1024 * 1024))
                resource.setrlimit(resource.RLIMIT_CPU, (80, 85))
            except (ImportError, OSError, ValueError):
                pass
        result = analyze_pdf(Path(path), source_sha256, artifact_dir=Path(artifact_dir) if artifact_dir else None)
        envelope = {"ok": True, "result": result}
    except BaseException as exc:
        envelope = {"ok": False, "error_type": type(exc).__name__, "error": str(exc)[:1000]}
    Path(result_path).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")


def analyze_pdf_isolated(path: Path, source_sha256: str, *, artifact_dir: Path | None = None, timeout_seconds: int = 90) -> dict:
    """Parse untrusted PDFs in a killable process with a hard wall-clock timeout."""
    context = multiprocessing.get_context("spawn")
    with tempfile.TemporaryDirectory(prefix="docforensiq-pdf-worker-") as scratch:
        result_path = Path(scratch) / "result.json"
        process = context.Process(target=_analysis_process_entry, args=(str(path), source_sha256, str(artifact_dir) if artifact_dir else None, str(result_path)))
        process.start()
        process.join(timeout_seconds)
        if process.is_alive():
            process.terminate()
            process.join(5)
            if process.is_alive():
                process.kill()
                process.join(5)
            raise TimeoutError(f"PDF analysis exceeded its {timeout_seconds}-second process timeout")
        if not result_path.is_file():
            raise RuntimeError(f"PDF parser process exited without a report (exit code {process.exitcode})")
        envelope = json.loads(result_path.read_text(encoding="utf-8"))
        if not envelope.get("ok"):
            error_type = envelope.get("error_type")
            error = envelope.get("error", "PDF parser process failed")
            if error_type == "ValueError":
                raise ValueError(error)
            raise RuntimeError(error)
        return envelope["result"]
