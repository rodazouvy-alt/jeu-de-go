from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT_DIR = Path(__file__).resolve().parents[2]


def load_config() -> dict[str, Any]:
    path = ROOT_DIR / "config.yaml"
    local = ROOT_DIR / "config.local.yaml"
    with path.open(encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if local.exists():
        with local.open(encoding="utf-8") as f:
            override = yaml.safe_load(f) or {}
        cfg = _deep_merge(cfg, override)
    return cfg


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def resolve_path(relative: str) -> Path:
    return (ROOT_DIR / relative).resolve()


def ensure_data_dirs(cfg: dict[str, Any]) -> None:
    for key in ("sgf_dir", "analysis_dir", "reports_dir"):
        resolve_path(cfg["paths"][key]).mkdir(parents=True, exist_ok=True)
    review_dir = cfg.get("review", {}).get("output_dir", "data/review_sgf")
    resolve_path(review_dir).mkdir(parents=True, exist_ok=True)
    resolve_path(cfg["paths"]["db_path"]).parent.mkdir(parents=True, exist_ok=True)
