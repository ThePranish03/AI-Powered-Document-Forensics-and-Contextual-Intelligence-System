"""Long-lived PP-StructureV3 process used by the OCR worker.

PaddleOCR model initialization takes roughly a minute, so the worker keeps this
process alive and sends one JSON request per line instead of paying that cost
for every document.
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path


SUPPORTED_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".pdf", ".tif", ".tiff"}
MAGIC_SUFFIXES = (
    (b"\xff\xd8\xff", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"BM", ".bmp"),
    (b"%PDF", ".pdf"),
    (b"II*\x00", ".tiff"),
    (b"MM\x00*", ".tiff"),
)


def _input_path(path: str) -> tuple[str, str | None]:
    """PaddleOCR picks its reader by file suffix; uploads are stored as bare UUIDs."""
    if os.path.splitext(path)[1].lower() in SUPPORTED_SUFFIXES:
        return path, None
    with open(path, "rb") as handle:
        header = handle.read(8)
    suffix = next((candidate for magic, candidate in MAGIC_SUFFIXES if header.startswith(magic)), None)
    if suffix is None:
        raise ValueError("Unsupported document format; expected JPEG, PNG, BMP, or PDF")
    handle, temporary = tempfile.mkstemp(suffix=suffix)
    os.close(handle)
    shutil.copyfile(path, temporary)
    return temporary, temporary


def _render_pdf_pages(path: str, dpi: int = 144) -> tuple[str, str, list[dict]]:
    """Rasterize PDF pages ourselves so OCR boxes have a known pixel space."""
    import fitz

    if Path(path).stat().st_size > 25 * 1024 * 1024:
        raise ValueError("PDF exceeds the OCR source byte limit")
    directory = tempfile.mkdtemp(prefix="docforensiq-ocr-")
    metadata: list[dict] = []
    try:
        with fitz.open(path) as document:
            if document.needs_pass:
                raise ValueError("Encrypted PDF cannot be rasterized without a password")
            if document.page_count == 0:
                raise ValueError("PDF contains no pages")
            if document.page_count > 250:
                raise ValueError("PDF exceeds the OCR page limit of 250")
            total_pixels = 0
            scale = dpi / 72
            for index in range(document.page_count):
                page = document.load_page(index)
                pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                total_pixels += pixmap.width * pixmap.height
                if total_pixels > 50_000_000:
                    raise ValueError("PDF exceeds the OCR raster pixel limit")
                image_path = os.path.join(directory, f"page-{index + 1:04d}.png")
                pixmap.save(image_path)
                rect = page.rect
                metadata.append({
                    "page_number": index + 1,
                    "width": pixmap.width,
                    "height": pixmap.height,
                    "bbox_space": "rendered_pixels_top_left",
                    "geometry": {
                        "mapping_version": "ocr_pixel_to_pdf_v1",
                        "render_width_px": pixmap.width,
                        "render_height_px": pixmap.height,
                        "page_width_points": float(rect.width),
                        "page_height_points": float(rect.height),
                        "origin_x_points": float(rect.x0),
                        "origin_y_points": float(rect.y0),
                        "page_rotation": int(page.rotation),
                        "render_dpi": dpi,
                        "renderer": f"PyMuPDF/{fitz.VersionBind}",
                    },
                })
        return directory, directory, metadata
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise


def _enabled(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _cleanup_temporary(path: str | None) -> None:
    """Remove either a suffix-adjusted input file or a rendered-page directory."""
    if not path:
        return
    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.unlink(path)
    except OSError as exc:
        # Cleanup failure must not turn successful OCR into a worker crash.
        print(f"OCR temporary cleanup failed ({type(exc).__name__})", file=sys.stderr)


def _page_metadata(path: str, result: list) -> list[dict]:
    if os.path.splitext(path)[1].lower() in {".pdf"}:
        return [{"page_number": index + 1, "width": None, "height": None} for index in range(len(result))]
    try:
        from PIL import Image
        with Image.open(path) as image:
            pages = []
            frame_count = getattr(image, "n_frames", 1)
            for index in range(frame_count):
                image.seek(index)
                pages.append({"page_number": index + 1, "width": image.width, "height": image.height})
            return pages
    except (OSError, ValueError):
        return [{"page_number": index + 1, "width": None, "height": None} for index in range(len(result))]


def main() -> None:
    protocol = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
    os.dup2(2, 1)
    os.environ.setdefault("DISABLE_MODEL_SOURCE_CHECK", "True")

    def reply(payload: dict) -> None:
        protocol.write(json.dumps(payload, ensure_ascii=False) + "\n")
        protocol.flush()

    try:
        from paddleocr import PPStructureV3
        pipeline = PPStructureV3(
            layout_detection_model_name=os.getenv("PADDLEOCR_LAYOUT_MODEL", "PP-DocLayout_plus-L"),
            text_detection_model_name=os.getenv("PADDLEOCR_DET_MODEL", "PP-OCRv5_mobile_det"),
            text_recognition_model_name=os.getenv("PADDLEOCR_REC_MODEL", "PP-OCRv5_mobile_rec"),
            use_doc_orientation_classify=_enabled("PADDLEOCR_DOC_ORIENTATION", True),
            use_doc_unwarping=_enabled("PADDLEOCR_DOC_UNWARPING", False),
            use_textline_orientation=_enabled("PADDLEOCR_TEXTLINE_ORIENTATION", False),
            use_seal_recognition=_enabled("PADDLEOCR_SEAL_RECOGNITION", False),
            use_table_recognition=_enabled("PADDLEOCR_TABLE_RECOGNITION", False),
            use_formula_recognition=_enabled("PADDLEOCR_FORMULA_RECOGNITION", False),
            use_chart_recognition=_enabled("PADDLEOCR_CHART_RECOGNITION", False),
            use_region_detection=_enabled("PADDLEOCR_REGION_DETECTION", False),
        )
    except Exception as exc:
        reply({"ok": False, "error": f"PaddleOCR initialization failed: {exc}"})
        raise
    reply({"ok": True, "ready": True})

    for line in sys.stdin:
        if not line.strip():
            continue
        temporary = None
        try:
            request = json.loads(line)
            input_path, temporary = _input_path(request["path"])
            if os.path.splitext(input_path)[1].lower() == ".pdf":
                pdf_source = input_path
                input_path, raster_directory, page_metadata = _render_pdf_pages(pdf_source)
                if temporary:
                    os.unlink(temporary)
                temporary = raster_directory
                payload = []
                for index, metadata in enumerate(page_metadata):
                    result = list(pipeline.predict(input=os.path.join(input_path, f"page-{index + 1:04d}.png")))
                    item = result[0].json if result and hasattr(result[0], "json") else (result[0] if result else {})
                    if isinstance(item, dict):
                        item = {**item, "page_index": index}
                    payload.append(item)
            else:
                results = list(pipeline.predict(input=input_path))
                payload = [result.json if hasattr(result, "json") else str(result) for result in results]
                page_metadata = _page_metadata(input_path, payload)
            reply({"ok": True, "result": payload, "page_metadata": page_metadata})
        except Exception as exc:
            reply({"ok": False, "error": str(exc)[:500]})
        finally:
            _cleanup_temporary(temporary)


if __name__ == "__main__":
    main()
