"""
Port of DocsGraphHandler.cs

Routes:
  GET  /docs-graph           → serve HTML (or /docs, /docs/)
  POST /docs-graph/generate  → ?vaultPath=...  build graph JSON, return {ok, vaultPath}
  GET  /docs-graph/export    → download standalone HTML with embedded graph
"""

import json
import os
import re
from typing import Optional

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter()

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_TEMPLATE_PATH = os.path.join(_BASE_DIR, "wwwroot", "graph.html")
_EXPORT_TEMPLATE_PATH = os.path.join(
    _BASE_DIR, "templates", "docs_graph_export_template.html"
)

_EMPTY_GRAPH = '{"nodes":[],"edges":[]}'

# ── in-memory state (mirrors C# instance fields) ──────────────────────────────

_last_html: Optional[str] = None
_last_graph_json: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────────────


def _json_resp(data, status: int = 200) -> JSONResponse:
    return JSONResponse(content=data, status_code=status)


def _read_template(path: str) -> Optional[str]:
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return f.read()


# ── Routes ────────────────────────────────────────────────────────────────────


@router.get("/docs-graph")
@router.get("/docs")
@router.get("/docs/")
async def serve():
    global _last_html

    template = _read_template(_TEMPLATE_PATH)
    if template is None:
        return _json_resp({"error": f"Template not found: {_TEMPLATE_PATH}"}, 500)

    if _last_html is None:
        html = template.replace("DOCS_GRAPH_DATA_PLACEHOLDER", _EMPTY_GRAPH).replace(
            'vault path…"', 'vault path…" value="docs-vault'
        )
    else:
        html = _last_html

    return HTMLResponse(content=html)


@router.post("/docs-graph/generate")
async def generate(vaultPath: str = ""):
    global _last_html, _last_graph_json

    if not vaultPath.strip():
        return _json_resp({"error": "vaultPath is required"}, 400)

    if not os.path.isabs(vaultPath):
        vaultPath = os.path.join(_BASE_DIR, vaultPath)

    if not os.path.isdir(vaultPath):
        return _json_resp({"error": f"Directory not found: {vaultPath}"}, 400)

    template = _read_template(_TEMPLATE_PATH)
    if template is None:
        return _json_resp({"error": f"Template not found: {_TEMPLATE_PATH}"}, 500)

    try:
        graph_json = _build_graph_json(vaultPath)
        graph_json = graph_json.replace("</script>", "<\\/script>")

        _last_graph_json = graph_json
        _last_html = template.replace("DOCS_GRAPH_DATA_PLACEHOLDER", graph_json)

        return _json_resp({"ok": True, "vaultPath": vaultPath})
    except Exception as ex:
        return _json_resp({"error": str(ex)}, 500)


@router.get("/docs-graph/export")
async def export():
    if _last_graph_json is None:
        return _json_resp({"error": "No graph generated yet"}, 404)

    template = _read_template(_EXPORT_TEMPLATE_PATH)
    if template is None:
        return _json_resp(
            {"error": f"Export template not found: {_EXPORT_TEMPLATE_PATH}"}, 500
        )

    html = template.replace("DOCS_GRAPH_DATA_PLACEHOLDER", _last_graph_json)
    body = html.encode("utf-8")

    return Response(
        content=body,
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="docs.html"'},
    )


# ── Parser ────────────────────────────────────────────────────────────────────

_WIKILINK_RX = re.compile(r"\[\[([^\]|#\n]+)")
_TAG_LINE_RX = re.compile(r"^tags\s*:\s*(.+)$", re.MULTILINE)
_TITLE_LINE_RX = re.compile(r"^title\s*:\s*(.+)$", re.MULTILINE)


def _build_graph_json(vault_path: str) -> str:
    md_files = [
        os.path.join(root, f)
        for root, _, files in os.walk(vault_path)
        for f in files
        if f.endswith(".md") and ".obsidian" not in root
    ]

    # pass 1: label → relative id map
    label_to_id: dict[str, str] = {}
    for file in md_files:
        rel = os.path.relpath(file, vault_path).replace("\\", "/")
        label = os.path.splitext(os.path.basename(file))[0]
        label_to_id.setdefault(label.lower(), rel)

    nodes = []
    edge_set: set[str] = set()

    # pass 2: parse each file
    for file in md_files:
        rel = os.path.relpath(file, vault_path).replace("\\", "/")
        label = os.path.splitext(os.path.basename(file))[0]

        with open(file, encoding="utf-8", errors="replace") as f:
            raw = f.read()

        frontmatter, body = _split_frontmatter(raw)

        title_m = _TITLE_LINE_RX.search(frontmatter) if frontmatter else None
        title = title_m.group(1).strip().strip('"') if title_m else None

        tags = _parse_tags(frontmatter)

        nodes.append(
            {
                "id": rel,
                "label": label,
                "title": title,
                "tags": tags,
                "path": rel,
                "content": raw,
            }
        )

        # wikilink edges
        for m in _WIKILINK_RX.finditer(body):
            target = m.group(1).strip()
            target_id = label_to_id.get(target.lower())
            if target_id and target_id != rel:
                edge_set.add(f"{rel}|{target_id}|wikilink")

        # tag edges
        for tag in tags:
            edge_set.add(f"tag::{tag}|{rel}|tag")

        # folder edges
        parts = rel.split("/")
        if len(parts) > 1:
            folder_path = "/".join(parts[:-1])
            edge_set.add(f"folder::{folder_path}|{rel}|folder")

    # virtual tag nodes
    tag_nodes = [
        {
            "id": tid,
            "label": tid.replace("tag::", "#"),
            "title": None,
            "tags": [],
            "path": "",
            "content": "",
        }
        for tid in {e.split("|")[0] for e in edge_set if e.startswith("tag::")}
    ]

    # virtual folder nodes
    folder_nodes = [
        {
            "id": fid,
            "label": fid.replace("folder::", "").split("/")[-1],
            "title": None,
            "tags": [],
            "path": "",
            "content": "",
        }
        for fid in {e.split("|")[0] for e in edge_set if e.startswith("folder::")}
    ]

    all_nodes = nodes + tag_nodes + folder_nodes

    edges = [
        {"source": p[0], "target": p[1], "kind": p[2]}
        for e in edge_set
        for p in [e.split("|")]
    ]

    return json.dumps({"nodes": all_nodes, "edges": edges})


def _split_frontmatter(raw: str) -> tuple[str, str]:
    if not raw.startswith("---"):
        return "", raw
    end = raw.find("\n---", 3)
    if end < 0:
        return "", raw
    return raw[3:end].strip(), raw[end + 4 :]


def _parse_tags(frontmatter: str) -> list[str]:
    if not frontmatter:
        return []

    m = _TAG_LINE_RX.search(frontmatter)
    if not m:
        return []

    val = m.group(1).strip()

    # inline: tags: [a, b, c]
    if val.startswith("["):
        return [t.strip().strip('"') for t in val.strip("[]").split(",") if t.strip()]

    # multiline list after "tags:"
    idx = frontmatter.index(m.group(0))
    after = frontmatter[idx + len(m.group(0)) :]
    tags = []
    for line in after.split("\n"):
        l = line.strip()
        if not l.startswith("-"):
            break
        tag = l.lstrip("-").strip().strip('"')
        if tag:
            tags.append(tag)

    return tags
