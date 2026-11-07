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


def _enabled(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


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
            results = list(pipeline.predict(input=input_path))
            payload = [result.json if hasattr(result, "json") else str(result) for result in results]
            reply({"ok": True, "result": payload, "page_metadata": _page_metadata(input_path, payload)})
        except Exception as exc:
            reply({"ok": False, "error": str(exc)[:500]})
        finally:
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass


if __name__ == "__main__":
    main()
