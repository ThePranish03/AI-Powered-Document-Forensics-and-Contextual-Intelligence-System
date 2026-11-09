"""Render immutable investigation snapshots as standalone HTML and PDF reports."""
from __future__ import annotations

import hashlib
import html
import io
import json
from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageBreak, PageTemplate, Paragraph,
    Spacer, Table, TableStyle,
)


def _esc(value) -> str:
    return html.escape("" if value is None else str(value))


def snapshot_digest(snapshot: dict, report_html: str) -> str:
    """Hash the snapshot and HTML, excluding the digest field itself."""
    normalized = dict(snapshot)
    report = dict(normalized.get("report") or {})
    report.pop("integrity_sha256", None)
    normalized["report"] = report
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256((canonical + "\n" + report_html).encode("utf-8")).hexdigest()


def build_snapshot(*, investigation: dict, documents: list[dict], findings: list[dict], timeline: list[dict], nodes: list[dict], correlations: list[dict], discrepancies: list[dict], audit_activity: list[dict] | None = None, document_relationships: list[dict] | None = None, audit_verification: dict | None = None, visual_runs: list[dict] | None = None) -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "label": "Evidence snapshot. Automated items remain candidates until an investigator reviews them.",
        "investigation": investigation,
        "evidence_inventory": documents,
        "extracted_information": [{
            "document_id": item["id"], "filename": item["filename"],
            "text": item.get("extracted_text", ""), "blocks": item.get("extracted_blocks", []),
        } for item in documents if item.get("extracted_text") or item.get("extracted_blocks")],
        "relationship_overview": nodes,
        "document_relationships": document_relationships or [],
        "cross_document_links": correlations,
        "cross_document_discrepancies": discrepancies,
        "visual_analysis": {
            "status": "completed" if any(run.get("status") == "completed" for run in (visual_runs or [])) else ("partial" if visual_runs else "not_run"),
            "runs": visual_runs or [],
            "checks": [
                {"check_id": check.get("check_type"), "status": check.get("status"), "result": run.get("result", {}).get("observations", []), "evidence_ref": None,
                 "explanation": check.get("algorithm"), "limitations": "Observations are candidates requiring investigator review; visual signals alone do not establish authenticity."}
                for run in (visual_runs or []) for check in run.get("result", {}).get("checks", [])
            ],
        },
        "timeline": timeline,
        "findings": findings,
        "reviewed_findings": [item for item in findings if item["status"] != "candidate"],
        "audit_activity": audit_activity or [],
        "audit_chain_verification": audit_verification or {"valid": False, "event_count": len(audit_activity or []), "verified_count": 0, "reason": "Not verified."},
        "methodology": [
            "Findings are produced by deterministic rules over stored OCR text and exact labeled identifiers.",
            "OCR text and engine confidence are extracted signals; review the unmodified source before drawing conclusions.",
            "Cross-document links are candidates based on exact identifiers and do not establish common identity or transaction.",
            "The report is a versioned snapshot of database records at generation time.",
        ],
        "limitations": [
            "OCR text is not a substitute for the original file.",
            "Engine confidence is not calibrated accuracy.",
            "Rules can miss context such as credits, rounding, or multi-currency totals.",
            "Visual-forensics observations are algorithmic candidates; they do not determine document authenticity.",
            "Investigator conclusions are only included when explicitly recorded in stored data.",
            "Language-model narrative is an optional, source-cited summary and does not create evidence or conclusions.",
        ],
        "investigator_conclusion": None,
        "ai_narrative": {"status": "not_generated", "text": None, "evidence_refs": []},
    }


def _refs(refs: list[dict]) -> str:
    return "".join(
        "<li><b>{file}</b> · document {doc} · page {page} · block {block} · {field} · quote: “{quote}” · coordinates: {coords}</li>".format(
            file=_esc(ref.get("filename") or "Unknown source"), doc=_esc(ref.get("document_id")),
            page=_esc(ref.get("page_number") or "unknown"), block=_esc(ref.get("block_id") or "unknown"),
            field=_esc(ref.get("field_type") or "evidence"), quote=_esc(ref.get("quoted_text") or ""),
            coords=_esc(ref.get("bounding_box") or ref.get("coordinates") or "not recorded"),
        ) for ref in refs
    ) or "<li>No source references were stored.</li>"


def render_html(snapshot: dict) -> str:
    inv = snapshot["investigation"]
    report = snapshot.get("report", {})
    docs = snapshot.get("evidence_inventory", [])
    inventory = "".join(
        "<tr><td>{filename}</td><td><code>{id}</code></td><td><code>{sha}</code></td><td>{status}</td><td>{size}</td><td>{date}</td></tr>".format(
            filename=_esc(doc.get("filename")), id=_esc(doc.get("id")), sha=_esc(doc.get("sha256") or "not available"),
            status=_esc(doc.get("processing_status")), size=_esc(doc.get("size_bytes")), date=_esc(doc.get("uploaded_at")),
        ) for doc in docs
    ) or '<tr><td colspan="6">No documents were stored in this investigation.</td></tr>'

    extracted = []
    for item in snapshot.get("extracted_information", []):
        blocks = "".join(
            "<tr><td>{page}</td><td><code>{block}</code></td><td>{confidence}</td><td>{bbox}</td><td>{text}</td></tr>".format(
                page=_esc((block.get("page_index", 0) + 1) if isinstance(block.get("page_index", 0), int) else "unknown"),
                block=_esc(block.get("block_id")), confidence=_esc(block.get("engine_confidence", "not recorded")),
                bbox=_esc(block.get("engine_bbox", block.get("bounding_box", "not recorded"))), text=_esc(block.get("text")),
            ) for block in item.get("blocks", [])
        )
        extracted.append("<article><h3>{}</h3><p>Document ID: <code>{}</code></p><pre>{}</pre><table><thead><tr><th>Page</th><th>Block</th><th>Engine confidence</th><th>Coordinates</th><th>OCR text</th></tr></thead><tbody>{}</tbody></table></article>".format(
            _esc(item.get("filename")), _esc(item.get("document_id")), _esc(item.get("text")), blocks or "<tr><td colspan='5'>No OCR blocks were stored.</td></tr>"))

    findings = []
    for item in snapshot.get("findings", []):
        findings.append("<article><h3>{}</h3><p>{}</p><dl><dt>Classification</dt><dd>{} · {}</dd><dt>Review status</dt><dd>{}</dd><dt>Confidence</dt><dd>{}</dd><dt>Detection method</dt><dd>{} · {}/{}</dd><dt>Analyst decision</dt><dd>{}</dd><dt>Review note</dt><dd>{}</dd><dt>Created</dt><dd>{}</dd><dt>Limitations</dt><dd>{}</dd></dl><ul>{}</ul></article>".format(
            _esc(item.get("title")), _esc(item.get("description")), _esc(item.get("finding_type")), _esc(item.get("severity")),
            _esc(item.get("status")), _esc(item.get("confidence")), _esc(item.get("method")), _esc(item.get("analyzer_name")), _esc(item.get("analyzer_version")),
            _esc(item.get("status")), _esc(item.get("review_notes") or "No analyst review recorded."), _esc(item.get("created_at")),
            _esc(item.get("limitations")), _refs(item.get("evidence_refs", []))))

    graph = "".join("<article><h3>{} · {}</h3><p>Resolution: {} · Review: {} · Documents: {}</p><p>{}</p><p>{}</p></article>".format(
        _esc(item.get("label")), _esc(item.get("type")), _esc(item.get("resolution")), _esc(item.get("review_status")), _esc(", ".join(item.get("document_ids", []))),
        _esc(item.get("basis")), _esc(item.get("review_reason") or "No review decision recorded.")) for item in snapshot.get("relationship_overview", []))
    relationships = "".join("<article><h3>{}</h3><pre>{}</pre></article>".format(_esc(item.get("filename")), _esc(json.dumps(item.get("payload"), indent=2, ensure_ascii=False))) for item in snapshot.get("document_relationships", []))
    correlations = "".join("<article><h3>{}: {}</h3><p>Status: {} · Method: {}</p><p>{}</p><p>Review note: {}</p><ul>{}</ul></article>".format(
        _esc(item.get("identifier_type")), _esc(item.get("normalized_identifier")), _esc(item.get("review_status")), _esc(item.get("method")),
        _esc(item.get("explanation")), _esc(item.get("review_reason") or "None"), _refs(item.get("evidence_refs", []))) for item in snapshot.get("cross_document_links", []))
    discrepancies = "".join("<article><h3>{}</h3><p>{}</p><p>{}</p><ul>{}</ul></article>".format(
        _esc(item.get("title")), _esc(item.get("description")), _esc(item.get("limitations")), _refs(item.get("evidence_refs", []))) for item in snapshot.get("cross_document_discrepancies", []))
    visual_checks = "".join("<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
        _esc(item.get("check_id")), _esc(item.get("status")), _esc(item.get("result") or "No result"), _esc(item.get("limitations"))) for item in snapshot.get("visual_analysis", {}).get("checks", []))
    timeline = "".join("<li><b>{}</b> {} · {} · confidence {} {}</li>".format(
        _esc(item.get("raw_date")), "(ambiguous date)" if item.get("ambiguous") else "", _esc(item.get("description")),
        _esc(item.get("confidence")), _refs(item.get("evidence_refs", []))) for item in snapshot.get("timeline", []))
    audit = "".join("<tr><td>{}</td><td>{}</td><td>{} {}</td><td>{}</td><td><code>{}</code></td><td><code>{}</code></td></tr>".format(
        _esc(item.get("created_at")), _esc(item.get("action")), _esc(item.get("target_type")), _esc(item.get("target_id")),
        _esc(item.get("actor_user_id") or "system"), _esc(item.get("previous_hash") or "not recorded"), _esc(item.get("event_hash") or "not recorded")) for item in snapshot.get("audit_activity", []))
    narrative = snapshot.get("ai_narrative") or {}
    narrative_html = "<p>{}</p><p>Model: {} · status: {}</p><ul>{}</ul>".format(_esc(narrative.get("text") or "No model narrative was stored."),
        _esc(narrative.get("model") or "not used"), _esc(narrative.get("status")), _refs(narrative.get("evidence_refs", [])))
    lists = lambda items: "".join(f"<li>{_esc(value)}</li>" for value in items) or "<li>None recorded.</li>"
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Investigation dossier · {_esc(inv.get("case_number"))}</title>
<style>
:root{{--ink:#152331;--muted:#526273;--line:#ccd5dc;--accent:#075b62;--paper:#fff;--wash:#eef3f4}}
*{{box-sizing:border-box}}body{{margin:0;color:var(--ink);background:var(--paper);font:15px/1.55 Arial,Helvetica,sans-serif}}.cover{{min-height:88vh;padding:14vh 10vw 8vh;display:flex;flex-direction:column;justify-content:space-between;background:linear-gradient(145deg,#102532,#174a50);color:#fff;page-break-after:always}}.brand{{font-size:13px;letter-spacing:.2em;text-transform:uppercase}}.cover h1{{font-size:52px;line-height:1.08;max-width:800px;margin:0}}.cover p{{color:#d8e7e6}}.meta{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;max-width:780px}}.meta div{{border-top:1px solid #6d9392;padding-top:8px}}.meta small{{display:block;color:#bed0cf;text-transform:uppercase;letter-spacing:.1em}}main{{max-width:1100px;margin:auto;padding:24px 42px}}h2{{font-size:24px;border-bottom:2px solid var(--accent);padding-bottom:8px;margin-top:42px;page-break-after:avoid}}h3{{font-size:17px;margin-bottom:6px}}article{{border:1px solid var(--line);border-radius:8px;padding:16px;margin:14px 0;break-inside:avoid}}table{{width:100%;border-collapse:collapse;margin:12px 0;font-size:12px;table-layout:fixed}}th{{background:var(--wash);text-align:left}}td,th{{border:1px solid var(--line);padding:7px;vertical-align:top;overflow-wrap:anywhere}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f6f7;padding:12px;border-radius:6px;font:12px/1.5 Consolas,monospace;max-height:none}}code{{overflow-wrap:anywhere}}dl{{display:grid;grid-template-columns:180px 1fr;gap:4px 12px}}dt{{color:var(--muted);font-weight:bold}}dd{{margin:0}}.badge{{display:inline-block;padding:4px 8px;border:1px solid #aac4c4;border-radius:30px}}.muted{{color:var(--muted)}}footer{{border-top:1px solid var(--line);padding:16px 42px;color:var(--muted);font-size:11px}}@media print{{.cover{{min-height:96vh}}main{{padding:0 18px}}article,table{{break-inside:avoid}}h2{{break-after:avoid}}}}
</style></head><body>
<section class="cover"><div class="brand">DocForensIQ · Evidence intelligence</div><div><p>CONFIDENTIAL INVESTIGATION REPORT · VERSION {_esc(report.get("version", "pending"))}</p><h1>{_esc(inv.get("title") or "Investigation dossier")}</h1><p>{_esc(inv.get("case_number"))}</p></div><div class="meta"><div><small>Investigation ID</small>{_esc(inv.get("id"))}</div><div><small>Investigation status</small>{_esc(inv.get("status"))}</div><div><small>Created</small>{_esc(inv.get("created_at") or "Not available")}</div><div><small>Report generated</small>{_esc(snapshot.get("generated_at"))}</div><div><small>Report ID</small>{_esc(report.get("dossier_id", "pending"))}</div><div><small>Content SHA-256</small><code>See accompanying JSON integrity metadata</code></div></div></section>
<main><h2>Executive summary</h2><p>{_esc(inv.get("description") or "No investigation scope description is recorded.")}</p><p>This report contains {len(docs)} persisted document(s), {len(snapshot.get("findings", []))} candidate/reviewed finding(s), {len(snapshot.get("cross_document_links", []))} cross-document link(s), and {len(snapshot.get("timeline", []))} timeline event(s). Findings remain candidates unless an investigator decision is recorded.</p>{narrative_html}
<h2>Scope and methodology</h2><p>Investigation status: <b>{_esc(inv.get("status"))}</b>. The dossier was assembled from records persisted at generation time and is immutable as a versioned snapshot.</p><ul>{lists(snapshot.get("methodology", []))}</ul>
<h2>Document inventory and chain of custody</h2><table><thead><tr><th>File</th><th>Document ID</th><th>SHA-256</th><th>Processing status</th><th>Bytes</th><th>Uploaded at</th></tr></thead><tbody>{inventory}</tbody></table>
<h2>OCR-extracted data and source traceability</h2>{''.join(extracted) or '<p>No OCR text is available.</p>'}
<h2>Cross-document verification</h2>{''.join(correlations) or '<p>No cross-document identifier links were found.</p>'}{''.join(discrepancies) or '<p>No supported cross-document discrepancies were found.</p>'}
<h2>Evidence-backed findings and analyst disposition</h2>{''.join(findings) or '<p>No findings are stored for this investigation.</p>'}
<h2>Visual-forensics results</h2><p>Status: <span class="badge">{_esc(snapshot.get("visual_analysis", {}).get("status"))}</span>. Measured observations remain candidates for investigator review and do not determine authenticity.</p><table><thead><tr><th>Check</th><th>Status</th><th>Result</th><th>Limitations</th></tr></thead><tbody>{visual_checks or '<tr><td colspan="4">No visual checks were recorded.</td></tr>'}</tbody></table>
<h2>Relationship analysis</h2>{''.join(graph) or '<p>No investigation relationship graph nodes are stored.</p>'}{''.join(relationships) or '<p>No per-document relationship extraction payloads are stored.</p>'}
<h2>Investigation timeline</h2><ul>{timeline or '<li>No timeline events are stored.</li>'}</ul>
<h2>Audit trail and chain of custody</h2><p>Audit chain verification: <b>{'valid' if snapshot.get('audit_chain_verification', {}).get('valid') else 'not verified or invalid'}</b> · { _esc(snapshot.get('audit_chain_verification', {}).get('verified_count', 0)) }/{_esc(snapshot.get('audit_chain_verification', {}).get('event_count', 0))} events verified. {_esc(snapshot.get('audit_chain_verification', {}).get('reason') or '')}</p><table><thead><tr><th>Timestamp</th><th>Action</th><th>Target</th><th>Actor user ID</th><th>Previous hash</th><th>Event hash</th></tr></thead><tbody>{audit or '<tr><td colspan="6">No audit events were stored.</td></tr>'}</tbody></table>
<h2>Final conclusion</h2><p>{_esc(snapshot.get("investigator_conclusion") or "No final investigator conclusion is recorded in the persisted investigation data.")}</p><h2>Limitations and disclaimer</h2><ul>{lists(snapshot.get("limitations", []))}</ul><p class="muted">This report is a traceable summary of stored information. It does not establish fraud, authenticity, intent, or legal conclusions.</p></main><footer>DocForensIQ · Report version {_esc(report.get("version", "pending"))} · Generated {_esc(snapshot.get("generated_at"))} · Investigation {_esc(inv.get("id"))}</footer></body></html>'''


def render_pdf(snapshot: dict) -> bytes:
    """Create a genuine, paginated PDF from a persisted report snapshot."""
    buffer = io.BytesIO()
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="CoverTitle", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=27, leading=33, textColor=colors.HexColor("#15333c"), alignment=TA_CENTER, spaceAfter=14))
    styles.add(ParagraphStyle(name="Section", parent=styles["Heading1"], fontSize=16, leading=20, textColor=colors.HexColor("#075b62"), spaceBefore=12, spaceAfter=8, keepWithNext=True))
    styles.add(ParagraphStyle(name="Small", parent=styles["BodyText"], fontSize=8, leading=10, wordWrap="CJK"))
    styles.add(ParagraphStyle(name="Cell", parent=styles["BodyText"], fontSize=8, leading=10, wordWrap="CJK"))
    styles.add(ParagraphStyle(name="Lead", parent=styles["BodyText"], fontSize=12, leading=17, alignment=TA_CENTER, textColor=colors.HexColor("#526273")))
    page_w, page_h = A4
    margin = 17 * mm

    def decorate(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#ccd5dc"))
        canvas.line(margin, 13 * mm, page_w - margin, 13 * mm)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#526273"))
        canvas.drawString(margin, 8.5 * mm, "DocForensIQ · Evidence-backed investigation report")
        canvas.drawRightString(page_w - margin, 8.5 * mm, f"Page {doc.page}")
        canvas.restoreState()

    doc = BaseDocTemplate(buffer, pagesize=A4, leftMargin=margin, rightMargin=margin, topMargin=18 * mm, bottomMargin=20 * mm, title=f"Investigation dossier {snapshot['investigation'].get('case_number', '')}", author="DocForensIQ")
    frame = Frame(margin, 20 * mm, page_w - 2 * margin, page_h - 38 * mm, id="normal")
    doc.addPageTemplates([PageTemplate(id="report", frames=[frame], onPage=decorate)])
    story = []
    inv = snapshot["investigation"]
    report = snapshot.get("report", {})

    def para(value, style="BodyText"):
        return Paragraph(_esc(value).replace("\n", "<br/>"), styles[style])

    def section(title, text=None):
        story.append(Paragraph(_esc(title), styles["Section"]))
        if text:
            story.append(para(text))

    def table(rows, widths=None):
        if not rows:
            rows = [["No records stored."]]
        wrapped = [[para(value, "Cell") for value in row] for row in rows]
        t = Table(wrapped, colWidths=widths, repeatRows=1, hAlign="LEFT")
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eaf0f1")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#15333c")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), .35, colors.HexColor("#ccd5dc")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.extend([t, Spacer(1, 6)])

    story.extend([Spacer(1, 38 * mm), para("DOCFORENSIQ · EVIDENCE INTELLIGENCE", "Lead"), Spacer(1, 14 * mm),
                  para("INVESTIGATION DOSSIER", "CoverTitle"), para(inv.get("title") or "Investigation report", "Lead"), Spacer(1, 10 * mm),
                  para(f"{inv.get('case_number', '')} · Report version {report.get('version', 'pending')}", "Lead"), Spacer(1, 35 * mm)])
    table([["Investigation ID", inv.get("id", "")], ["Status", inv.get("status", "")], ["Created", inv.get("created_at", "Not available")], ["Report generated", snapshot.get("generated_at", "")], ["Report ID", report.get("dossier_id", "pending")], ["Content SHA-256", report.get("integrity_sha256", "calculated after rendering")]], [45 * mm, 125 * mm])
    story.append(PageBreak())
    section("Executive summary", inv.get("description") or "No investigation scope description is recorded.")
    story.append(para(f"{len(snapshot.get('evidence_inventory', []))} documents · {len(snapshot.get('findings', []))} findings · {len(snapshot.get('cross_document_links', []))} cross-document links · {len(snapshot.get('timeline', []))} timeline events"))
    narrative = snapshot.get("ai_narrative") or {}
    story.append(para(f"Contextual narrative ({narrative.get('status', 'not generated')}): {narrative.get('text') or 'No model narrative was stored.'}"))
    section("Scope and methodology")
    for item in snapshot.get("methodology", []): story.append(para("• " + item))
    section("Document inventory and chain of custody")
    table([["File", "Document ID", "SHA-256", "Status", "Bytes", "Uploaded"]] + [[d.get("filename", ""), d.get("id", ""), d.get("sha256") or "N/A", d.get("processing_status", ""), str(d.get("size_bytes", "")), d.get("uploaded_at", "")] for d in snapshot.get("evidence_inventory", [])], [35 * mm, 32 * mm, 37 * mm, 22 * mm, 15 * mm, 29 * mm])
    section("OCR-extracted data and source traceability")
    for item in snapshot.get("extracted_information", []):
        story.extend([para(item.get("filename", "Unknown document"), "Heading3"), para(f"Document ID: {item.get('document_id', '')}", "Small"), para(item.get("text", "") or "No OCR text.", "Small")])
        blocks = [["Page", "Block", "Confidence", "Coordinates", "OCR text"]]
        for block in item.get("blocks", []):
            original_text = str(block.get("text", ""))
            parts = [original_text[start:start + 300] for start in range(0, len(original_text), 300)] or [""]
            for part_index, part in enumerate(parts, start=1):
                page = str(block.get("page_index", 0) + 1) if isinstance(block.get("page_index", 0), int) else "unknown"
                block_id = str(block.get("block_id", "")) + (f" · part {part_index}/{len(parts)}" if len(parts) > 1 else "")
                blocks.append([page, block_id, str(block.get("engine_confidence", "N/A")), str(block.get("engine_bbox", block.get("bounding_box", "N/A"))), part])
        if len(blocks) > 1: table(blocks, [13 * mm, 25 * mm, 21 * mm, 30 * mm, 81 * mm])
    section("Cross-document verification")
    for link in snapshot.get("cross_document_links", []):
        story.append(para(f"{link.get('identifier_type')}: {link.get('normalized_identifier')} · {link.get('review_status')} · {link.get('method')}" , "Heading3")); story.append(para(link.get("explanation", "")))
        for ref in link.get("evidence_refs", []): story.append(para(f"Source {ref.get('filename') or ref.get('document_id')} · page {ref.get('page_number')} · block {ref.get('block_id')}: {ref.get('quoted_text')}", "Small"))
    for item in snapshot.get("cross_document_discrepancies", []):
        story.append(para(item.get("title", "Discrepancy"), "Heading3")); story.append(para(item.get("description", "")))
        for ref in item.get("evidence_refs", []): story.append(para(f"Source {ref.get('filename') or ref.get('document_id')} · page {ref.get('page_number')} · block {ref.get('block_id')}: {ref.get('quoted_text')}", "Small"))
    if not snapshot.get("cross_document_links") and not snapshot.get("cross_document_discrepancies"): story.append(para("No supported cross-document link or discrepancy is stored."))
    section("Evidence-backed findings and analyst disposition")
    for item in snapshot.get("findings", []):
        story.extend([para(item.get("title", "Finding"), "Heading3"), para(item.get("description", "")), para(f"Type: {item.get('finding_type')} · Severity: {item.get('severity')} · Status: {item.get('status')} · Confidence: {item.get('confidence')}"), para(f"Method: {item.get('method')} · Analyzer: {item.get('analyzer_name')}/{item.get('analyzer_version')}"), para(f"Review: {item.get('review_notes') or 'No analyst review recorded.'}")])
        for ref in item.get("evidence_refs", []): story.append(para(f"Source {ref.get('filename') or ref.get('document_id')} · page {ref.get('page_number')} · block {ref.get('block_id')} · coordinates {ref.get('bounding_box', 'not recorded')}: {ref.get('quoted_text')}", "Small"))
        story.append(Spacer(1, 5))
    if not snapshot.get("findings"): story.append(para("No findings are stored for this investigation."))
    section("Visual-forensics results", f"Status: {snapshot.get('visual_analysis', {}).get('status')}. Measured observations are candidates requiring investigator review and do not determine authenticity.")
    table([["Check", "Status", "Result", "Limitations"]] + [[c.get("check_id", ""), c.get("status", ""), str(c.get("result") or "No result"), c.get("limitations", "")] for c in snapshot.get("visual_analysis", {}).get("checks", [])], [42 * mm, 28 * mm, 33 * mm, 67 * mm])
    section("Relationship analysis")
    for node in snapshot.get("relationship_overview", []): story.append(para(f"{node.get('label')} · {node.get('type')} · {node.get('review_status')} · documents {', '.join(node.get('document_ids', []))}")); story.append(para(node.get("basis", ""), "Small"))
    for relation in snapshot.get("document_relationships", []): story.append(para(relation.get("filename", "Document"), "Heading3")); story.append(para(json.dumps(relation.get("payload", {}), ensure_ascii=False), "Small"))
    if not snapshot.get("relationship_overview") and not snapshot.get("document_relationships"): story.append(para("No relationship analysis is stored."))
    section("Investigation timeline")
    for item in snapshot.get("timeline", []):
        story.append(para(f"{item.get('raw_date')} {'(ambiguous)' if item.get('ambiguous') else ''} · {item.get('description')} · confidence {item.get('confidence')}"))
        for ref in item.get("evidence_refs", []): story.append(para(f"Source {ref.get('filename') or ref.get('document_id')} · page {ref.get('page_number')} · block {ref.get('block_id')}: {ref.get('quoted_text')}", "Small"))
    if not snapshot.get("timeline"): story.append(para("No timeline events are stored."))
    verification = snapshot.get("audit_chain_verification", {})
    section("Audit trail and chain of custody", f"Verification: {'valid' if verification.get('valid') else 'not verified or invalid'} · {verification.get('verified_count', 0)}/{verification.get('event_count', 0)} events. {verification.get('reason') or ''}")
    table([["Timestamp", "Action", "Target", "Actor", "Previous hash", "Event hash"]] + [[a.get("created_at", ""), a.get("action", ""), f"{a.get('target_type', '')} {a.get('target_id', '')}", a.get("actor_user_id") or "system", a.get("previous_hash", "N/A"), a.get("event_hash", "N/A")] for a in snapshot.get("audit_activity", [])], [27 * mm, 25 * mm, 30 * mm, 23 * mm, 35 * mm, 35 * mm])
    section("Final conclusion", snapshot.get("investigator_conclusion") or "No final investigator conclusion is recorded in the persisted investigation data.")
    section("Limitations and disclaimer")
    for item in snapshot.get("limitations", []): story.append(para("• " + item))
    story.append(Spacer(1, 8)); story.append(para("This report summarizes stored information. It does not establish fraud, authenticity, intent, or legal conclusions.", "Small"))
    doc.build(story)
    return buffer.getvalue()
