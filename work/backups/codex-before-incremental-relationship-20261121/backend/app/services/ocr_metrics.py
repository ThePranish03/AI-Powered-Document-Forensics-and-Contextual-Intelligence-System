"""OCR quality signals and evaluation metrics.

Confidence values are engine scores, not measured accuracy. Character and word
error rates are computed only when a non-empty ground-truth reference is supplied.
"""
from __future__ import annotations

import copy
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import re
import threading
import time
from collections import OrderedDict
from typing import Any

from app.services.deterministic_relationships import build_deterministic_relationship_candidates


def _edits(reference: list[str], hypothesis: list[str]) -> int:
    if not reference:
        return len(hypothesis)
    if not hypothesis:
        return len(reference)
    if len(hypothesis) > len(reference):
        reference, hypothesis = hypothesis, reference
    previous = list(range(len(hypothesis) + 1))
    for i, ref_token in enumerate(reference, start=1):
        current = [i]
        for j, hyp_token in enumerate(hypothesis, start=1):
            cost = 0 if ref_token == hyp_token else 1
            current.append(min(current[j - 1] + 1, previous[j] + 1, previous[j - 1] + cost))
        previous = current
    return previous[-1]


def _rate(reference: list[str], hypothesis: list[str]) -> dict[str, Any]:
    if not reference:
        return {
            "value": None,
            "edits": _edits(reference, hypothesis),
            "reference_length": 0,
            "status": "unavailable",
            "reason": "Ground truth is empty, so the error rate is undefined.",
        }
    edits = _edits(reference, hypothesis)
    return {
        "value": edits / len(reference),
        "edits": edits,
        "reference_length": len(reference),
        "status": "computed",
        "reason": None,
    }


def character_error_rate(reference: str, hypothesis: str) -> dict[str, Any]:
    return _rate(list(reference), list(hypothesis))


def word_error_rate(reference: str, hypothesis: str) -> dict[str, Any]:
    return _rate(reference.split(), hypothesis.split())


def exact_match(reference: str, hypothesis: str) -> bool:
    return reference == hypothesis


def _box(value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (list, tuple)):
        return None
    if len(value) == 4 and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value):
        x1, y1, x2, y2 = (float(item) for item in value)
    elif len(value) >= 4 and all(
        isinstance(point, (list, tuple)) and len(point) >= 2
        and all(isinstance(coordinate, (int, float)) and not isinstance(coordinate, bool) for coordinate in point[:2])
        for point in value
    ):
        xs = [float(point[0]) for point in value]
        ys = [float(point[1]) for point in value]
        x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
    else:
        return None
    if x2 < x1 or y2 < y1:
        return None
    return x1, y1, x2, y2


def _iou(left: tuple[float, float, float, float], right: tuple[float, float, float, float]) -> float:
    x1 = max(left[0], right[0])
    y1 = max(left[1], right[1])
    x2 = min(left[2], right[2])
    y2 = min(left[3], right[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    if intersection == 0:
        return 0.0
    area_left = (left[2] - left[0]) * (left[3] - left[1])
    area_right = (right[2] - right[0]) * (right[3] - right[1])
    union = area_left + area_right - intersection
    return intersection / union if union else 0.0


def detection_scores(ground_truth_boxes: list[Any] | None, predicted_boxes: list[Any] | None, iou_threshold: float = 0.5) -> dict[str, Any]:
    if not ground_truth_boxes and not predicted_boxes:
        return {"status": "unavailable", "reason": "No region annotations were supplied.", "precision": None, "recall": None, "f1": None}
    truth = [box for box in (ground_truth_boxes or []) if _box(box)]
    predicted = [box for box in (predicted_boxes or []) if _box(box)]
    if ground_truth_boxes and not truth:
        return {"status": "unavailable", "reason": "Region annotations were present but none were valid boxes.", "precision": None, "recall": None, "f1": None}
    if not truth:
        return {"status": "unavailable", "reason": "Detection metrics need ground-truth regions.", "precision": None, "recall": None, "f1": None}
    matched_truth: set[int] = set()
    true_positive = 0
    for pred in predicted:
        pred_box = _box(pred)
        if pred_box is None:
            continue
        best_index = None
        best_iou = iou_threshold
        for index, truth_box in enumerate(truth):
            if index in matched_truth:
                continue
            score = _iou(pred_box, _box(truth_box) or (0, 0, 0, 0))
            if score >= best_iou:
                best_iou = score
                best_index = index
        if best_index is not None:
            matched_truth.add(best_index)
            true_positive += 1
    false_positive = len(predicted) - true_positive
    false_negative = len(truth) - true_positive
    precision = true_positive / (true_positive + false_positive) if (true_positive + false_positive) else 0.0
    recall = true_positive / (true_positive + false_negative) if (true_positive + false_negative) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {
        "status": "computed",
        "reason": None,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "iou_threshold": iou_threshold,
    }


def assess_ocr_quality(blocks: list[dict], low_confidence_threshold: float = 0.55) -> dict[str, Any]:
    scored = [block for block in blocks if isinstance(block.get("engine_confidence"), (int, float))]
    if not scored:
        return {
            "status": "unavailable",
            "reason": "The OCR engine did not return per-region confidence.",
            "mean_confidence": None,
            "low_confidence_block_ids": [],
            "threshold": low_confidence_threshold,
            "accepted": None,
        }
    confidences = [float(block["engine_confidence"]) for block in scored]
    low = [block.get("block_id") for block in scored if float(block["engine_confidence"]) < low_confidence_threshold]
    mean = sum(confidences) / len(confidences)
    return {
        "status": "computed",
        "reason": "Mean engine confidence is a quality signal, not measured transcription accuracy.",
        "mean_confidence": mean,
        "low_confidence_block_ids": [item for item in low if item],
        "threshold": low_confidence_threshold,
        "accepted": mean >= low_confidence_threshold and not low,
    }


def evaluate_samples(samples: list[dict], iou_threshold: float = 0.5) -> dict[str, Any]:
    rows = []
    cer_values: list[float] = []
    wer_values: list[float] = []
    exact = 0
    detection_rows = []
    latencies: list[float] = []
    for index, sample in enumerate(samples):
        reference = str(sample.get("ground_truth") or "")
        hypothesis = str(sample.get("hypothesis") or "")
        cer = character_error_rate(reference, hypothesis)
        wer = word_error_rate(reference, hypothesis)
        matched = exact_match(reference, hypothesis)
        exact += int(matched)
        if cer["value"] is not None:
            cer_values.append(cer["value"])
        if wer["value"] is not None:
            wer_values.append(wer["value"])
        detection = detection_scores(sample.get("ground_truth_boxes"), sample.get("predicted_boxes"), iou_threshold)
        if detection["status"] == "computed":
            detection_rows.append(detection)
        latency_ms = sample.get("latency_ms")
        if isinstance(latency_ms, (int, float)) and not isinstance(latency_ms, bool) and math.isfinite(latency_ms) and latency_ms >= 0:
            latencies.append(float(latency_ms))
        rows.append({
            "id": str(sample.get("id") or f"sample-{index + 1}"),
            "document_id": sample.get("document_id"),
            "filename": sample.get("filename"),
            "provider": sample.get("provider"),
            "provider_version": sample.get("provider_version"),
            "extraction_run_id": sample.get("extraction_run_id"),
            "latency_ms": latency_ms if isinstance(latency_ms, (int, float)) and not isinstance(latency_ms, bool) else None,
            "document_type": sample.get("document_type"),
            "quality": sample.get("quality"),
            "exact_match": matched,
            "cer": cer,
            "wer": wer,
            "detection": detection,
            "ground_truth": reference,
            "hypothesis": hypothesis,
        })
    def _mean(values: list[float]) -> float | None:
        return sum(values) / len(values) if values else None
    detection_summary = {"status": "unavailable", "reason": "No sample included region ground truth.", "precision": None, "recall": None, "f1": None}
    if detection_rows:
        detection_summary = {
            "status": "computed",
            "reason": None,
            "precision": _mean([row["precision"] for row in detection_rows]),
            "recall": _mean([row["recall"] for row in detection_rows]),
            "f1": _mean([row["f1"] for row in detection_rows]),
            "samples_with_regions": len(detection_rows),
        }
    if latencies:
        ordered_latencies = sorted(latencies)
        p95_index = max(0, math.ceil(0.95 * len(ordered_latencies)) - 1)
        latency_summary = {
            "status": "computed",
            "reason": "Measured OCR engine latency from stored extraction runs; excludes upload and queue wait time.",
            "sample_count": len(latencies),
            "average_ms": _mean(latencies),
            "p95_ms": ordered_latencies[p95_index],
            "throughput_documents_per_second": len(latencies) / (sum(latencies) / 1000) if sum(latencies) > 0 else None,
        }
    else:
        latency_summary = {"status": "unavailable", "reason": "No measured OCR engine latency was supplied."}
    return {
        "sample_count": len(samples),
        "exact_match_accuracy": (exact / len(samples)) if samples else None,
        "cer_mean": _mean(cer_values),
        "wer_mean": _mean(wer_values),
        "cer_defined_samples": len(cer_values),
        "wer_defined_samples": len(wer_values),
        "detection": detection_summary,
        "latency": latency_summary,
        "resources": {"status": "unavailable", "reason": "CPU, RAM, and GPU measurements are not collected for supplied-text evaluation."},
        "samples": rows,
    }


_AMOUNT = r"((?:[0-9Oo]{1,3}(?:,[0-9Oo]{3})+|[0-9Oo]+)(?:\.[0-9Oo]{1,2})?)(?=\s|[.;:)\]\n]|(?:tax|gst|vat|invoice|payment|settlement|to|and)\b|$)"
_CURRENCY = r"(?:INR|USD|EUR|Rs\.?|₹|\$|€|[^\w\s0-9.,-]+)?\s*"
_LABELS = (
    ("subtotal", re.compile(rf"(?i)\bsub[\s-]*total\b\s*[:\-]?\s*{_CURRENCY}{_AMOUNT}")),
    ("tax", re.compile(rf"(?i)(?<![A-Za-z])(?:tax|gst|vat)\b\s*[:\-]?\s*{_CURRENCY}{_AMOUNT}")),
    ("transaction_amount", re.compile(rf"(?i)\bamount(?:\s*\([^)]*\))?\s*[:\-]?\s*{_CURRENCY}{_AMOUNT}")),
    ("total", re.compile(rf"(?i)\b(?:grand\s+total|total\s+amount|amount\s+due|total)\b\s*[:\-]?\s*{_CURRENCY}{_AMOUNT}")),
    ("purchase_order_value", re.compile(rf"(?i)\b(?:approved\s+)?purchase\s+order\s+value\b\s*(?:is\s*)?[:\-]?\s*{_CURRENCY}{_AMOUNT}")),
    ("invoice_amount", re.compile(rf"(?i)\binvoice\s+(?:amount|of)\b\s*[:\-]?\s*{_CURRENCY}{_AMOUNT}")),
    ("payment", re.compile(rf"(?i)\b(?:payment\s+of|payment\s+amount|paid)\b\s*[:\-]?\s*{_CURRENCY}{_AMOUNT}")),
    ("settlement", re.compile(rf"(?i)\bsettlement(?:\s+of|\s+amount)?\b\s*[:\-]?\s*{_CURRENCY}{_AMOUNT}")),
)
_IDENTIFIER = re.compile(r"(?i)\b(?P<label>purchase\s*order|invoice|transaction|reference|account|vendor)(?![a-z])(?:\s*(?:no|number|id|#))?\s*[:#\-]?\s*(?P<value>[A-Z0-9][A-Z0-9\-]{2,})")
_PREFIXED_IDENTIFIER = re.compile(r"(?i)\b(?P<prefix>INV|PO|TXN|REF)-(?P<suffix>[A-Z0-9][A-Z0-9\-]{2,})\b")
_IDENTIFIER_STOP_WORDS = {"total", "subtotal", "approval", "value", "should", "reference", "holder", "matching", "amount", "invoice", "order", "date", "ledger", "code", "onboarding", "master", "form"}
_DOCUMENT_ID_FIELD = re.compile(r"(?i)\bDocument\s+ID\s*[:#]\s*([A-Z0-9][A-Z0-9-]{2,})")
_CASE_LINK_FIELD = re.compile(r"(?i)\bCase\s+Link\s*[:#]\s*([A-Z0-9][A-Z0-9-]{2,})")
_VENDOR_IDENTIFIER = re.compile(r"(?i)\bVND-[A-Z0-9][A-Z0-9-]*\b")
_VESSEL_IDENTIFIER = re.compile(r"(?i)\b(?:Vessel\s+)?IMO\s*[:#]?\s*(\d{7})\b")
_ACCOUNT_NUMBER = re.compile(r"(?i)\b(?:corporate\s+bank\s+account|bank\s+account|beneficiary\s+account|account)\b[^\n]{0,120}?#\s*([A-Z0-9][A-Z0-9-]{3,})")
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_SLASH_DATE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b")
_MONTH_DATE = re.compile(r"(?i)\b(?P<day>\d{1,2})(?:st|nd|rd|th)?\s+(?P<month>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+(?P<year>\d{4})(?!\d)")
_MONTHS = {name.lower(): index for index, names in enumerate((
    ("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
    ("may",), ("jun", "june"), ("jul", "july"), ("aug", "august"),
    ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"), ("dec", "december"),
), start=1) for name in names}
_OBSERVED_FIELD_PATTERNS = (
    ("organization", re.compile(r"(?i)\b(?:company|employer|supplier|vendor|bill\s+to)\s*[:\-]\s*([^|\n]+)")),
    ("account_holder", re.compile(r"(?i)\baccount\s+holder\s*[:\-]\s*([^|\n]+)")),
    ("vendor_id", re.compile(r"(?i)\bvendor\s+id\s*[:#\-]?\s*([^|\n]+)")),
    ("person_name", re.compile(r"(?i)\b(?:employee\s+name|employee|name)\s*[:\-]\s*([^|\n]+)")),
    ("address", re.compile(r"(?i)\baddress\s*[:\-]\s*([^|\n]+)")),
    ("account_number", re.compile(r"(?i)\b(?:bank\s+account|account\s*(?:no|number|#))\s*[:#\-]?\s*([0-9][0-9 -]{3,})")),
    ("salary", re.compile(r"(?i)\b(?:gross\s+salary|net\s+salary|salary|gross\s+pay|net\s+pay)\s*[:\-]?\s*([^\s|]+)")),
    ("date", re.compile(r"(?i)\b(?:invoice\s+date|date|statement\s+period|period)\s*[:\-]\s*([^|\n]+)")),
)


def _money(value: str) -> tuple[float, list[str]]:
    normalized = value.replace(",", "")
    warnings: list[str] = []
    if re.fullmatch(r"[0-9Oo]+(?:\.[0-9Oo]{1,2})?", normalized):
        if re.search(r"[Oo]", normalized):
            normalized = re.sub(r"[Oo]", "0", normalized)
            warnings.append("OCR letter O was interpreted as zero; verify the original page.")
        return float(normalized), warnings
    raise ValueError("Amount contains unsupported characters")


def _valid_date(year: int, month: int, day: int) -> bool:
    if not (1 <= month <= 12 and 1 <= day <= 31 and 1900 <= year <= 2100):
        return False
    try:
        date(year, month, day)
    except ValueError:
        return False
    return True


def extract_document_facts(document: dict) -> dict[str, Any]:
    blocks = document.get("blocks") or []
    pieces = []
    identifiers = []
    dates = []
    labeled: dict[str, dict[str, Any]] = {}
    financial_amounts: list[dict[str, Any]] = []
    observed_fields: list[dict[str, Any]] = []
    structured_fields: list[dict[str, Any]] = []
    table_rows: list[dict[str, Any]] = []
    transaction_rows: list[dict[str, Any]] = []
    ownership_rows: list[dict[str, Any]] = []
    access_session_rows: list[dict[str, Any]] = []
    seen_identifiers: set[tuple[str, str]] = set()
    seen_observation_text: set[str] = set()
    for block in blocks:
        text = str(block.get("text") or "")
        if not text:
            continue
        logical_text = " ".join(text.casefold().split())
        if logical_text in seen_observation_text:
            continue
        seen_observation_text.add(logical_text)
        pieces.append(text)
        block_id = block.get("block_id")
        page = block.get("page_index")
        cells = block.get("cells")
        if isinstance(cells, list):
            row = {}
            row_cells = []
            for cell in cells:
                if not isinstance(cell, dict):
                    continue
                header = " ".join(str(cell.get("header") or "").casefold().split())
                raw_value = " ".join(str(cell.get("raw_text") or "").split())
                if not raw_value:
                    continue
                normalized_value = raw_value
                if header in {"beneficiary account", "account", "source ip", "ip address", "session id", "transaction id", "tx id", "vendor id", "vessel imo"}:
                    normalized_value = re.sub(r"\s*-\s*", "-", raw_value).upper()
                elif "@" in raw_value:
                    normalized_value = re.sub(r"\s*-\s*", "-", re.sub(r"\s+", "", raw_value)).casefold()
                row_cells.append({
                    "column_index": cell.get("column_index"),
                    "header": cell.get("header"),
                    "raw": raw_value,
                    "normalized": normalized_value,
                    "bbox": cell.get("bbox"),
                })
                if header:
                    row[header] = {"raw": raw_value, "normalized": normalized_value}
                identifier_types = {
                    "tx id": "TRANSACTION", "transaction id": "TRANSACTION", "vendor id": "VENDOR",
                    "session id": "SESSION", "vessel imo": "VESSEL", "beneficiary account": "ACCOUNT",
                    "account": "ACCOUNT", "source ip": "IP", "ip address": "IP",
                }
                entity_type = identifier_types.get(header)
                if entity_type:
                    value = normalized_value
                    key = (entity_type, value)
                    if key not in seen_identifiers:
                        seen_identifiers.add(key)
                        identifiers.append({"value": value, "entity_type": entity_type, "block_id": block_id, "page_index": page, "text": text})
                structured_fields.append({
                    "field_type": header.replace(" ", "_") or "table_column",
                    "raw": raw_value,
                    "normalized": normalized_value,
                    "block_id": block_id,
                    "page_index": page,
                    "text": text,
                    "table_index": block.get("table_index"),
                    "row_index": block.get("row_index"),
                    "column_index": cell.get("column_index"),
                    "table_structure_status": block.get("table_structure_status", "uncertain"),
                })
            table_rows.append({
                "block_id": block_id,
                "page_index": page,
                "table_index": block.get("table_index"),
                "row_index": block.get("row_index"),
                "structure_status": block.get("table_structure_status", "uncertain"),
                "cells": row_cells,
            })
            if "tx id" in row:
                transaction_rows.append({"fields": row, "block_id": block_id, "page_index": page})
            if "full name" in row:
                ownership_rows.append({"fields": row, "block_id": block_id, "page_index": page})
            if "session id" in row:
                access_session_rows.append({"fields": row, "block_id": block_id, "page_index": page})
        for field_type, pattern in (("document_id", _DOCUMENT_ID_FIELD), ("case_link", _CASE_LINK_FIELD)):
            match = pattern.search(text)
            if match:
                value = match.group(1).upper()
                identifier_type = "DOCUMENT_ID" if field_type == "document_id" else "CASE_LINK"
                structured_fields.append({
                    "field_type": field_type, "raw": match.group(1), "normalized": value,
                    "block_id": block_id, "page_index": page, "text": text,
                })
                key = (identifier_type, value)
                if key not in seen_identifiers:
                    seen_identifiers.add(key)
                    identifiers.append({"value": value, "entity_type": identifier_type, "block_id": block_id, "page_index": page, "text": text})
        for label, pattern in _LABELS:
            match = pattern.search(text)
            if match:
                try:
                    amount_value, warnings = _money(match.group(1))
                except ValueError:
                    continue
                field = {
                    "field_type": label,
                    "value": amount_value,
                    "raw": match.group(1),
                    "normalization_warnings": warnings,
                    "block_id": block_id,
                    "page_index": page,
                    "text": text,
                }
                financial_amounts.append(field)
                labeled.setdefault(label, field)
        for field_type, pattern in _OBSERVED_FIELD_PATTERNS:
            for match in pattern.finditer(text):
                observed_fields.append({
                    "field_type": field_type,
                    "raw": match.group(1).strip(),
                    "block_id": block_id,
                    "page_index": page,
                    "text": text,
                })
        for match in _IDENTIFIER.finditer(text):
            value = match.group("value").upper()
            if value.rstrip("-").casefold() in _IDENTIFIER_STOP_WORDS:
                continue
            label = re.sub(r"\s+", " ", match.group("label").casefold())
            entity_type = ("INVOICE" if label in {"invoice", "inv"} else
                           "PURCHASE_ORDER" if label in {"purchase order", "po"} else
                           "TRANSACTION" if label in {"transaction", "txn", "tx", "tx id"} else
                           "ACCOUNT" if label == "account" else
                           "VENDOR" if label == "vendor" else "REFERENCE")
            if entity_type == "ACCOUNT" and not re.search(r"\d", value):
                continue
            key = (entity_type, value)
            if key in seen_identifiers:
                continue
            seen_identifiers.add(key)
            identifiers.append({"value": value, "entity_type": entity_type, "block_id": block_id, "page_index": page, "text": text})
        for match in _PREFIXED_IDENTIFIER.finditer(text):
            prefix = match.group("prefix").upper()
            value = f"{prefix}-{match.group('suffix').upper()}"
            if any(item["value"] == value and item["entity_type"] in {"CASE_LINK", "DOCUMENT_ID"} for item in identifiers):
                continue
            entity_type = {"INV": "INVOICE", "PO": "PURCHASE_ORDER", "TXN": "TRANSACTION", "REF": "REFERENCE"}[prefix]
            key = (entity_type, value)
            if key in seen_identifiers:
                continue
            seen_identifiers.add(key)
            identifiers.append({"value": value, "entity_type": entity_type, "block_id": block_id, "page_index": page, "text": text})
        for entity_type, pattern in (("VENDOR", _VENDOR_IDENTIFIER), ("VESSEL", _VESSEL_IDENTIFIER)):
            for match in pattern.finditer(text):
                raw_value = match.group(0) if entity_type == "VENDOR" else match.group(1)
                value = raw_value.upper()
                key = (entity_type, value)
                if key in seen_identifiers:
                    continue
                seen_identifiers.add(key)
                identifiers.append({"value": value, "entity_type": entity_type, "block_id": block_id, "page_index": page, "text": text})
        for match in _ACCOUNT_NUMBER.finditer(text):
            value = re.sub(r"\s*-\s*", "-", match.group(1)).upper()
            key = ("ACCOUNT", value)
            if key not in seen_identifiers:
                seen_identifiers.add(key)
                identifiers.append({"value": value, "entity_type": "ACCOUNT", "block_id": block_id, "page_index": page, "text": text})
        for match in _ISO_DATE.finditer(text):
            year, month, day = (int(match.group(i)) for i in (1, 2, 3))
            if _valid_date(year, month, day):
                dates.append({"raw": match.group(0), "interpreted": f"{year:04d}-{month:02d}-{day:02d}", "ambiguous": False, "block_id": block_id, "page_index": page, "text": text})
        for match in _MONTH_DATE.finditer(text):
            year, day = int(match.group("year")), int(match.group("day"))
            month = _MONTHS.get(match.group("month").casefold())
            if month and _valid_date(year, month, day):
                dates.append({"raw": match.group(0), "interpreted": f"{year:04d}-{month:02d}-{day:02d}", "ambiguous": False, "block_id": block_id, "page_index": page, "text": text})
        for match in _SLASH_DATE.finditer(text):
            first, second, year_raw = int(match.group(1)), int(match.group(2)), int(match.group(3))
            year = year_raw + 2000 if year_raw < 100 else year_raw
            ambiguous = first <= 12 and second <= 12
            interpreted = None
            if not ambiguous and _valid_date(year, second if first > 12 else first, first if first > 12 else second):
                month, day = (second, first) if first > 12 else (first, second)
                interpreted = f"{year:04d}-{month:02d}-{day:02d}"
            elif not ambiguous:
                continue
            dates.append({"raw": match.group(0), "interpreted": interpreted, "ambiguous": ambiguous, "block_id": block_id, "page_index": page, "text": text})
    text = "\n".join(pieces) if pieces else str(document.get("text") or "")
    quality = assess_ocr_quality(blocks)
    return {
        "document_id": document["document_id"],
        "filename": document.get("filename") or document["document_id"],
        "sha256": document.get("sha256"),
        "provider": document.get("provider"),
        "provider_version": document.get("provider_version"),
        "processing_status": document.get("processing_status"),
        "text": text,
        "identifiers": identifiers,
        "labeled_amounts": labeled,
        "financial_amounts": financial_amounts,
        "observed_fields": observed_fields,
        "structured_fields": structured_fields,
        "table_rows": table_rows,
        "transaction_rows": transaction_rows,
        "ownership_rows": ownership_rows,
        "access_session_rows": access_session_rows,
        "dates": dates,
        "quality": quality,
        "structured_entities": (document.get("structured_extraction") or {}).get("entities", []),
        "structured_relationships": (document.get("structured_extraction") or {}).get("relationships", []),
        "source_blocks": blocks,
        "empty": not text.strip(),
    }


def _ref(document: dict, **extra: Any) -> dict[str, Any]:
    page_index = extra.pop("page_index", None)
    page_number = page_index + 1 if isinstance(page_index, int) and not isinstance(page_index, bool) else page_index
    return {
        "document_id": document["document_id"],
        "filename": document["filename"],
        "source_sha256": document.get("sha256"),
        "page_number": page_number,
        **extra,
    }


def _analysis_diagnostics(facts: list[dict[str, Any]], findings: list[dict], timeline: list[dict], by_hash: dict, by_identifier: dict) -> dict[str, Any]:
    finding_counts: dict[str, int] = {}
    for finding in findings:
        kind = finding["finding_type"]
        finding_counts[kind] = finding_counts.get(kind, 0) + 1

    def rule(rule_id: str, description: str, inspected: list[str], matched: int, comparisons: list[dict], skipped: list[dict]) -> dict[str, Any]:
        status = "matched" if matched else "not_matched" if comparisons else (
            "error" if any(item.get("status") == "error" for item in skipped) else
            "extraction_incomplete" if any(item.get("status") == "extraction_incomplete" for item in skipped) else
            "missing_input" if any(item.get("status") == "missing_input" for item in skipped) else
            "not_matched" if any(item.get("status") == "not_matched" for item in skipped) else
            "not_applicable"
        )
        return {
            "rule_id": rule_id,
            "description": description,
            "status": status,
            "executed": True,
            "documents_inspected": inspected,
            "fields_extracted": [],
            "comparisons_performed": comparisons,
            "rules_matched": matched,
            "rules_skipped": skipped,
        }

    inspected = [fact["document_id"] for fact in facts]
    reconciliation_comparisons = []
    reconciliation_skipped = []
    for fact in facts:
        amounts = fact["labeled_amounts"]
        required = ("subtotal", "tax", "total")
        missing = [label for label in required if label not in amounts]
        if missing:
            extraction_failed_without_ocr = (
                fact.get("processing_status") == "failed"
                and not fact.get("provider")
                and not fact.get("source_blocks")
                and not fact.get("text")
            )
            skip_status = (
                "error" if extraction_failed_without_ocr else
                "not_applicable" if not amounts and fact.get("text") else
                "missing_input"
            )
            reconciliation_skipped.append({
                "document_id": fact["document_id"],
                "reason": "required_labeled_amounts_missing",
                "missing_fields": missing,
                "status": skip_status,
            })
        else:
            expected = amounts["subtotal"]["value"] + amounts["tax"]["value"]
            observed = amounts["total"]["value"]
            reconciliation_comparisons.append({
                "document_id": fact["document_id"],
                "fields": {label: {"raw": amounts[label]["raw"], "normalized": amounts[label]["value"]} for label in required},
                "expected_total": round(expected, 2),
                "observed_total": round(observed, 2),
                "difference": round(observed - expected, 2),
                "matched": abs(expected - observed) > 0.02,
            })

    extraction_skipped = [
        {"document_id": fact["document_id"], "reason": "recognized_text_present", "status": "not_matched"}
        for fact in facts if not (fact["empty"] and fact.get("provider"))
    ]
    confidence_skipped = []
    confidence_comparisons = []
    for fact in facts:
        quality = fact["quality"]
        if quality["status"] != "computed":
            confidence_skipped.append({"document_id": fact["document_id"], "reason": "engine_confidence_unavailable", "status": "missing_input"})
        elif not quality["low_confidence_block_ids"]:
            confidence_skipped.append({"document_id": fact["document_id"], "reason": "no_block_below_threshold", "threshold": quality["threshold"], "status": "not_matched"})
        else:
            confidence_comparisons.append({
                "document_id": fact["document_id"],
                "threshold": quality["threshold"],
                "low_confidence_block_ids": quality["low_confidence_block_ids"],
            })

    duplicate_comparisons = []
    duplicate_skipped = []
    for digest, group in by_hash.items():
        comparison = {
            "sha256": digest,
            "document_ids": [fact["document_id"] for fact in group],
            "matched": len(group) > 1,
        }
        if len(group) > 1:
            duplicate_comparisons.append(comparison)
        else:
            duplicate_skipped.append({"document_id": group[0]["document_id"], "reason": "no_other_document_has_same_sha256", "status": "not_matched"})
    docs_with_hash = {fact["document_id"] for fact in facts if fact["sha256"]}
    duplicate_skipped.extend(
        {"document_id": fact["document_id"], "reason": "sha256_unavailable", "status": "missing_input"}
        for fact in facts if fact["document_id"] not in docs_with_hash
    )

    cross_comparisons = []
    cross_skipped = []
    for (entity_type, value), occurrences in by_identifier.items():
        distinct_docs: dict[str, tuple[dict, dict]] = {}
        for fact, identifier in occurrences:
            distinct_docs.setdefault(fact["document_id"], (fact, identifier))
        if len(distinct_docs) < 2:
            continue
        if entity_type not in {"INVOICE", "PURCHASE_ORDER", "TRANSACTION", "REFERENCE"}:
            cross_skipped.append({
                "identifier_type": entity_type,
                "identifier": value,
                "document_ids": list(distinct_docs),
                "reason": "identifier_type_not_used_to_compare_transaction_totals",
                "status": "not_applicable",
            })
            continue
        available = []
        missing_total_docs = []
        for fact, identifier in distinct_docs.values():
            total = fact["labeled_amounts"].get("total")
            if total is None:
                missing_total_docs.append(fact["document_id"])
            else:
                available.append({
                    "document_id": fact["document_id"],
                    "filename": fact["filename"],
                    "raw": total["raw"],
                    "normalized": total["value"],
                    "normalization_warnings": total.get("normalization_warnings", []),
                    "block_id": total["block_id"],
                    "page_index": total["page_index"],
                    "identifier_block_id": identifier["block_id"],
                })
        if len(available) < 2:
            cross_skipped.append({
                "identifier_type": entity_type,
                "identifier": value,
                "document_ids": list(distinct_docs),
                "reason": "fewer_than_two_documents_have_labeled_totals",
                "documents_without_total": missing_total_docs,
                "status": "missing_input",
            })
            continue
        normalized_values = sorted({round(item["normalized"], 2) for item in available})
        cross_comparisons.append({
            "identifier_type": entity_type,
            "identifier": value,
            "documents": available,
            "documents_without_total": missing_total_docs,
            "distinct_normalized_totals": normalized_values,
            "matched": len(normalized_values) > 1,
        })
        if len(normalized_values) == 1:
            cross_skipped.append({
                "identifier_type": entity_type,
                "identifier": value,
                "document_ids": [item["document_id"] for item in available],
                "reason": "labeled_totals_equal",
                "status": "not_matched",
            })

    date_skipped = [
        {"document_id": fact["document_id"], "reason": "no_supported_date_string", "status": "extraction_incomplete" if not fact.get("text") else "missing_input"}
        for fact in facts if not fact["dates"]
    ]
    shared_ip_comparisons = []
    shared_ip_skipped = []
    for fact in facts:
        ip_users: dict[str, set[str]] = {}
        for session in fact.get("access_session_rows", []):
            fields = session.get("fields", {})
            ip = fields.get("source ip", {}).get("normalized")
            account = fields.get("user account", {}).get("normalized")
            if ip and account:
                ip_users.setdefault(ip, set()).add(account)
        if not ip_users:
            shared_ip_skipped.append({"document_id": fact["document_id"], "reason": "access_session_table_missing", "status": "missing_input"})
        for ip, users in ip_users.items():
            shared_ip_comparisons.append({"document_id": fact["document_id"], "ip_address": ip, "distinct_user_accounts": sorted(users), "matched": len(users) > 1})
    diagnostic_rules = [
        rule("subtotal_plus_tax_vs_total", "Requires subtotal, tax, and total from the same document; flags an absolute difference greater than 0.02.", inspected, finding_counts.get("reconciliation", 0), reconciliation_comparisons, reconciliation_skipped),
        rule("empty_ocr_text", "Requires an OCR provider and empty recognized text.", inspected, finding_counts.get("extraction_gap", 0), [], extraction_skipped),
        rule("low_confidence_regions", "Requires engine confidence scores and at least one block below 0.55.", inspected, finding_counts.get("ocr_quality", 0), confidence_comparisons, confidence_skipped),
        rule("duplicate_file_bytes", "Requires the same recorded SHA-256 on at least two documents.", inspected, finding_counts.get("duplicate_bytes", 0), duplicate_comparisons, duplicate_skipped),
        rule("shared_identifier_total_conflict", "Requires the same typed identifier in at least two documents, at least two parsed labeled totals, and unequal normalized values.", inspected, finding_counts.get("identifier_amount_conflict", 0), cross_comparisons, cross_skipped),
        rule("date_extraction", "Extracts valid ISO, slash-form, and named-month dates into timeline events; this rule does not create findings.", inspected, len(timeline), [], date_skipped),
        rule("shared_access_ip_accounts", "Surfaces an IP address used by at least two distinct user accounts in a source access log; shared gateways are a possible explanation.", inspected, finding_counts.get("shared_access_ip", 0), shared_ip_comparisons, shared_ip_skipped),
    ]
    diagnostic_fields = {
        "subtotal_plus_tax_vs_total": lambda fact: [item for item in fact["financial_amounts"] if item["field_type"] in {"subtotal", "tax", "total"}],
        "empty_ocr_text": lambda fact: [{"recognized_text_characters": len(fact["text"]), "provider_present": bool(fact.get("provider"))}],
        "low_confidence_regions": lambda fact: [fact["quality"]],
        "duplicate_file_bytes": lambda fact: [{"sha256": fact.get("sha256")}],
        "shared_identifier_total_conflict": lambda fact: [{"identifiers": fact["identifiers"], "labeled_total": fact["labeled_amounts"].get("total")}],
        "date_extraction": lambda fact: fact["dates"],
        "shared_access_ip_accounts": lambda fact: fact["access_session_rows"],
    }
    for item in diagnostic_rules:
        item["fields_extracted"] = [{
            "document_id": fact["document_id"],
            "fields": diagnostic_fields[item["rule_id"]](fact),
        } for fact in facts]
    return {
        "summary": {
            "documents_inspected": len(facts),
            "findings_produced": len(findings),
            "timeline_events_produced": len(timeline),
            "graph_nodes_produced": len(by_identifier),
            "findings_by_type": finding_counts,
        },
        "data_flow": {
            "source": "Documents join to the latest OCRResult by document_id; native PDF text and OCR blocks preserve separate methods, page indexes, confidence, and bounding boxes.",
            "structured_extraction_used_for_findings": True,
            "structured_extraction_note": "Grounded relationship assertions are included as cited supporting context when a deterministic finding references the same source block. Assertions do not independently create findings or determine their outcome.",
        },
        "documents": [{
            "document_id": fact["document_id"],
            "filename": fact["filename"],
            "ocr_text_characters": len(fact["text"]),
            "identifiers_extracted": fact["identifiers"],
            "financial_fields_extracted": [{
                "field_type": item["field_type"],
                "raw": item["raw"],
                "normalized": item["value"],
                "normalization_warnings": item.get("normalization_warnings", []),
                "block_id": item["block_id"],
                "page_number": item["page_index"] + 1 if isinstance(item.get("page_index"), int) else item.get("page_index"),
            } for item in fact["financial_amounts"]],
            "observed_labeled_fields": fact["observed_fields"],
            "structured_fields": fact["structured_fields"],
            "table_rows": fact["table_rows"],
            "transaction_rows": fact["transaction_rows"],
            "ownership_rows": fact["ownership_rows"],
            "access_session_rows": fact["access_session_rows"],
            "dates_extracted": fact["dates"],
            "ocr_quality": fact["quality"],
            "structured_entities": [{
                "entity_type": item.get("entity_type"),
                "mention_text": item.get("mention_text"),
                "source_block_ids": item.get("source_block_ids", []),
            } for item in fact["structured_entities"] if isinstance(item, dict)],
        } for fact in facts],
        "rules": diagnostic_rules,
    }


def analyze_documents(documents: list[dict]) -> dict[str, Any]:
    facts = [extract_document_facts(document) for document in documents]
    findings: list[dict[str, Any]] = []
    timeline: list[dict[str, Any]] = []
    nodes: dict[str, dict[str, Any]] = {}
    limitation = "This is a candidate signal from deterministic rules over OCR text. It is not a determination of fraud, authenticity, or intent."

    by_hash: dict[str, list[dict]] = {}
    by_identifier: dict[tuple[str, str], list[tuple[dict, dict]]] = {}
    for fact in facts:
        if fact["sha256"]:
            by_hash.setdefault(fact["sha256"], []).append(fact)
        for identifier in fact["identifiers"]:
            by_identifier.setdefault((identifier["entity_type"], identifier["value"]), []).append((fact, identifier))
        amounts = fact["labeled_amounts"]
        if {"subtotal", "tax", "total"} <= set(amounts):
            expected = amounts["subtotal"]["value"] + amounts["tax"]["value"]
            observed = amounts["total"]["value"]
            if abs(expected - observed) > 0.02:
                findings.append({
                    "finding_type": "reconciliation",
                    "title": f"Labeled total does not reconcile in {fact['filename']}",
                    "description": f"Subtotal {amounts['subtotal']['raw']} plus tax {amounts['tax']['raw']} is {expected:.2f}, while the labeled total is {amounts['total']['raw']}.",
                    "severity": "medium",
                    "confidence": 0.74,
                    "method": "subtotal_plus_tax_vs_total",
                    "fingerprint": f"reconcile:{fact['document_id']}:{amounts['total']['raw']}",
                    "evidence_refs": [_ref(fact, block_id=amounts[name]["block_id"], page_index=amounts[name]["page_index"], field_type=name, quoted_text=amounts[name]["text"]) for name in ("subtotal", "tax", "total")],
                    "limitations": limitation + " OCR may have misread a digit, and unlabeled adjustments are not modeled.",
                })
        quality = fact["quality"]
        if fact["empty"] and fact.get("provider"):
            findings.append({
                "finding_type": "extraction_gap",
                "title": f"No recognized text in {fact['filename']}",
                "description": "The stored OCR result has no text. The page may be blank, unsupported, or the engine may have failed to read it.",
                "severity": "low",
                "confidence": 0.9,
                "method": "empty_ocr_text",
                "fingerprint": f"empty:{fact['document_id']}",
                "evidence_refs": [_ref(fact, quoted_text="")],
                "limitations": "Absence of text is an extraction gap, not evidence that the source page is blank or altered.",
            })
        elif quality["status"] == "computed" and quality["low_confidence_block_ids"]:
            findings.append({
                "finding_type": "ocr_quality",
                "title": f"Low-confidence OCR regions in {fact['filename']}",
                "description": f"{len(quality['low_confidence_block_ids'])} region(s) are below the engine-confidence threshold of {quality['threshold']}. Mean confidence is {quality['mean_confidence']:.2f}.",
                "severity": "low",
                "confidence": 0.66,
                "method": "engine_confidence_threshold",
                "fingerprint": f"ocr-quality:{fact['document_id']}",
                "evidence_refs": [_ref(fact, block_id=block_id, quoted_text="low engine confidence") for block_id in quality["low_confidence_block_ids"][:12]],
                "limitations": "Engine confidence is not calibrated accuracy and does not indicate manipulation.",
            })
        for date in fact["dates"]:
            timeline.append({
                "raw_date": date["raw"],
                "interpreted_date": date["interpreted"],
                "ambiguous": date["ambiguous"],
                "timezone_known": False,
                "event_type": "extracted_date",
                "description": f"Date string “{date['raw']}” was read from {fact['filename']}. No timezone was present, so none was assumed.",
                "confidence": 0.45 if date["ambiguous"] else 0.7,
                "evidence_refs": [_ref(fact, block_id=date["block_id"], page_index=date["page_index"], quoted_text=date["text"])],
            })
        sessions_by_ip: dict[str, list[dict[str, Any]]] = {}
        for session in fact.get("access_session_rows", []):
            fields = session.get("fields", {})
            ip = fields.get("source ip", {}).get("normalized")
            account = fields.get("user account", {}).get("normalized")
            if ip and account:
                sessions_by_ip.setdefault(ip, []).append(session)
        for ip, sessions in sessions_by_ip.items():
            accounts = {session["fields"]["user account"]["normalized"] for session in sessions}
            if len(accounts) < 2:
                continue
            evidence_refs = []
            for session in sessions:
                fields = session["fields"]
                source_blocks = {str(block.get("block_id")): block for block in fact.get("source_blocks", []) if isinstance(block, dict)}
                mention_map = {str(item.get("mention_id")): item for item in fact.get("structured_entities", []) if isinstance(item, dict)}
                grounded_relationships = []
                for assertion in fact.get("structured_relationships", []):
                    if not isinstance(assertion, dict):
                        continue
                    cited_blocks = [str(value) for value in assertion.get("source_block_ids", []) if str(value) in source_blocks]
                    if session["block_id"] not in cited_blocks:
                        continue
                    subject = mention_map.get(str(assertion.get("subject_mention_id")), {})
                    obj = mention_map.get(str(assertion.get("object_mention_id")), {})
                    citation_text = " ".join(" ".join(str(source_blocks[block_id].get("text") or "").casefold().split()) for block_id in cited_blocks)
                    grounded_mentions = [str(value.get("mention_text") or "").casefold().strip() for value in (subject, obj)]
                    if not all(value and value in citation_text for value in grounded_mentions):
                        continue
                    grounded_relationships.append({
                        "predicate": assertion.get("predicate"),
                        "relationship_kind": assertion.get("relationship_kind"),
                        "subject": subject.get("mention_text"),
                        "object": obj.get("mention_text"),
                        "source_refs": [_ref(fact, block_id=block_id, page_index=source_blocks[block_id].get("page_index"), quoted_text=source_blocks[block_id].get("text")) for block_id in cited_blocks],
                    })
                evidence_refs.append(_ref(
                    fact,
                    block_id=session["block_id"],
                    page_index=session["page_index"],
                    field_type="shared_source_ip",
                    normalized_ip=ip,
                    session_id=fields.get("session id", {}).get("normalized"),
                    user_account=fields.get("user account", {}).get("raw"),
                    grounded_relationships=grounded_relationships,
                    quoted_text="; ".join(f"{key}: {value['raw']}" for key, value in fields.items()),
                ))
            findings.append({
                "finding_type": "shared_access_ip",
                "title": f"Source IP {ip} appears in multiple access accounts",
                "description": f"The access log records {ip} for {len(accounts)} distinct user accounts. Shared infrastructure or a common VPN may explain the reuse; this is a review lead only.",
                "severity": "low",
                "confidence": 0.4,
                "method": "shared_access_ip_accounts",
                "fingerprint": f"shared-access-ip:{fact['document_id']}:{ip}",
                "evidence_refs": evidence_refs,
                "limitations": "An IP address may be shared by a VPN, proxy, gateway, or network address translation. The observation does not identify a person or establish unauthorized access.",
            })
    for digest, group in by_hash.items():
        if len(group) < 2:
            continue
        names = ", ".join(item["filename"] for item in group)
        findings.append({
            "finding_type": "duplicate_bytes",
            "title": "Identical file bytes uploaded more than once",
            "description": f"These documents share SHA-256 {digest}: {names}.",
            "severity": "low",
            "confidence": 1.0,
            "method": "sha256_equality",
            "fingerprint": f"duplicate:{digest}",
            "evidence_refs": [_ref(item, quoted_text=item["sha256"]) for item in group],
            "limitations": "Identical bytes mean the stored files match. They do not explain why the file was uploaded again.",
        })
    for (entity_type, value), occurrences in by_identifier.items():
        docs = []
        for fact, identifier in occurrences:
            if fact["document_id"] not in {item["document_id"] for item in docs}:
                docs.append(fact)
        node_id = f"det:identifier:{entity_type.lower()}:{value}"
        nodes[node_id] = {
            "canonical_id": node_id,
            "label": value,
            "entity_type": entity_type,
            "document_ids": [fact["document_id"] for fact, _identifier in occurrences],
            "resolution": "identifier_match" if len(docs) > 1 else "candidate",
            "basis": "Explicit identifier text matched after case-folding. A single occurrence stays a candidate link, not a confirmed party.",
        }
        if entity_type not in {"INVOICE", "PURCHASE_ORDER", "TRANSACTION", "REFERENCE"}:
            continue
        totals = []
        for fact, identifier in occurrences:
            total = fact["labeled_amounts"].get("total")
            if total:
                totals.append((fact, identifier, total))
        distinct = {round(item[2]["value"], 2) for item in totals}
        if len(docs) > 1 and len(distinct) > 1:
            normalized_totals = [
                f"{fact['filename']}: {total['raw']} => {total['value']:.2f}"
                + (" (OCR-confusable character normalization)" if total.get("normalization_warnings") else "")
                for fact, _identifier, total in totals
            ]
            findings.append({
                "finding_type": "identifier_amount_conflict",
                "title": f"Identifier {value} has conflicting labeled totals",
                "description": "The same typed identifier is paired with different OCR-extracted labeled totals: " + "; ".join(normalized_totals) + ". This is a candidate discrepancy to review, not proof that either document is false.",
                "severity": "medium" if any(total.get("normalization_warnings") for _, _, total in totals) else "high",
                "confidence": 0.45 if any(total.get("normalization_warnings") for _, _, total in totals) else 0.7,
                "method": "shared_identifier_total_conflict",
                "fingerprint": f"conflict:{value}",
                "evidence_refs": [
                    _ref(fact, block_id=total["block_id"], page_index=total["page_index"], quoted_text=total["text"], field_type="labeled_total")
                    | {"identifier": value, "identifier_block_id": identifier["block_id"], "related_total": total["raw"], "normalized_total": total["value"], "normalization_warnings": total.get("normalization_warnings", [])}
                    for fact, identifier, total in totals
                ],
                "limitations": limitation + " The identifier match is textual, and OCR-confusable characters were normalized only to surface a low-confidence candidate. Verify the unmodified source before comparing financial values.",
            })
    # Include exact, grounded canonical names in the persisted review graph.
    grounded_groups: dict[tuple[str, str], set[str]] = {}
    generic_mentions = {"document", "discrepancy", "onboarding", "code", "invoice", "ledger", "unknown"}
    for fact in facts:
        block_map = {str(block.get("block_id")): str(block.get("text") or "") for block in fact.get("source_blocks", []) if isinstance(block, dict)}
        for mention in fact.get("structured_entities", []):
            if not isinstance(mention, dict):
                continue
            raw_name = str(mention.get("mention_text") or "").strip()
            entity_type = str(mention.get("entity_type") or "OTHER").upper()
            normalized = " ".join(raw_name.casefold().split()).strip(" .,:;()[]{}")
            if len(normalized) < 3 or normalized in generic_mentions or entity_type in {"DOCUMENT", "OTHER"}:
                continue
            cited = [str(value) for value in mention.get("source_block_ids", []) if str(value) in block_map]
            if not cited or not any(normalized in " ".join(block_map[value].casefold().split()) for value in cited):
                continue
            grounded_groups.setdefault((entity_type, normalized), set()).add(fact["document_id"])
    for (entity_type, normalized), document_ids in grounded_groups.items():
        if len(document_ids) < 2:
            continue
        canonical_id = "entity:" + hashlib.sha256(f"{entity_type}|{normalized}".encode("utf-8")).hexdigest()[:24]
        nodes[canonical_id] = {
            "canonical_id": canonical_id,
            "label": normalized,
            "entity_type": entity_type,
            "document_ids": sorted(document_ids),
            "resolution": "exact_match",
            "basis": "Exact typed entity text is grounded in source blocks from multiple documents; link remains a review candidate.",
            "candidate_kind": "grounded_entity",
        }
    diagnostics = _analysis_diagnostics(facts, findings, timeline, by_hash, by_identifier)
    return {"findings": findings, "timeline": timeline, "nodes": list(nodes.values()), "documents": facts, "diagnostics": diagnostics}


def _build_cross_document_correlation_uncached(documents: list[dict]) -> dict[str, Any]:
    """Build explainable investigation-scoped links from persisted OCR facts.

    Typed identifiers and exact, grounded canonical entity mentions create
    review candidates. Dates and amounts remain comparison fields and never
    create identity links. Scores are rule ranks, not probabilities.
    """
    analyzed = analyze_documents(documents)
    facts = analyzed["documents"]
    links: list[dict[str, Any]] = []
    comparisons: list[dict[str, Any]] = []
    for node in analyzed["nodes"]:
        if node.get("candidate_kind") == "grounded_entity":
            continue
        document_ids = list(dict.fromkeys(node["document_ids"]))
        if len(document_ids) < 2:
            continue
        value = node["label"]
        occurrences: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for fact in facts:
            if fact["document_id"] not in document_ids:
                continue
            occurrences.extend((fact, mention) for mention in fact["identifiers"] if mention["value"] == value and mention["entity_type"] == node["entity_type"])
        references = [
            _ref(fact, block_id=mention["block_id"], page_index=mention["page_index"],
                 field_type="document_identifier", normalized_value=value,
                 quoted_text=mention["text"])
            for fact, mention in occurrences
        ]
        relation_type = "shared_case_link" if node["entity_type"] == "CASE_LINK" else "shared_document_identifier"
        links.append({
            "id": node["canonical_id"],
            "relation_type": relation_type,
            "normalized_identifier": value,
            "identifier_type": node["entity_type"],
            "document_ids": document_ids,
            "document_count": len(document_ids),
            "rank": 1.0,
            "rank_kind": "exact_rule_match",
            "method": "exact_identifier_v1",
            "explanation": ("The same labeled case link appears in these source records. It associates them with a case but does not prove they describe the same transaction or party." if relation_type == "shared_case_link" else "The same labeled document identifier appears in these source records. This is a review lead and does not prove they describe the same transaction or party."),
            "evidence_kind": "explicit_source_identifier",
            "explicit_or_inferred": "explicit",
            "limitations": "Identifier equality supports a review lead; it does not establish a common party or transaction.",
            "evidence_refs": references,
            "review_status": "candidate",
        })
        entries = []
        distinct_totals: set[Decimal] = set()
        for fact in facts:
            if fact["document_id"] not in document_ids:
                continue
            amount = fact["labeled_amounts"].get("total")
            if amount:
                try:
                    distinct_totals.add(Decimal(str(amount["value"])).quantize(Decimal("0.01")))
                except (InvalidOperation, ValueError):
                    pass
            identifiers = [item for item in fact["identifiers"] if item["value"] == value and item["entity_type"] == node["entity_type"]]
            if not identifiers:
                continue
            entries.append({
                "document_id": fact["document_id"],
                "filename": fact["filename"],
                "total": ({
                    "raw": amount["raw"],
                    "value": amount["value"],
                    "evidence_ref": _ref(fact, block_id=amount["block_id"], page_index=amount["page_index"], field_type="total", quoted_text=amount["text"]),
                } if amount else None),
                "dates": [{
                    "raw": item["raw"], "interpreted": item["interpreted"], "ambiguous": item["ambiguous"],
                    "evidence_ref": _ref(fact, block_id=item["block_id"], page_index=item["page_index"], field_type="date", quoted_text=item["text"]),
                } for item in fact["dates"]],
            })
        has_conflict = len(distinct_totals) > 1
        comparisons.append({
            "id": node["canonical_id"],
            "field": "document_identifier",
            "value": value,
            "status": "value_conflict" if has_conflict else "matched_identifier",
            "documents": entries,
            "explanation": "The exact identifier is shared. Labeled totals differ across sources." if has_conflict else "The exact identifier is shared. Missing values are not treated as agreement.",
        })
    # Persisted relationship extraction contributes candidate entity links only
    # after source block references and the exact mention text are revalidated.
    entity_groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    generic_mentions = {"document", "discrepancy", "onboarding", "code", "invoice", "ledger", "unknown"}
    for fact in facts:
        block_map = {str(item.get("block_id")): str(item.get("text") or "") for item in fact.get("source_blocks", []) if isinstance(item, dict)}
        for mention in fact.get("structured_entities", []):
            if not isinstance(mention, dict):
                continue
            mention_text = str(mention.get("mention_text") or "").strip()
            entity_type = str(mention.get("entity_type") or "OTHER").upper()
            normalized = " ".join(mention_text.casefold().split()).strip(" .,:;()[]{}")
            if len(normalized) < 3 or normalized in generic_mentions or entity_type in {"DOCUMENT", "OTHER"}:
                continue
            source_ids = [str(value) for value in mention.get("source_block_ids", []) if str(value) in block_map]
            if not source_ids:
                continue
            mention_key = re.sub(r"\s+", " ", mention_text.casefold())
            grounded_ids = [block_id for block_id in source_ids if mention_key in re.sub(r"\s+", " ", block_map[block_id].casefold())]
            if not grounded_ids:
                continue
            group = entity_groups.setdefault((entity_type, normalized), [])
            group.append({"fact": fact, "mention": mention, "block_ids": grounded_ids, "raw": mention_text})
    for (entity_type, normalized), occurrences in entity_groups.items():
        doc_ids = list(dict.fromkeys(item["fact"]["document_id"] for item in occurrences))
        if len(doc_ids) < 2:
            continue
        canonical_id = "entity:" + hashlib.sha256(f"{entity_type}|{normalized}".encode("utf-8")).hexdigest()[:24]
        references = []
        relationship_assertions = []
        for item in occurrences:
            fact, mention = item["fact"], item["mention"]
            block_map = {str(block.get("block_id")): block for block in fact.get("source_blocks", []) if isinstance(block, dict)}
            for block_id in item["block_ids"]:
                block = block_map.get(block_id, {})
                references.append(_ref(
                    fact,
                    block_id=block_id,
                    page_index=block.get("page_index"),
                    quoted_text=block.get("text"),
                    raw_mention=item["raw"],
                    normalized_mention=normalized,
                    entity_type=entity_type,
                    extraction_method=block.get("extraction_method", "ocr"),
                    relationship_kind="grounded_mention",
                ))
            mentioned_ids = set(item["block_ids"])
            mentions_by_id = {
                str(value.get("mention_id")): value
                for value in fact.get("structured_entities", []) if isinstance(value, dict)
            }
            for assertion in fact.get("structured_relationships", []):
                if not isinstance(assertion, dict):
                    continue
                cited_ids = [str(value) for value in assertion.get("source_block_ids", []) if str(value) in block_map]
                if not mentioned_ids.intersection(cited_ids):
                    continue
                subject = mentions_by_id.get(str(assertion.get("subject_mention_id")), {})
                obj = mentions_by_id.get(str(assertion.get("object_mention_id")), {})
                relationship_assertions.append({
                    "predicate": assertion.get("predicate"),
                    "relationship_kind": assertion.get("relationship_kind"),
                    "subject": subject.get("mention_text"),
                    "object": obj.get("mention_text"),
                    "document_id": fact["document_id"],
                    "source_refs": [_ref(fact, block_id=block_id, page_index=block_map[block_id].get("page_index"), quoted_text=block_map[block_id].get("text")) for block_id in cited_ids],
                })
        links.append({
            "id": canonical_id,
            "relation_type": "shared_canonical_entity",
            "normalized_identifier": normalized,
            "identifier_type": entity_type,
            "document_ids": doc_ids,
            "document_count": len(doc_ids),
            "rank": 0.9,
            "rank_kind": "exact_normalized_entity_match",
            "method": "grounded_entity_exact_v1",
            "explanation": "The same typed entity mention was extracted from validated source blocks in multiple documents. This is an analyst review candidate, not confirmation of identity or transaction linkage.",
            "evidence_kind": "source_grounded_entity_mentions",
            "explicit_or_inferred": "explicit_mention_with_model_assisted_typing",
            "limitations": "Entity typing and canonical grouping are model-assisted. Exact source text and citations are preserved, but identity has not been confirmed.",
            "evidence_refs": references,
            "relationship_assertions": relationship_assertions,
            "review_status": "candidate",
        })
    # Surface high-precision local matches while model enrichment is pending.
    # The candidate builder uses only typed exact matches and source citations.
    deterministic_links = build_deterministic_relationship_candidates(facts)
    existing_pairs = {
        (str(link.get("identifier_type") or "").upper(), str(link.get("normalized_identifier") or ""))
        for link in links
    }
    deterministic_links = [
        link for link in deterministic_links
        if (link["identifier_type"].upper(), link["normalized_identifier"]) not in existing_pairs
    ]
    links.extend(deterministic_links)
    status = "no_documents" if not documents else "ready" if any(doc.get("blocks") or doc.get("text") for doc in documents) else "no_ocr_results"
    return {
        "status": status,
            "method": "hybrid_deterministic_exact_v2",
        "summary": {
            "documents_total": len(documents),
            "documents_with_ocr": sum(bool(fact["text"] or fact["identifiers"]) for fact in facts),
            "documents_pending": sum(doc.get("processing_status") in {"pending", "queued", "processing", "relationship_extraction_pending", "relationship_extraction_processing"} for doc in documents),
            "documents_with_processing_errors": sum(doc.get("processing_status") in {"failed", "relationship_extraction_failed"} for doc in documents),
            "identifier_mentions": sum(len(fact["identifiers"]) for fact in facts),
            "candidate_entities": len(analyzed["nodes"]),
            "deterministic_candidate_links": len(deterministic_links),
            "total_links": len(links),
            "candidate_links": len(links),
            "confirmed_links": 0,
            "unreviewed_links": len(links),
            "contradictions": sum(item["finding_type"] == "identifier_amount_conflict" for item in analyzed["findings"]),
        },
        "links": links,
        "comparisons": comparisons,
        "contradictions": [
            {"id": item["fingerprint"], **item}
            for item in analyzed["findings"]
            if item["finding_type"] == "identifier_amount_conflict"
        ],
        "limitations": "Exact labeled identifiers are candidate links, not identity or transaction proof. Dates and amounts are comparison signals only. OCR uncertainty and unlabeled fields can affect results.",
    }


_CORRELATION_CACHE_MAX_ENTRIES = 8
_CORRELATION_CACHE_MAX_VALUE_BYTES = 2_000_000
_CORRELATION_CACHE_MAX_INPUT_BYTES = 8_000_000
_correlation_cache: OrderedDict[str, tuple[dict[str, Any], int]] = OrderedDict()
_correlation_cache_bytes = 0
_correlation_cache_lock = threading.RLock()


def build_cross_document_correlation(documents: list[dict], *, include_timings: bool = False) -> dict[str, Any]:
    """Cache repeat correlation reads by a content-complete fingerprint.

    Review state is overlaid by the API after this function returns, so it is
    intentionally not cached here. Cached results are copied before return.
    """
    started = time.perf_counter()
    serialized = json.dumps(documents, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    if len(serialized.encode("utf-8")) <= _CORRELATION_CACHE_MAX_INPUT_BYTES:
        cache_key = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        with _correlation_cache_lock:
            cached = _correlation_cache.get(cache_key)
            if cached is not None:
                _correlation_cache.move_to_end(cache_key)
                result = copy.deepcopy(cached[0])
                if include_timings:
                    elapsed = round((time.perf_counter() - started) * 1000, 3)
                    result["timings_ms"] = {"cache_hit": True, "deterministic_computation": elapsed}
                return result

    result = _build_cross_document_correlation_uncached(documents)
    computation_ms = round((time.perf_counter() - started) * 1000, 3)
    encoded_result = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    value_bytes = len(encoded_result.encode("utf-8"))
    if len(serialized.encode("utf-8")) <= _CORRELATION_CACHE_MAX_INPUT_BYTES and value_bytes <= _CORRELATION_CACHE_MAX_VALUE_BYTES:
        global _correlation_cache_bytes
        stored_result = copy.deepcopy(result)
        with _correlation_cache_lock:
            previous = _correlation_cache.pop(cache_key, None)
            if previous:
                _correlation_cache_bytes -= previous[1]
            _correlation_cache[cache_key] = (stored_result, value_bytes)
            _correlation_cache_bytes += value_bytes
            while len(_correlation_cache) > _CORRELATION_CACHE_MAX_ENTRIES or _correlation_cache_bytes > _CORRELATION_CACHE_MAX_ENTRIES * _CORRELATION_CACHE_MAX_VALUE_BYTES:
                _old_key, (_old_value, old_bytes) = _correlation_cache.popitem(last=False)
                _correlation_cache_bytes -= old_bytes
    if include_timings:
        result["timings_ms"] = {"cache_hit": False, "deterministic_computation": computation_ms}
    return result
