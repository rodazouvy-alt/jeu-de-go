from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import resolve_path

GOGOD_ZIP_GLOB = "*Database-Jan2026.zip"
META_ZIPS = (
    "Database_ReadMeFirst_20251111_2252.zip",
    "Config.txt.zip",
    "GameData.zip",
    "NamesXML-XSD.zip",
)


def _default_downloads_dir() -> Path:
    return Path.home() / "Downloads"


def _gogod_paths(cfg: dict[str, Any]) -> dict[str, Path]:
    pro = cfg.get("pro_corpus", {}).get("gogod", {})
    return {
        "downloads": Path(pro.get("downloads_dir", _default_downloads_dir())),
        "sgf": resolve_path(pro.get("sgf_dir", "data/pro_corpus/gogod/sgf")),
        "meta": resolve_path(pro.get("meta_dir", "data/pro_corpus/gogod/meta")),
        "manifest": resolve_path(
            pro.get("manifest_path", "data/pro_corpus/gogod/manifest.json")
        ),
    }


def _extract_zip(zip_path: Path, dest: Path) -> int:
    dest.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            out = dest / info.filename
            out.parent.mkdir(parents=True, exist_ok=True)
            if out.exists() and out.stat().st_size == info.file_size:
                count += 1
                continue
            with zf.open(info) as src, out.open("wb") as dst:
                dst.write(src.read())
            count += 1
    return count


def import_gogod(
    cfg: dict[str, Any],
    *,
    downloads_dir: Path | None = None,
    skip_extract: bool = False,
) -> dict[str, Any]:
    paths = _gogod_paths(cfg)
    dl = downloads_dir or paths["downloads"]
    if not dl.exists():
        raise FileNotFoundError(f"Dossier introuvable : {dl}")

    db_zips = sorted(dl.glob(GOGOD_ZIP_GLOB))
    if not db_zips:
        raise FileNotFoundError(
            f"Aucun zip GoGoD (*Database-Jan2026.zip) dans {dl}"
        )

    stats: dict[str, Any] = {
        "archives": [],
        "meta_archives": [],
        "sgf_files": 0,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
    }

    if not skip_extract:
        print(f"Extraction vers {paths['sgf']} …", flush=True)
        for zp in db_zips:
            print(f"  {zp.name} …", flush=True)
            n = _extract_zip(zp, paths["sgf"])
            stats["archives"].append({"file": zp.name, "entries": n})
            print(f"    -> {n} fichiers", flush=True)

        paths["meta"].mkdir(parents=True, exist_ok=True)
        for name in META_ZIPS:
            zp = dl / name
            if zp.exists():
                n = _extract_zip(zp, paths["meta"])
                stats["meta_archives"].append({"file": name, "entries": n})

    sgf_count = sum(1 for _ in paths["sgf"].rglob("*.sgf"))
    stats["sgf_files"] = sgf_count
    stats["sgf_dir"] = str(paths["sgf"])

    paths["manifest"].parent.mkdir(parents=True, exist_ok=True)
    paths["manifest"].write_text(
        json.dumps(stats, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\nTerminé : {sgf_count:,} fichiers SGF pro indexés.", flush=True)
    print(f"Manifeste : {paths['manifest']}", flush=True)
    return stats
