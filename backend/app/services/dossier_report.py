import html
from datetime import datetime, timezone


def _esc(value) -> str:
    return html.escape("" if value is None else str(value))


def build_snapshot(*, investigation: dict, documents: list[dict], findings: list[dict], timeline: list[dict], nodes: list[dict], correlations: list[dict], discrepancies: list[dict]) -> dict:
    reviewed = [item for item in findings if item["status"] != "candidate"]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "label": "Evidence snapshot. Automated items are candidates until an investigator reviews them.",
        "investigation": investigation,
        "evidence_inventory": documents,
        "relationship_overview": nodes,
        "cross_document_links": correlations,
        "cross_document_discrepancies": discrepancies,
        "timeline": timeline,
        "findings": findings,
        "reviewed_findings": reviewed,
        "limitations": [
            "OCR text is not a substitute for the original file.",
            "Engine confidence is not calibrated accuracy.",
            "Rules can miss context such as credits, rounding, or multi-currency totals.",
            "No visual analyzer in this build asserts that a document was forged.",
            "Language-model narrative is omitted unless a provider response is stored separately from this snapshot.",
        ],
        "investigator_conclusion": None,
        "ai_narrative": None,
    }


def render_html(snapshot: dict) -> str:
    investigation = snapshot["investigation"]
    rows = []
    for document in snapshot["evidence_inventory"]:
        rows.append(
            "<tr><td>{filename}</td><td><code>{sha}</code></td><td>{status}</td></tr>".format(
                filename=_esc(document.get("filename")),
                sha=_esc(document.get("sha256")),
                status=_esc(document.get("processing_status")),
            )
        )
    finding_rows = []
    for finding in snapshot["findings"]:
        finding_rows.append(
            "<article><h3>{title}</h3><p>{description}</p><p><b>Status:</b> {status} · <b>Method:</b> {method}</p><p>{limits}</p></article>".format(
                title=_esc(finding.get("title")),
                description=_esc(finding.get("description")),
                status=_esc(finding.get("status")),
                method=_esc(finding.get("method")),
                limits=_esc(finding.get("limitations")),
            )
        )
    timeline_rows = "".join(
        "<li>{raw}{flag}: {description}</li>".format(
            raw=_esc(item.get("raw_date")),
            flag=" (ambiguous)" if item.get("ambiguous") else "",
            description=_esc(item.get("description")),
        )
        for item in snapshot["timeline"]
    )
    correlation_rows = []
    for link in snapshot.get("cross_document_links", []):
        citations = "".join(
            "<li>{filename} · page {page} · {block}: {quote}</li>".format(
                filename=_esc(ref.get("filename")),
                page=_esc(ref.get("page_number") or "unknown"),
                block=_esc(ref.get("block_id") or "unknown block"),
                quote=_esc(ref.get("quoted_text")),
            )
            for ref in link.get("evidence_refs", [])
        )
        correlation_rows.append(
            "<article><h3>{kind}: {identifier}</h3><p>Status: {status} · Method: {method}</p><p>{explanation}</p><ul>{citations}</ul><p>Review note: {reason}</p></article>".format(
                kind=_esc(link.get("identifier_type")),
                identifier=_esc(link.get("normalized_identifier")),
                status=_esc(link.get("review_status")),
                method=_esc(link.get("method")),
                explanation=_esc(link.get("explanation")),
                citations=citations or "<li>No citations available.</li>",
                reason=_esc(link.get("review_reason") or "None"),
            )
        )
    discrepancy_rows = []
    for item in snapshot.get("cross_document_discrepancies", []):
        refs = "".join(
            "<li>{filename} · page {page} · {block}: {quote}</li>".format(
                filename=_esc(ref.get("filename")),
                page=_esc(ref.get("page_number") or "unknown"),
                block=_esc(ref.get("block_id") or "unknown block"),
                quote=_esc(ref.get("quoted_text")),
            )
            for ref in item.get("evidence_refs", [])
        )
        discrepancy_rows.append(
            "<article><h3>{title}</h3><p>{description}</p><p>{limitations}</p><ul>{refs}</ul></article>".format(
                title=_esc(item.get("title")),
                description=_esc(item.get("description")),
                limitations=_esc(item.get("limitations")),
                refs=refs or "<li>No citations available.</li>",
            )
        )
    limitations = "".join(f"<li>{_esc(item)}</li>" for item in snapshot["limitations"])
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Dossier {_esc(investigation.get("case_number"))}</title>
<style>body{{font-family:Georgia,serif;margin:2rem;color:#1c1917;background:#faf7f2}}h1,h2{{font-weight:500}}code{{font-size:.85em}}article{{border-top:1px solid #ddd;padding:.8rem 0}}</style>
</head><body>
<p>DocForensIQ investigation dossier · snapshot</p>
<h1>{_esc(investigation.get("title"))}</h1>
<p>{_esc(investigation.get("case_number"))} · generated {_esc(snapshot.get("generated_at"))}</p>
<p>{_esc(snapshot.get("label"))}</p>
<h2>Evidence inventory</h2>
<table><thead><tr><th>File</th><th>SHA-256</th><th>Status</th></tr></thead><tbody>{''.join(rows) or '<tr><td colspan="3">No documents</td></tr>'}</tbody></table>
<h2>Cross-document links</h2>{''.join(correlation_rows) or '<p>No exact identifier links were found.</p>'}
<h2>Cross-document discrepancies</h2>{''.join(discrepancy_rows) or '<p>No supported total conflicts were found.</p>'}
<h2>Timeline</h2><ul>{timeline_rows or '<li>No extracted dates.</li>'}</ul>
<h2>Findings</h2>{''.join(finding_rows) or '<p>No findings in this snapshot.</p>'}
<h2>Limitations</h2><ul>{limitations}</ul>
<p>Investigator conclusion: {_esc(snapshot.get("investigator_conclusion") or "Not recorded in this snapshot.")}</p>
<p>AI narrative: {_esc(snapshot.get("ai_narrative") or "Not included. This snapshot was assembled from stored evidence and rules.")}</p>
</body></html>"""
