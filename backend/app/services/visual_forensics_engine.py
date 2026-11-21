"""Provider-neutral visual forensics engine interface plus the TruFor worker adapter.

The TruFor engine never runs inside the FastAPI process. It is an HTTP client for a
dedicated inference worker (PyTorch + the official TruFor repository) that must be
deployed separately and whose license terms must be reviewed before use. When no
worker is configured, health reports unavailable and no model output is fabricated.
"""
from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from app.core.config import settings


@dataclass(frozen=True)
class EngineHealth:
    engine: str
    available: bool
    reason: str | None = None
    model_version: str | None = None
    checkpoint_sha256: str | None = None
    device: str | None = None

    def to_dict(self) -> dict:
        return {
            "engine": self.engine,
            "available": self.available,
            "reason": self.reason,
            "model_version": self.model_version,
            "checkpoint_sha256": self.checkpoint_sha256,
            "device": self.device,
        }


@dataclass
class EnginePageResult:
    page_number: int
    status: str
    integrity_score: float | None = None
    localization_map_path: Path | None = None
    reliability_map_path: Path | None = None
    map_width_px: int | None = None
    map_height_px: int | None = None
    regions: list[dict] = field(default_factory=list)
    diagnostics: dict = field(default_factory=dict)
    error_code: str | None = None
    error_detail: str | None = None


class VisualForensicsEngine(Protocol):
    def health(self) -> EngineHealth: ...

    def analyze_page(self, page_number: int, image_path: Path) -> EnginePageResult: ...


class TruForEngine:
    """HTTP adapter around the isolated TruFor inference worker.

    The worker is expected to expose:
      GET  {url}/health  -> {"available": true, "model_version": ..., "checkpoint_sha256": ..., "device": ...}
      POST {url}/analyze (multipart: image file) -> the official model outputs, untranslated.
    """

    def __init__(self, worker_url: str, timeout_seconds: float, model_version: str = "", checkpoint_sha256: str = "", device: str = "cpu") -> None:
        self.worker_url = worker_url
        self.timeout_seconds = timeout_seconds
        self.model_version = model_version
        self.checkpoint_sha256 = checkpoint_sha256
        self.device = device

    def health(self) -> EngineHealth:
        if not self.worker_url:
            return EngineHealth(
                engine="trufor",
                available=False,
                reason="TruFor worker URL is not configured (TRUFOR_WORKER_URL). The engine stays disabled and no model output is produced.",
                model_version=self.model_version or None,
                checkpoint_sha256=self.checkpoint_sha256 or None,
                device=self.device,
            )
        try:
            import httpx
        except ImportError:
            return EngineHealth(engine="trufor", available=False, reason="httpx is not installed in the API environment; the TruFor worker client cannot run.", device=self.device)
        try:
            response = httpx.get(f"{self.worker_url}/health", timeout=self.timeout_seconds)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Worker response must be an object")
        except Exception:
            return EngineHealth(engine="trufor", available=False, reason="TruFor worker is not reachable or returned an invalid health response.", device=self.device)
        return EngineHealth(
            engine="trufor",
            available=bool(payload.get("available")),
            reason=payload.get("reason"),
            model_version=payload.get("model_version") or (self.model_version or None),
            checkpoint_sha256=payload.get("checkpoint_sha256") or (self.checkpoint_sha256 or None),
            device=payload.get("device") or self.device,
        )

    def analyze_page(self, page_number: int, image_path: Path) -> EnginePageResult:
        if not self.worker_url:
            return EnginePageResult(page_number=page_number, status="unavailable", error_code="engine_not_configured", error_detail="TRUFOR_WORKER_URL is not set; no model output exists for this page.")
        try:
            import httpx
        except ImportError:
            return EnginePageResult(page_number=page_number, status="unavailable", error_code="engine_client_unavailable", error_detail="httpx is not installed in the API environment.")
        try:
            with open(image_path, "rb") as handle:
                files = {"image": (image_path.name, handle, "image/png")}
                response = httpx.post(f"{self.worker_url}/analyze", files=files, timeout=self.timeout_seconds)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Worker response must be an object")
        except Exception:
            return EnginePageResult(page_number=page_number, status="failed", error_code="engine_request_failed", error_detail="The inference worker request failed or returned an invalid response.", diagnostics={})
        localization_path = _save_map_payload(payload, "localization_map_png", image_path.parent, f"page{page_number}_localization")
        reliability_path = _save_map_payload(payload, "reliability_map_png", image_path.parent, f"page{page_number}_reliability")
        if localization_path is None:
            return EnginePageResult(
                page_number=page_number,
                status="failed",
                error_code="engine_invalid_response",
                error_detail="The TruFor worker response did not contain a usable localization map.",
                diagnostics=_safe_dict(payload.get("diagnostics")),
            )
        return EnginePageResult(
            page_number=page_number,
            status=str(payload.get("status", "completed")),
            integrity_score=_optional_float(payload.get("integrity_score")),
            localization_map_path=localization_path,
            reliability_map_path=reliability_path,
            map_width_px=_optional_int(payload.get("map_width_px")),
            map_height_px=_optional_int(payload.get("map_height_px")),
            regions=_safe_regions(payload.get("regions")),
            diagnostics=_safe_dict(payload.get("diagnostics")),
        )


def _save_map_payload(payload: dict, key: str, directory: Path, stem: str) -> Path | None:
    encoded = payload.get(key)
    if not isinstance(encoded, str) or not encoded or len(encoded) > 64_000_000:
        return None
    import base64
    import cv2
    import numpy as np

    try:
        decoded = base64.b64decode(encoded, validate=True)
        if len(decoded) > 48_000_000 or not decoded.startswith(bytes((137, 80, 78, 71, 13, 10, 26, 10))):
            return None
        if len(decoded) < 24 or decoded[12:16] != b"IHDR":
            return None
        width, height = struct.unpack(">II", decoded[16:24])
        if width < 1 or height < 1 or width * height > 20_000_000:
            return None
        image = cv2.imdecode(np.frombuffer(decoded, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
        if image is None or image.shape[1] != width or image.shape[0] != height:
            return None
    except Exception:
        return None
    path = directory / f"{stem}.png"
    path.write_bytes(decoded)
    return path
def _optional_float(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return None
    return float(value)


def _optional_int(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return int(value)


def _safe_dict(value) -> dict:
    if isinstance(value, dict):
        try:
            json.dumps(value)
            return value
        except TypeError:
            return {}
    return {}


def _safe_regions(value) -> list[dict]:
    """Keep only well-formed candidate regions produced by the worker's versioned post-processing."""
    regions = []
    if not isinstance(value, list):
        return regions
    for item in value:
        if not isinstance(item, dict):
            continue
        bbox = item.get("bbox_pixel")
        if not isinstance(bbox, dict) or any(k not in bbox for k in ("x0", "y0", "x1", "y1")):
            continue
        try:
            box = {k: float(bbox[k]) for k in ("x0", "y0", "x1", "y1")}
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(value) for value in box.values()) or box["x1"] <= box["x0"] or box["y1"] <= box["y0"]:
            continue
        regions.append({
            "bbox_pixel": box,
            "mean_localization_score": _optional_float(item.get("mean_localization_score")),
            "max_localization_score": _optional_float(item.get("max_localization_score")),
            "mean_reliability_value": _optional_float(item.get("mean_reliability_value")),
            "postprocess_version": str(item.get("postprocess_version", "worker_unspecified")),
        })
    return regions


def configured_engine() -> VisualForensicsEngine | None:
    """Return the configured engine, or None when the feature flag is off."""
    if not settings.visual_forensics_enabled or not settings.trufor_license_cleared:
        return None
    return TruForEngine(
        worker_url=settings.trufor_worker_url,
        timeout_seconds=settings.trufor_job_timeout_seconds,
        model_version=settings.trufor_model_version,
        checkpoint_sha256=settings.trufor_checkpoint_sha256,
        device=settings.trufor_device,
    )


def engine_health_payload() -> dict:
    """Honest engine status for API consumers; never implies model output exists."""
    if not settings.visual_forensics_enabled:
        return {
            "engine": "trufor",
            "feature_enabled": False,
            "available": False,
            "reason": "Visual forensics engine is disabled (VISUAL_FORENSICS_ENABLED=false). Existing deterministic checks are unaffected.",
            "model_version": settings.trufor_model_version or None,
            "checkpoint_sha256": settings.trufor_checkpoint_sha256 or None,
            "device": settings.trufor_device,
        }
    if not settings.trufor_license_cleared:
        return {
            "engine": "trufor",
            "feature_enabled": False,
            "available": False,
            "reason": "TruFor inference is blocked until intended use and applicable model/checkpoint terms are cleared (TRUFOR_LICENSE_CLEARED=false).",
            "model_version": settings.trufor_model_version or None,
            "checkpoint_sha256": settings.trufor_checkpoint_sha256 or None,
            "device": settings.trufor_device,
        }
    engine = configured_engine()
    health = engine.health() if engine else None
    payload = health.to_dict() if health else {"engine": "trufor", "available": False, "reason": "Engine is not configured."}
    payload["feature_enabled"] = True
    return payload
