from __future__ import annotations

import io
import re
import time
import zipfile
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .config import resolve_path
from .db import Database
from .sgf_parse import parse_sgf, player_color, opponent_name


class KgsSync:
    def __init__(self, cfg: dict[str, Any], db: Database):
        self.cfg = cfg
        self.db = db
        self.username = cfg["player"]["kgs_username"]
        self.base_url = cfg["kgs"]["base_url"].rstrip("/")
        self.files_url = cfg["kgs"]["files_url"].rstrip("/")
        self.delay = cfg["kgs"]["request_delay_seconds"]
        self.sgf_dir = resolve_path(cfg["paths"]["sgf_dir"])
        self.client = httpx.Client(
            timeout=60.0,
            headers={"User-Agent": "GoProgress/0.1 (personal study tool)"},
            follow_redirects=True,
        )

    def close(self) -> None:
        self.client.close()

    def _sleep(self) -> None:
        time.sleep(self.delay)

    def list_months(self) -> list[tuple[int, int]]:
        """Découvre les mois disponibles depuis la page archives."""
        url = (
            f"{self.base_url}/gameArchives.jsp"
            f"?user={self.username}&allAccounts=on"
        )
        self._sleep()
        html = self.client.get(url).text
        pairs = set(re.findall(
            rf"user={re.escape(self.username)}&amp;year=(\d{{4}})&amp;month=(\d{{1,2}})",
            html,
        ))
        months = sorted((int(y), int(m)) for y, m in pairs)
        return months

    def fetch_month_page(self, year: int, month: int) -> str:
        url = (
            f"{self.base_url}/gameArchives.jsp"
            f"?user={self.username}&year={year}&month={month}&allAccounts=on"
        )
        self._sleep()
        return self.client.get(url).text

    def parse_games_from_html(self, html: str, year: int, month: int) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
        games: list[dict[str, Any]] = []

        rows = soup.find_all("tr")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 7:
                continue
            sgf_link = row.find("a", href=re.compile(r"files\.gokgs\.com.*\.sgf", re.I))
            if not sgf_link:
                continue

            href = unescape(sgf_link["href"])
            if href.startswith("http"):
                kgs_url = href
            else:
                kgs_url = href if href.startswith("https://") else f"{self.files_url}/{href.lstrip('/')}"

            white = cells[1].get_text(" ", strip=True)
            black = cells[2].get_text(" ", strip=True)
            setup = cells[3].get_text(" ", strip=True)
            start_time = cells[4].get_text(" ", strip=True)
            result = cells[6].get_text(" ", strip=True)

            if "19" not in setup and self.cfg["kgs"].get("board_size_filter") == 19:
                continue

            handicap = 0
            if "H" in setup:
                m = re.search(r"H(\d+)", setup)
                if m:
                    handicap = int(m.group(1))

            games.append({
                "kgs_url": kgs_url,
                "year": year,
                "month": month,
                "white": white,
                "black": black,
                "setup": setup,
                "played_at": start_time,
                "result": result,
                "handicap": handicap,
                "board_size": 19,
            })
        return games

    def download_sgf(self, kgs_url: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and dest.stat().st_size > 0:
            return dest
        self._sleep()
        resp = self.client.get(kgs_url)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
        return dest

    def sgf_dest_path(self, kgs_url: str, year: int, month: int) -> Path:
        filename = kgs_url.rsplit("/", 1)[-1]
        return self.sgf_dir / str(year) / f"{month:02d}" / filename

    def sync_month(self, year: int, month: int, limit: int | None = None) -> int:
        html = self.fetch_month_page(year, month)
        games = self.parse_games_from_html(html, year, month)
        if limit:
            games = games[:limit]

        count = 0
        now = datetime.now(timezone.utc).isoformat()
        for meta in games:
            dest = self.sgf_dest_path(meta["kgs_url"], year, month)
            try:
                self.download_sgf(meta["kgs_url"], dest)
            except httpx.HTTPError as exc:
                print(f"  ERREUR téléchargement {meta['kgs_url']}: {exc}")
                continue

            try:
                parsed = parse_sgf(dest)
                pcolor = player_color(parsed, self.username)
                opponent = opponent_name(parsed, self.username)
            except Exception:
                pcolor = None
                opponent = "?"

            game_id = self.db.upsert_game({
                "kgs_url": meta["kgs_url"],
                "sgf_path": str(dest.relative_to(resolve_path("."))),
                "played_at": meta["played_at"],
                "year": year,
                "month": month,
                "black": meta["black"],
                "white": meta["white"],
                "player_color": pcolor,
                "opponent": opponent,
                "result": meta["result"],
                "handicap": meta["handicap"],
                "board_size": meta["board_size"],
                "downloaded_at": now,
            })
            count += 1
            print(f"  [{count}] partie #{game_id} -> {dest.name}")
        return count

    def sync_month_zip(self, year: int, month: int) -> int:
        """Télécharge l'archive ZIP mensuelle KGS (plus rapide en masse)."""
        zip_url = (
            f"{self.base_url}/servlet/archives/en_US/"
            f"{self.username}-{year}-{month}.zip"
        )
        print(f"Téléchargement archive {year}-{month:02d}...")
        self._sleep()
        resp = self.client.get(zip_url)
        resp.raise_for_status()

        count = 0
        now = datetime.now(timezone.utc).isoformat()
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            for name in zf.namelist():
                if not name.lower().endswith(".sgf"):
                    continue
                dest = self.sgf_dir / str(year) / f"{month:02d}" / Path(name).name
                dest.parent.mkdir(parents=True, exist_ok=True)
                if not dest.exists():
                    dest.write_bytes(zf.read(name))

                kgs_url = f"{self.files_url}/games/{year}/{month}/{dest.name}"
                try:
                    parsed = parse_sgf(dest)
                    if parsed.board_size != 19:
                        continue
                    pcolor = player_color(parsed, self.username)
                    opponent = opponent_name(parsed, self.username)
                except Exception:
                    continue

                self.db.upsert_game({
                    "kgs_url": kgs_url,
                    "sgf_path": str(dest.relative_to(resolve_path("."))),
                    "played_at": None,
                    "year": year,
                    "month": month,
                    "black": parsed.black,
                    "white": parsed.white,
                    "player_color": pcolor,
                    "opponent": opponent,
                    "result": parsed.result,
                    "handicap": parsed.handicap,
                    "board_size": parsed.board_size,
                    "downloaded_at": now,
                })
                count += 1
        print(f"  {count} parties indexées depuis le ZIP")
        return count

    def sync_recent(self, limit: int = 10) -> int:
        """Sync rapide : les N dernières parties du mois courant."""
        now = datetime.now()
        return self.sync_month(now.year, now.month, limit=limit)
