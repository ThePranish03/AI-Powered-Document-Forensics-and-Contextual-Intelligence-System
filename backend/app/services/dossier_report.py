import html
from datetime import datetime, timezone


def _esc(value) -> str:
    return html.escape("" if value is None else str(value))


def build_snapshot(*, investigation: dict, documents: list[dict], findings: list[dict], timeline: list[dict], nodes: list[dict]) -> dict:
    reviewed = [item for item in findings if item["status"] != "candidate"]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "label": "Evidence snapshot. Automated items are candidates until an investigator reviews them.",
        "investigation": investigation,
        "evidence_inventory": documents,
        "relationship_overview": nodes,
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
<h2>Timeline</h2><ul>{timeline_rows or '<li>No extracted dates.</li>'}</ul>
<h2>Findings</h2>{''.join(finding_rows) or '<p>No findings in this snapshot.</p>'}
<h2>Limitations</h2><ul>{limitations}</ul>
<p>Investigator conclusion: {_esc(snapshot.get("investigator_conclusion") or "Not recorded in this snapshot.")}</p>
<p>AI narrative: {_esc(snapshot.get("ai_narrative") or "Not included. This snapshot was assembled from stored evidence and rules.")}</p>
</body></html>"""
