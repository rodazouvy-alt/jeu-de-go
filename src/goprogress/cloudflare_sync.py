"""Miroir Cloudflare Pages — copie consultable 24/7 du dashboard local."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .config import resolve_path
from .db import Database
from .publish import deploy_cloudflare_pages, publish_dashboard


def sync_cloudflare_mirror(db: Database, cfg: dict[str, Any], *, reason: str = "") -> bool:
    """
    Exporte le dashboard en HTML statique et le pousse sur Cloudflare Pages.
    Le workflow local (analyse KataGo, SQLite) ne change pas.
    """
    ccfg = cfg.get("cloudflare", {})
    project = (ccfg.get("pages_project") or "").strip()
    if not project:
        print(
            "  Cloudflare : pages_project non configure "
            "(voir config.local.yaml.example)",
            flush=True,
        )
        return False

    label = f" ({reason})" if reason else ""
    print(f"  -> Miroir Cloudflare{label}...", flush=True)
    games_limit = int(ccfg.get("publish_games_limit", 200))
    path = publish_dashboard(db, cfg, games_limit=games_limit, static_export=True)
    meta = {
        "published_at": datetime.now(timezone.utc).isoformat(),
        "pages_project": project,
        "pages_url": ccfg.get("pages_url") or "",
        "reason": reason,
    }
    (path / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if not ccfg.get("auto_deploy_pages", True):
        print(f"  Snapshot local : {path} (deploy manuel)", flush=True)
        return True

    ok = deploy_cloudflare_pages(path, project)
    if ok:
        url = ccfg.get("pages_url") or f"https://{project}.pages.dev"
        print(f"  Miroir en ligne : {url}", flush=True)
        return True

    url = ccfg.get("pages_url") or f"https://{project}.pages.dev"
    print(f"  Snapshot OK : {path}", flush=True)
    print("  Deploy auto impossible (wrangler manquant ou non connecte).", flush=True)
    print(f"  Uploadez le dossier sur Cloudflare Pages -> {url}", flush=True)
    return True


def maybe_sync_after_patterns(db: Database, cfg: dict[str, Any]) -> None:
    if cfg.get("cloudflare", {}).get("auto_publish_on_patterns", False):
        sync_cloudflare_mirror(db, cfg, reason="patterns")


def maybe_sync_on_finalize(db: Database, cfg: dict[str, Any]) -> None:
    if cfg.get("cloudflare", {}).get("sync_on_finalize", True):
        sync_cloudflare_mirror(db, cfg, reason="finalize")
