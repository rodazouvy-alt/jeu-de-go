from __future__ import annotations

import re
import subprocess
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from html import escape
from urllib.parse import quote
from .config import resolve_path
from .dashboard_ui import (
    EXPLORER_BUCKET_FILES,
    EXPLORER_BUCKET_ORDER,
    PATTERN_KIND_PAGES,
    RECURRENTS_BUCKET_FILES,
    render_alerte_page,
    render_dashboard,
    render_games_page,
    render_games_hub,
    render_patterns_explorer,
    render_patterns_explorer_hub,
    render_patterns_kind_hub,
    render_patterns_kind_page,
    static_game_parts_script,
    _patch_stone_count,
    _stone_length_label,
)
from .db import Database
from .local_pattern import FUSEKI_MAX_MOVE


def _game_date_key(g: dict[str, Any]) -> tuple[int, int, int]:
    return (int(g.get("year") or 0), int(g.get("month") or 0), int(g["id"]))


def _format_game_month(g: dict[str, Any]) -> str:
    y, m = int(g.get("year") or 0), int(g.get("month") or 0)
    return f"{y}-{m:02d}" if y and m else "?"


def _chunk_hub_meta(chunk: list[dict[str, Any]]) -> dict[str, Any]:
    ids = [int(g["id"]) for g in chunk]
    keys = [_game_date_key(g) for g in chunk]
    dmin, dmax = min(keys), max(keys)
    gmin = gmax = chunk[0]
    for g in chunk:
        k = _game_date_key(g)
        if k == dmin:
            gmin = g
        if k == dmax:
            gmax = g
    date_label = (
        _format_game_month(gmin)
        if dmin == dmax
        else f"{_format_game_month(gmin)} – {_format_game_month(gmax)}"
    )
    return {
        "date_min": dmin,
        "date_label": date_label,
        "id_min": min(ids),
        "id_max": max(ids),
    }


def _build_sorted_chunk_hub(
    parts: list[list[dict[str, Any]]],
) -> tuple[list[tuple[str, str, int, str]], dict[str, tuple[int, dict[str, Any]]]]:
    """Lot 1 = plus ancien, tri chronologique ; retourne liens hub + métadonnées par fichier."""
    filenames = [f"games-part-{i:02d}.html" for i in range(1, len(parts) + 1)]
    items = [
        (_chunk_hub_meta(chunk), filename, chunk)
        for chunk, filename in zip(parts, filenames)
    ]
    items.sort(key=lambda x: x[0]["date_min"])
    chunk_links: list[tuple[str, str, int, str]] = []
    lot_by_file: dict[str, tuple[int, dict[str, Any]]] = {}
    for lot_num, (meta, filename, chunk) in enumerate(items, 1):
        lot_by_file[filename] = (lot_num, meta)
        chunk_links.append((
            f"Lot {lot_num}",
            filename,
            len(chunk),
            f"{meta['date_label']} · #{meta['id_min']}–#{meta['id_max']}",
        ))
    return chunk_links, lot_by_file


def _staticize_html(html: str) -> str:
    """Liens relatifs pour hébergement statique (Cloudflare Pages)."""

    def _route_href(match: re.Match[str]) -> str:
        path = (match.group(1) or "").strip("/")
        qs = match.group(2) or ""
        if not path:
            return f'href="index.html{qs}"'
        if path == "games":
            return f'href="games.html{qs}"'
        if path == "alerte":
            return f'href="alerte.html{qs}"'
        if path == "patterns":
            return f'href="patterns.html{qs}"'
        if path.startswith("patterns/kind/"):
            rest = path[len("patterns/kind/"):]
            if "/stones/" in rest:
                kind, slug = rest.split("/stones/", 1)
                return f'href="patterns-kind-{kind}-{slug}.html{qs}"'
            kind = rest.rsplit("/", 1)[-1]
            return f'href="patterns-kind-{kind}.html{qs}"'
        if path.startswith("patterns/stones/"):
            slug = path.rsplit("/", 1)[-1]
            return f'href="patterns-stones-{slug}.html{qs}"'
        return match.group(0)

    html = re.sub(r'href="/([^"?]*)(\?[^"]*)?"', _route_href, html)
    return html.replace('href="/"', 'href="index.html"')


def _write_static_page(
    publish_dir: Path,
    filename: str,
    html: str,
    cfg: dict[str, Any],
    *,
    game_parts: dict[int, str] | None = None,
) -> None:
    (publish_dir / filename).write_text(
        _static_banner(cfg, game_parts=game_parts) + _staticize_html(html),
        encoding="utf-8",
    )

def _static_banner(
    cfg: dict[str, Any] | None = None,
    *,
    game_parts: dict[int, str] | None = None,
) -> str:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    url = ""
    if cfg:
        url = (cfg.get("cloudflare", {}) or {}).get("pages_url") or ""
    url_line = (
        f' · <a href="{escape(url)}" style="color:#6b4423">miroir mobile</a>'
        if url else ""
    )
    parts_script = static_game_parts_script(game_parts) if game_parts else ""
    return (
        f'<div id="static-mirror-marker" data-static-mirror="1" hidden aria-hidden="true"></div>'
        f'<div style="background:#ede0c8;border:1px solid #d4c4a8;color:#2a2118;'
        f'padding:0.6rem 1rem;margin:0;font-size:0.85rem;text-align:center;'
        f'font-family:\'Source Sans 3\',system-ui,sans-serif">'
        f"Miroir Cloudflare — snapshot du {ts}{url_line}. "
        f"Analyse locale inchangée ; Lizzie et filtres live = <code>start.bat</code> sur le PC."
        f"</div>"
        f"{parts_script}"
    )


def _static_page_bytes(html: str, cfg: dict[str, Any]) -> int:
    return len((_static_banner(cfg) + _staticize_html(html)).encode("utf-8"))


def _estimate_games_per_chunk(
    n_games: int,
    total_bytes: int,
    max_bytes: int,
    *,
    safety: float = 0.85,
) -> int:
    """Estime le nombre de parties par fichier sans re-rendu (1 mesure globale)."""
    if n_games <= 0:
        return 1
    if total_bytes <= max_bytes:
        return n_games
    per_game = total_bytes / n_games
    return max(1, int(max_bytes * safety / per_game))


def _games_publish_chunks(
    games: list[Any],
    render_chunk: Any,
    cfg: dict[str, Any],
    *,
    max_bytes: int,
) -> list[list[Any]]:
    """Découpe garantie sous max_bytes (re-rendu si une tranche dépasse)."""
    chunks: list[list[Any]] = [games]
    while True:
        oversized = False
        next_chunks: list[list[Any]] = []
        for chunk in chunks:
            if len(chunk) <= 1:
                next_chunks.append(chunk)
                continue
            if _static_page_bytes(render_chunk(chunk), cfg) <= max_bytes:
                next_chunks.append(chunk)
            else:
                mid = max(1, len(chunk) // 2)
                next_chunks.append(chunk[:mid])
                next_chunks.append(chunk[mid:])
                oversized = True
        chunks = next_chunks
        if not oversized:
            return chunks


def publish_dashboard(
    db: Database,
    cfg: dict[str, Any],
    *,
    games_limit: int = 200,
    static_export: bool = False,
) -> Path:
    publish_dir = resolve_path(cfg.get("paths", {}).get("publish_dir", "data/publish"))
    publish_dir.mkdir(parents=True, exist_ok=True)

    thresholds = cfg.get("thresholds") or {}
    stats = db.reconcile_move_severities(thresholds)
    if stats["updated"]:
        print(
            f"  Scores recalculés : {stats['updated']} coups "
            f"({stats['loss_fixed']} pertes, {stats['best_fixed']} best_move) "
            f"sur {stats['total']}",
            flush=True,
        )

    username = cfg["player"]["kgs_username"]
    opening_prog = db.opening_progress()
    stats = {
        **db.stats_enhanced(username, fuseki_max_move=FUSEKI_MAX_MOVE),
        "pending_analysis": db.pending_analysis_count(),
        "opening_pending": opening_prog["pending"],
    }
    pattern_totals = {
        "pattern": db.count_pattern_clusters("pattern"),
        "joseki": db.count_pattern_clusters("joseki"),
        "fuseki": db.count_pattern_clusters("fuseki"),
    }
    drilldown = db.dashboard_drilldown()

    index_html = render_dashboard(
        stats, [], {}, username, drilldown, db,
        db.timing_stats(),
        pattern_totals=pattern_totals,
        filter_meta=db.filter_metadata(),
        games_analyzed=db.count_analyzed_games(),
        suspect_count=db.count_suspicious_opponents(),
        static_export=static_export,
    )
    if not static_export:
        (publish_dir / "index.html").write_text(index_html, encoding="utf-8")

    game_to_part: dict[int, str] = {}
    opponent_links: list[tuple[str, str, int]] = []

    if static_export:
        ccfg = cfg.get("cloudflare", {}) or {}
        games_limit = int(ccfg.get("publish_games_limit", games_limit))
        patterns_limit = ccfg.get("publish_patterns_limit")
        pat_limit: int | None = int(patterns_limit) if patterns_limit else None
        publish_full_diagrams = bool(ccfg.get("publish_full_diagrams", True))

        total_games = db.count_analyzed_games()
        games = db.dashboard_games(limit=games_limit)
        game_ids = [g["id"] for g in games]
        pattern_recurrence = db.pattern_recurrence_for_games(game_ids)
        max_bytes = int(ccfg.get("publish_max_file_mb", 22)) * 1024 * 1024

        lot_by_file: dict[str, tuple[int, dict[str, Any]]] = {}

        def _render_games_chunk(
            chunk: list[Any],
            filename: str | None = None,
        ) -> str:
            if filename and filename in lot_by_file:
                lot_num, meta = lot_by_file[filename]
                label = (
                    f"Lot {lot_num} — {meta['date_label']} "
                    f"(#{meta['id_min']}–#{meta['id_max']})"
                )
            else:
                meta = _chunk_hub_meta(chunk)
                label = (
                    f"Parties #{meta['id_min']}–#{meta['id_max']} "
                    f"— {meta['date_label']}"
                )
            return render_games_page(
                chunk, username, db, pattern_recurrence,
                limit=games_limit, total=total_games,
                static_export=True,
                chunk_label=label,
            )

        def _register_games_chunk(chunk: list[Any], filename: str) -> None:
            for g in chunk:
                game_to_part[int(g["id"])] = filename

        def _build_opponent_links() -> list[tuple[str, str, int]]:
            opp_first: dict[str, str] = {}
            opp_counts: dict[str, int] = defaultdict(int)
            for g in games:
                gid = int(g["id"])
                fname = game_to_part.get(gid)
                if not fname:
                    continue
                opp = (g.get("opponent") or "?").strip() or "?"
                opp_counts[opp] += 1
                if opp not in opp_first:
                    opp_first[opp] = fname
            return sorted(
                [
                    (opp, f"{opp_first[opp]}?opp={quote(opp)}", opp_counts[opp])
                    for opp in opp_first
                ],
                key=lambda x: (-x[2], x[0].lower()),
            )

        single_size = 0
        full_games_html = ""
        for old in publish_dir.glob("games-part-*.html"):
            old.unlink(missing_ok=True)
        if games:
            print(f"  Parties : rendu HTML ({len(games)} parties)...", flush=True)
            full_games_html = _render_games_chunk(games)
            single_size = _static_page_bytes(full_games_html, cfg)
            if single_size <= max_bytes:
                _register_games_chunk(games, "games.html")
                opponent_links = _build_opponent_links()
                _write_static_page(
                    publish_dir, "games.html", full_games_html, cfg,
                    game_parts=game_to_part,
                )
                print(f"  Parties : games.html ({single_size / 1024 / 1024:.1f} Mo)", flush=True)
            else:
                parts = _games_publish_chunks(
                    games,
                    lambda chunk: _render_games_chunk(chunk),
                    cfg,
                    max_bytes=max_bytes,
                )
                print(
                    f"  Parties : {len(parts)} tranches "
                    f"(ref. {single_size / 1024 / 1024:.0f} Mo)...",
                    flush=True,
                )
                chunk_links, lot_by_file = _build_sorted_chunk_hub(parts)
                for part_num, chunk in enumerate(parts, 1):
                    filename = f"games-part-{part_num:02d}.html"
                    print(f"    {filename} ({len(chunk)} parties)...", flush=True)
                    chunk_html = _render_games_chunk(chunk, filename)
                    chunk_size = _static_page_bytes(chunk_html, cfg)
                    _register_games_chunk(chunk, filename)
                    _write_static_page(
                        publish_dir, filename, chunk_html, cfg,
                        game_parts=game_to_part,
                    )
                    print(
                        f"      -> {chunk_size / 1024 / 1024:.1f} Mo",
                        flush=True,
                    )
                opponent_links = _build_opponent_links()
                hub_html = render_games_hub(
                    username, chunk_links,
                    opponent_links=opponent_links,
                    total=total_games,
                )
                _write_static_page(
                    publish_dir, "games.html", hub_html, cfg,
                    game_parts=game_to_part,
                )
        else:
            games_html = render_games_page(
                games, username, db, pattern_recurrence,
                limit=games_limit, total=total_games,
                static_export=True,
            )
            _write_static_page(
                publish_dir, "games.html", games_html, cfg,
                game_parts=game_to_part,
            )

        _write_static_page(
            publish_dir, "index.html", index_html, cfg,
            game_parts=game_to_part or None,
        )

        all_opponents = db.opponent_suspicion_stats()
        alerte_html = render_alerte_page(
            all_opponents[:50], db.suspicious_opponent_moves(limit=50),
            username, db,
            limit=50, total=len(all_opponents),
            player_stats=db.player_move_quality_stats(),
            static_export=True,
        )
        _write_static_page(
            publish_dir, "alerte.html", alerte_html, cfg,
            game_parts=game_to_part or None,
        )

        for kind_key in PATTERN_KIND_PAGES:
            meta_kind = PATTERN_KIND_PAGES[kind_key]["kind"]
            total = db.count_pattern_clusters(meta_kind)
            patterns = db.pattern_clusters_list(meta_kind, limit=pat_limit)
            cluster_ids = [int(c["id"]) for c in patterns]
            occ = db.pattern_clusters_occurrences(cluster_ids) if cluster_ids else {}

            if kind_key == "recurrents" and publish_full_diagrams:
                bucket_counts = {b: 0 for b in EXPLORER_BUCKET_ORDER}
                for c in patterns:
                    bucket = _stone_length_label(_patch_stone_count(c["patch_ascii"]))
                    if bucket in bucket_counts:
                        bucket_counts[bucket] += 1
                meta = PATTERN_KIND_PAGES["recurrents"]
                bucket_links = [
                    (bucket, RECURRENTS_BUCKET_FILES[bucket], bucket_counts[bucket])
                    for bucket in EXPLORER_BUCKET_ORDER
                    if bucket_counts[bucket] > 0
                ]
                hub_html = render_patterns_kind_hub(
                    "recurrents", username,
                    str(meta["title"]), str(meta["hint"]),
                    bucket_links, total=total,
                )
                for old in publish_dir.glob("patterns-kind-recurrents-*.html"):
                    old.unlink(missing_ok=True)
                _write_static_page(
                    publish_dir, "patterns-kind-recurrents.html", hub_html, cfg,
                    game_parts=game_to_part or None,
                )
                for bucket in EXPLORER_BUCKET_ORDER:
                    if bucket_counts.get(bucket, 0) == 0:
                        continue
                    bucket_html = render_patterns_kind_page(
                        "recurrents", patterns, username, db, occ,
                        limit=pat_limit, total=total,
                        static_export=True,
                        full_diagrams=True,
                        only_bucket=bucket,
                    )
                    _write_static_page(
                        publish_dir, RECURRENTS_BUCKET_FILES[bucket], bucket_html, cfg,
                        game_parts=game_to_part or None,
                    )
                continue

            kind_html = render_patterns_kind_page(
                kind_key, patterns, username, db, occ,
                limit=pat_limit, total=total,
                static_export=True,
                full_diagrams=publish_full_diagrams,
            )
            _write_static_page(
                publish_dir, f"patterns-kind-{kind_key}.html", kind_html, cfg,
                game_parts=game_to_part or None,
            )

    all_patterns = db.all_pattern_clusters()
    cluster_ids = [int(c["id"]) for c in all_patterns]
    occurrences = db.pattern_clusters_occurrences(cluster_ids) if cluster_ids else {}

    if static_export and (cfg.get("cloudflare", {}) or {}).get("publish_full_diagrams", True):
        bucket_counts = {b: 0 for b in EXPLORER_BUCKET_ORDER}
        for c in all_patterns:
            bucket = _stone_length_label(_patch_stone_count(c["patch_ascii"]))
            if bucket in bucket_counts:
                bucket_counts[bucket] += 1
        fuseki_n = sum(
            1 for c in all_patterns
            if (c["pattern_kind"] if "pattern_kind" in c.keys() else "pattern") == "fuseki"
        )
        joseki_n = sum(
            1 for c in all_patterns
            if (c["pattern_kind"] if "pattern_kind" in c.keys() else "pattern") == "joseki"
        )
        bucket_links = [
            (bucket, EXPLORER_BUCKET_FILES[bucket], bucket_counts[bucket])
            for bucket in EXPLORER_BUCKET_ORDER
            if bucket_counts[bucket] > 0
        ]
        hub_html = render_patterns_explorer_hub(
            username, bucket_links,
            total=len(all_patterns), fuseki_n=fuseki_n, joseki_n=joseki_n,
        )
        for old in publish_dir.glob("patterns-stones-*.html"):
            old.unlink(missing_ok=True)
        _write_static_page(
            publish_dir, "patterns.html", hub_html, cfg,
            game_parts=game_to_part or None,
        )
        for bucket in EXPLORER_BUCKET_ORDER:
            if bucket_counts.get(bucket, 0) == 0:
                continue
            bucket_html = render_patterns_explorer(
                all_patterns, username, db,
                occurrences_by_cluster=occurrences,
                static_export=True,
                full_diagrams=True,
                only_bucket=bucket,
            )
            _write_static_page(
                publish_dir, EXPLORER_BUCKET_FILES[bucket], bucket_html, cfg,
                game_parts=game_to_part or None,
            )
    else:
        patterns_html = render_patterns_explorer(
            all_patterns, username, db,
            occurrences_by_cluster=occurrences,
            static_export=static_export,
        )
        if static_export:
            _write_static_page(
                publish_dir, "patterns.html", patterns_html, cfg,
                game_parts=game_to_part or None,
            )
        else:
            (publish_dir / "patterns.html").write_text(patterns_html, encoding="utf-8")

    readme_lines = [
        "Go Progress — snapshot statique",
        "================================",
        "",
        f"Généré : {datetime.now().isoformat(timespec='seconds')}",
        "",
        "Fichiers :",
        "  index.html                  — dashboard",
        "  games.html                  — parties (hub si découpé)",
        "  games-part-*.html           — tranches de parties (miroir mobile)",
        "  patterns.html               — explorateur (hub par tranche de pierres)",
        "  patterns-stones-*.html      — explorateur avec diagrammes SVG complets",
        "  patterns-kind-*.html        — joseki / fuseki / récurrents",
        "  patterns-kind-recurrents-*.html — récurrents avec diagrammes SVG complets",
        "  alerte.html                 — adversaires suspects",
        "",
    ]
    if static_export and (cfg.get("cloudflare", {}) or {}).get("publish_full_diagrams", True):
        readme_lines.extend([
            "Diagrammes : export SVG complets (limite Cloudflare 25 Mo/fichier).",
            "L'explorateur est découpé en tranches patterns-stones-*.html.",
            "Les patterns récurrents sont découpés en tranches patterns-kind-recurrents-*.html.",
            "",
        ])
    readme_lines.extend([
        "Déploiement Cloudflare Pages (gratuit) :",
        "  1. Compte Cloudflare → Pages → Create project",
        "  2. Upload ce dossier ou : wrangler pages deploy data/publish",
        "  3. Relancer publish après chaque lot d'analyses",
        "",
        "Pour Lizzie, filtres live et diagrammes : start.bat ou tunnel Cloudflare.",
        "",
    ])
    (publish_dir / "README.txt").write_text(
        "\n".join(readme_lines),
        encoding="utf-8",
    )

    return publish_dir


def deploy_cloudflare_pages(publish_dir: Path, project_name: str) -> bool:
    """Déploie data/publish sur Cloudflare Pages via wrangler (si installé)."""
    if not project_name.strip():
        return False
    try:
        result = subprocess.run(
            [
                "wrangler", "pages", "deploy", str(publish_dir),
                "--project-name", project_name.strip(),
            ],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except FileNotFoundError:
        print(
            "  wrangler introuvable — installez : npm install -g wrangler",
            flush=True,
        )
        return False
    if result.returncode != 0:
        print(f"  Deploy Cloudflare echoue : {result.stderr or result.stdout}", flush=True)
        return False
    for line in (result.stdout or "").splitlines():
        if "https://" in line:
            print(f"  Site en ligne : {line.strip()}", flush=True)
    return True


def publish_and_deploy(db: Database, cfg: dict[str, Any], **kwargs: Any) -> Path:
    """Export snapshot + déploiement Cloudflare Pages optionnel."""
    path = publish_dashboard(db, cfg, static_export=True, **kwargs)
    print(f"  Snapshot : {path}", flush=True)
    ccfg = cfg.get("cloudflare", {})
    if ccfg.get("auto_deploy_pages") and ccfg.get("pages_project"):
        deploy_cloudflare_pages(path, str(ccfg["pages_project"]))
    return path