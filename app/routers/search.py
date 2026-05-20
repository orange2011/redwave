from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from app.services.search_service import (
    album_score,
    artist_score,
    build_search_context,
    compact_text,
    match_text,
    track_score,
)
from app.templates_config import templates

router = APIRouter()


def _fmt_count(value) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return ""


def _rank_rows(query: str, items: list[dict], scorer, label_key: str, subtitle_keys: tuple[str, ...]) -> list[dict]:
    rows = []
    for item in items:
        title = item.get(label_key, "")
        subtitle = " - ".join(str(item.get(key, "")) for key in subtitle_keys if item.get(key))
        rows.append(
            {
                "title": title,
                "subtitle": subtitle,
                "source": item.get("source") or ("library" if item.get("in_collection") else ""),
                "score": scorer(query, item),
                "in_collection": bool(item.get("in_collection")),
            }
        )
    return sorted(rows, key=lambda row: row["score"], reverse=True)


def _top_summary(query: str, top: dict | None) -> dict | None:
    if not top:
        return None
    result_type = top.get("type", "")
    if result_type == "artist":
        listeners = _fmt_count(top.get("listeners"))
        return {
            "type": "artist",
            "title": top.get("name", ""),
            "subtitle": f"{listeners} listeners" if listeners else "",
            "score": artist_score(query, top),
        }
    if result_type == "track":
        return {
            "type": "song",
            "title": top.get("track", ""),
            "subtitle": " - ".join(v for v in [top.get("artist", ""), top.get("album", "")] if v),
            "score": track_score(query, top),
        }
    return {
        "type": "album",
        "title": top.get("album", ""),
        "subtitle": top.get("artist", ""),
        "score": album_score(query, top),
    }


@router.get("/search", response_class=HTMLResponse)
async def search(request: Request, q: str = Query(default="")):
    ctx = await build_search_context(request, q)
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse("partials/search_results.html", ctx)
    return templates.TemplateResponse("search.html", ctx)


@router.get("/search/diagnostics", response_class=HTMLResponse)
async def search_diagnostics(request: Request, q: str = Query(default="")):
    ctx = await build_search_context(request, q)
    term = ctx.get("search_term") or q
    diagnostics = None
    if term.strip():
        diagnostics = {
            "normalized": match_text(term),
            "compact": compact_text(term),
            "top": _top_summary(term, ctx.get("top_result")),
            "songs": _rank_rows(term, ctx.get("track_results", [])[:12], track_score, "track", ("artist", "album")),
            "albums": _rank_rows(
                term,
                (ctx.get("collection_hits", []) + ctx.get("results", []))[:20],
                album_score,
                "album",
                ("artist",),
            ),
            "artists": _rank_rows(term, ctx.get("artists", [])[:12], artist_score, "name", ()),
        }
    return templates.TemplateResponse("search_diagnostics.html", {**ctx, "diagnostics": diagnostics})
