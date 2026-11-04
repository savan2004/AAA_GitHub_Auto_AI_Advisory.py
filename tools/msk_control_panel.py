"""Offline incident viewer. No server, external JS, authentication or production writes.

python -m tools.msk_control_panel --input incidents.json --output control-panel.html
Open the generated HTML locally. Do not publish it publicly.
"""
from __future__ import annotations
import argparse
import html
import json
from pathlib import Path

def render(report: dict) -> str:
    incidents = report.get("incidents", [])
    rows = []
    for item in incidents:
        cells = [item.get("id", ""), item.get("priority", ""), item.get("category", ""),
                 str(item.get("count", 0)), item.get("action", ""), item.get("sample", "")]
        rows.append("<tr>" + "".join("<td>" + html.escape(str(c), quote=True) + "</td>" for c in cells) + "</tr>")
    total = int(report.get("total_events", 0))
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MSK Lite Error Control</title><style>
body{font:15px system-ui,sans-serif;background:#f5f7fa;color:#182338;margin:2rem}
main{max-width:1200px;margin:auto}header{display:flex;justify-content:space-between;align-items:center}
strong{color:#a12626}table{width:100%;border-collapse:collapse;background:white}
th,td{text-align:left;padding:10px;border-bottom:1px solid #dde3ea;overflow-wrap:anywhere}
th{background:#e8eef5}small{color:#566479} .scroll{overflow-x:auto}
</style></head><body><main><header><h1>MSK Lite Error Control</h1>
<strong>READ ONLY · NO AUTO DEPLOY</strong></header>
<p>Events: """ + str(total) + " · Signatures: " + str(len(incidents)) + """</p>
<small>Local diagnostic report only. Never publish raw logs or incident data.</small>
<div class="scroll"><table><thead><tr><th>ID</th><th>Priority</th><th>Category</th>
<th>Count</th><th>Action</th><th>Sanitized sample</th></tr></thead><tbody>""" + "".join(rows) + """</tbody></table></div>
</main></body></html>"""

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    report = json.loads(args.input.read_text(encoding="utf-8"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(report), encoding="utf-8")
    print("Generated local read-only control panel:", args.output)

if __name__ == "__main__":
    main()
