"""High precision, source-grounded cross-document relationship candidates."""
from __future__ import annotations

import hashlib
import ipaddress
import re
import unicodedata
from collections import defaultdict


_EMAIL = re.compile(r"(?i)(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])")
_PHONE = re.compile(
    r"(?i)\b(?:phone|telephone|mobile|contact(?:\s+phone)?|tel)\b[^+\d\r\n]{0,8}"
    r"(?P<value>\+?\d[\d().\s-]{5,}\d)"
)
_IP = re.compile(
    r"(?i)\b(?:source\s+ip|ip(?:v[46])?\s+address|ip)\b\s*[:#-]?\s*"
    r"(?P<value>[0-9a-f:.]{7,39})"
)
_REGISTRATION = re.compile(
    r"(?i)\b(?:company\s+)?(?:registration(?:\s*/\s*tax)?(?:\s+(?:id|no|number))?|"
    r"tax\s+(?:id|number)|gstin|vat(?:\s+(?:id|number))?|ein)\b"
    r"\s*[:#-]?\s*(?P<value>[A-Z0-9][A-Z0-9/-]{3,})"
)
_GENERIC = {
    "account", "company", "contact", "corporate", "document", "employee", "entity",
    "invoice", "ledger", "name", "n/a", "na", "none", "unknown", "vendor",
}


def _clean(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split()).strip(" .,:;()[]{}")


def _valid_name(value: str, entity_type: str) -> bool:
    normalized = _clean(value)
    tokens = re.findall(r"[^\W_]+", normalized, flags=re.UNICODE)
    if normalized in _GENERIC or len(normalized) < 4 or not tokens:
        return False
    if entity_type == "PERSON":
        return len(tokens) >= 2 and all(len(token) > 1 for token in tokens)
    return len(tokens) >= 2 and any(len(token) > 2 for token in tokens)


def _mentions_from_facts(facts: list[dict]) -> list[dict]:
    mentions: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()

    def add(fact: dict, block: dict, entity_type: str, raw: str, normalized: str, method: str, strength: str):
        block_id = str(block.get("block_id") or "")
        raw_value = raw.strip()
        if not block_id or not raw_value or not normalized:
            return
        key = (str(fact["document_id"]), entity_type, normalized, block_id)
        if key in seen:
            return
        seen.add(key)
        mentions.append({
            "document_id": str(fact["document_id"]),
            "filename": fact.get("filename") or str(fact["document_id"]),
            "source_sha256": fact.get("sha256"),
            "entity_type": entity_type,
            "raw": raw_value,
            "normalized": normalized,
            "block_id": block_id,
            "page_index": block.get("page_index"),
            "quoted_text": str(block.get("text") or ""),
            "extraction_method": block.get("extraction_method", "ocr"),
            "matching_method": method,
            "evidence_strength": strength,
        })

    for fact in facts:
        for block in fact.get("source_blocks", []):
            if not isinstance(block, dict):
                continue
            text = str(block.get("text") or "")
            for match in _EMAIL.finditer(text):
                value = match.group(0).rstrip(".,;:")
                local, _, domain = value.partition("@")
                if len(value) <= 254 and local and "." in domain:
                    add(fact, block, "EMAIL", value, value.casefold(), "email_exact_normalized", "strong_identifier")
            for match in _PHONE.finditer(text):
                value = match.group("value").strip()
                digits = re.sub(r"\D", "", value)
                if 7 <= len(digits) <= 15:
                    add(fact, block, "PHONE", value, digits, "labeled_phone_digits_exact", "strong_identifier")
            for match in _IP.finditer(text):
                value = match.group("value").rstrip(".,;:")
                try:
                    normalized = ipaddress.ip_address(value).compressed.casefold()
                except ValueError:
                    continue
                add(fact, block, "IP", value, normalized, "labeled_ip_exact", "strong_identifier")
            for match in _REGISTRATION.finditer(text):
                value = match.group("value").rstrip(".,;:")
                normalized = re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold()
                if len(re.sub(r"\W", "", normalized)) >= 4:
                    add(fact, block, "REGISTRATION_OR_TAX_ID", value, normalized, "labeled_registration_tax_id_exact", "strong_identifier")

        # Only explicitly labeled OCR fields qualify; unlabelled names are not guessed.
        block_by_id = {str(item.get("block_id")): item for item in fact.get("source_blocks", []) if isinstance(item, dict)}
        for observed in fact.get("observed_fields", []):
            if not isinstance(observed, dict):
                continue
            field_type = observed.get("field_type")
            entity_type = "ORGANIZATION" if field_type == "organization" else "PERSON" if field_type == "person_name" else None
            raw = str(observed.get("raw") or "").strip()
            normalized = _clean(raw)
            if entity_type and _valid_name(raw, entity_type):
                block = block_by_id.get(str(observed.get("block_id")), {})
                add(fact, block, entity_type, raw, normalized, f"labeled_{entity_type.lower()}_exact", "review_candidate")

    return mentions


def build_deterministic_relationship_candidates(facts: list[dict]) -> list[dict]:
    """Return exact, typed, source-cited cross-document candidates only.

    Dates and currency values are deliberately excluded from identity matching.
    A matching IP, phone, email, or name remains an unconfirmed review candidate.
    """
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for mention in _mentions_from_facts(facts):
        groups[(mention["entity_type"], mention["normalized"])].append(mention)

    links = []
    for (entity_type, normalized), occurrences in sorted(groups.items()):
        document_ids = list(dict.fromkeys(item["document_id"] for item in occurrences))
        if len(document_ids) < 2:
            continue
        canonical = f"{entity_type}|{normalized}"
        candidate_id = "detrel:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
        references = []
        for item in occurrences:
            page_index = item.get("page_index")
            references.append({
                "document_id": item["document_id"],
                "filename": item["filename"],
                "source_sha256": item.get("source_sha256"),
                "page_index": page_index,
                "page_number": page_index + 1 if isinstance(page_index, int) and not isinstance(page_index, bool) else None,
                "block_id": item["block_id"],
                "field_type": entity_type.lower(),
                "raw_value": item["raw"],
                "normalized_value": normalized,
                "matching_method": item["matching_method"],
                "evidence_strength": item["evidence_strength"],
                "extraction_method": item["extraction_method"],
                "quoted_text": item["quoted_text"],
            })
        limitations = {
            "IP": "A shared IP can represent a VPN, proxy, gateway, or NAT; it does not identify a person.",
            "PHONE": "A phone number can be shared or reassigned; equality is a review lead, not proof of identity.",
            "EMAIL": "An email address can be shared, delegated, or reassigned; equality is a review lead.",
            "PERSON": "Exact name equality can refer to different people; verify additional identifiers.",
            "ORGANIZATION": "Exact name equality can refer to similarly named legal entities; verify registration details.",
            "REGISTRATION_OR_TAX_ID": "Exact labeled identifier equality is a review lead and does not prove a transaction or party relationship.",
        }.get(entity_type, "Exact normalized equality is a review lead and does not prove identity or a transaction relationship.")
        links.append({
            "id": candidate_id,
            "relation_type": "shared_deterministic_identifier" if entity_type not in {"PERSON", "ORGANIZATION"} else "shared_labeled_name",
            "normalized_identifier": normalized,
            "identifier_type": entity_type,
            "document_ids": document_ids,
            "document_count": len(document_ids),
            "rank": 1.0,
            "rank_kind": "exact_rule_match",
            "method": "deterministic_relationship_candidate_v1",
            "explanation": f"The same {entity_type.lower().replace('_', ' ')} value was extracted from labeled or validated source evidence in multiple documents. This is an analyst review candidate.",
            "evidence_kind": "deterministic_source_match",
            "explicit_or_inferred": "explicit",
            "limitations": limitations,
            "evidence_refs": references,
            "review_status": "candidate",
        })
    return links
