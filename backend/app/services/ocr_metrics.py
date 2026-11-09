"""OCR quality signals and evaluation metrics.

Confidence values are engine scores, not measured accuracy. Character and word
error rates are computed only when a non-empty ground-truth reference is supplied.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
import math
import re
from typing import Any


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


_AMOUNT = r"([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})|[0-9]+\.[0-9]{2})"
_LABELS = (
    ("subtotal", re.compile(rf"(?i)\bsub[\s-]*total\b\s*[:\-]?\s*(?:INR|USD|EUR|Rs\.?|₹|\$|€)?\s*{_AMOUNT}")),
    ("tax", re.compile(rf"(?i)\b(?:tax|gst|vat)\b\s*[:\-]?\s*(?:INR|USD|EUR|Rs\.?|₹|\$|€)?\s*{_AMOUNT}")),
    ("total", re.compile(rf"(?i)\b(?:grand\s+total|total\s+amount|amount\s+due|total)\b\s*[:\-]?\s*(?:INR|USD|EUR|Rs\.?|₹|\$|€)?\s*{_AMOUNT}")),
)
_IDENTIFIER = re.compile(r"(?i)\b(?P<label>invoice|inv|purchase\s*order|po|transaction|txn|account|reference|ref)(?:\s*(?:no|number|id|#))?\s*[:#\-]?\s*(?P<value>[A-Z0-9][A-Z0-9\-]{4,})")
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_SLASH_DATE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b")


def _money(value: str) -> float:
    return float(value.replace(",", ""))


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
    seen_identifiers: set[tuple[str, str]] = set()
    for block in blocks:
        text = str(block.get("text") or "")
        if text:
            pieces.append(text)
        block_id = block.get("block_id")
        page = block.get("page_index")
        for label, pattern in _LABELS:
            if label in labeled:
                continue
            match = pattern.search(text)
            if match:
                labeled[label] = {"value": _money(match.group(1)), "raw": match.group(1), "block_id": block_id, "page_index": page, "text": text}
        for match in _IDENTIFIER.finditer(text):
            value = match.group("value").upper()
            label = re.sub(r"\s+", " ", match.group("label").casefold())
            entity_type = ("INVOICE" if label in {"invoice", "inv"} else
                           "PURCHASE_ORDER" if label in {"purchase order", "po"} else
                           "TRANSACTION" if label in {"transaction", "txn"} else
                           "ACCOUNT" if label == "account" else "REFERENCE")
            key = (entity_type, value)
            if key in seen_identifiers:
                continue
            seen_identifiers.add(key)
            identifiers.append({"value": value, "entity_type": entity_type, "block_id": block_id, "page_index": page, "text": text})
        for match in _ISO_DATE.finditer(text):
            year, month, day = (int(match.group(i)) for i in (1, 2, 3))
            if _valid_date(year, month, day):
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
        "text": text,
        "identifiers": identifiers,
        "labeled_amounts": labeled,
        "dates": dates,
        "quality": quality,
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
        totals = []
        for fact, identifier in occurrences:
            total = fact["labeled_amounts"].get("total")
            if total:
                totals.append((fact, identifier, total))
        distinct = {round(item[2]["value"], 2) for item in totals}
        if len(docs) > 1 and len(distinct) > 1:
            findings.append({
                "finding_type": "identifier_amount_conflict",
                "title": f"Identifier {value} has conflicting labeled totals",
                "description": "The same identifier is paired with different labeled totals. This is a conflict to review, not proof that either document is false.",
                "severity": "high",
                "confidence": 0.7,
                "method": "shared_identifier_total_conflict",
                "fingerprint": f"conflict:{value}",
                "evidence_refs": [_ref(fact, block_id=identifier["block_id"], page_index=identifier["page_index"], quoted_text=identifier["text"], field_type="identifier") | {"related_total": total["raw"]} for fact, identifier, total in totals],
                "limitations": limitation + " The identifier match is textual. It does not prove the documents describe one transaction.",
            })
    return {"findings": findings, "timeline": timeline, "nodes": list(nodes.values()), "documents": facts}


def build_cross_document_correlation(documents: list[dict]) -> dict[str, Any]:
    """Build explainable investigation-scoped links from persisted OCR facts.

    Only exact, typed document identifiers create cross-document links. Names,
    dates, and amounts remain comparison fields and never create identity links.
    Scores are rule ranks, not probabilities.
    """
    analyzed = analyze_documents(documents)
    facts = analyzed["documents"]
    links: list[dict[str, Any]] = []
    comparisons: list[dict[str, Any]] = []
    for node in analyzed["nodes"]:
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
        links.append({
            "id": node["canonical_id"],
            "relation_type": "shared_document_identifier",
            "normalized_identifier": value,
            "identifier_type": node["entity_type"],
            "document_ids": document_ids,
            "document_count": len(document_ids),
            "rank": 1.0,
            "rank_kind": "exact_rule_match",
            "method": "exact_identifier_v1",
            "explanation": "The same labeled document identifier appears in these source records. This is a review lead and does not prove they describe the same transaction or party.",
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
    status = "no_documents" if not documents else "ready" if any(doc.get("blocks") or doc.get("text") for doc in documents) else "no_ocr_results"
    return {
        "status": status,
        "method": "deterministic_exact_identifier_v1",
        "summary": {
            "documents_total": len(documents),
            "documents_with_ocr": sum(bool(fact["text"] or fact["identifiers"]) for fact in facts),
            "documents_pending": sum(doc.get("processing_status") in {"pending", "queued", "processing", "relationship_extraction_pending"} for doc in documents),
            "documents_with_processing_errors": sum(doc.get("processing_status") in {"failed", "relationship_extraction_failed"} for doc in documents),
            "identifier_mentions": sum(len(fact["identifiers"]) for fact in facts),
            "candidate_entities": len(analyzed["nodes"]),
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
