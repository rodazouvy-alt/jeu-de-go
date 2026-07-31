"""Pipeline par lot — moteurs KataGo reutilises, 1 seul actif sur le GPU (12 Go)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .config import resolve_path
from .katago import KataGoAnalysis


def _print_katago_startup(engine: KataGoAnalysis) -> None:
    for line in engine.startup_log:
        low = line.lower()
        if "running with following config" in low:
            continue
        if any(
            key in line
            for key in (
                "numSearchThreads",
                "nnMaxBatchSize",
                "numAnalysisThreads",
                "cuda",
                "loaded neural net",
                "analysis engine",
            )
        ):
            print(f"  {line}", flush=True)


class _KataGoPool:
    """Reutilise les moteurs KataGo dans un lot (un seul actif a la fois sur le GPU)."""

    def __init__(
        self,
        cfg: dict[str, Any],
        *,
        katago_restart_sec: float,
        quick_config: str,
        deep_config: str,
    ) -> None:
        self.cfg = cfg
        self.katago_cfg = cfg["katago"]
        self.katago_restart_sec = katago_restart_sec
        self.quick_config = quick_config
        self.deep_config = deep_config
        self._quick: KataGoAnalysis | None = None
        self._deep: KataGoAnalysis | None = None
        self._human: KataGoAnalysis | None = None
        self._session_start: float | None = None

    def _mark_session(self) -> None:
        if self._session_start is None:
            self._session_start = time.perf_counter()

    def _maybe_restart(self) -> None:
        if not self.katago_restart_sec or self.katago_restart_sec <= 0:
            return
        if self._session_start is None:
            return
        elapsed = time.perf_counter() - self._session_start
        if elapsed < self.katago_restart_sec:
            return
        mins = int(elapsed // 60)
        print(
            f"  [KataGo] Redemarrage preventif ({mins} min ecoulees)...",
            flush=True,
        )
        self.stop_all()
        self._session_start = None
        time.sleep(3)

    def quick(self) -> KataGoAnalysis:
        self._maybe_restart()
        if self._quick is None:
            print("Démarrage KataGo (quick)...", flush=True)
            self._quick = KataGoAnalysis(self.cfg, analysis_config=self.quick_config)
            self._quick.start()
            _print_katago_startup(self._quick)
            self._mark_session()
        return self._quick

    def deep(self) -> KataGoAnalysis:
        self._maybe_restart()
        if self._deep is None:
            print("Démarrage KataGo (deep)...", flush=True)
            self._deep = KataGoAnalysis(self.cfg, analysis_config=self.deep_config)
            self._deep.start()
            _print_katago_startup(self._deep)
            self._mark_session()
        return self._deep

    def human(self) -> KataGoAnalysis | None:
        self._maybe_restart()
        hcfg = self.cfg.get("human_sl", {})
        human_model = hcfg.get("model") or self.katago_cfg.get("human_model")
        if not human_model or not Path(human_model).exists():
            return None
        if self._human is None:
            analysis_config = hcfg.get("config", "config/katago_human.cfg")
            print("Démarrage KataGo (Human SL)...", flush=True)
            self._human = KataGoAnalysis(
                self.cfg,
                analysis_config=analysis_config,
                human_model=human_model,
            )
            self._human.start()
            _print_katago_startup(self._human)
            self._mark_session()
        return self._human

    def stop_quick(self) -> None:
        if self._quick is not None:
            self._quick.stop()
            self._quick = None

    def stop_deep(self) -> None:
        if self._deep is not None:
            self._deep.stop()
            self._deep = None

    def stop_all(self) -> None:
        for eng in (self._quick, self._deep, self._human):
            if eng is not None:
                eng.stop()
        self._quick = None
        self._deep = None
        self._human = None


def _post_quick_pass(analyzer, pool: _KataGoPool, game_id: int, sgf_path: Path) -> None:
    g_row = analyzer.db.conn.execute(
        "SELECT analyzed_opening, analyzed_deep FROM games WHERE id = ?",
        (game_id,),
    ).fetchone()
    if not g_row or not g_row["analyzed_opening"]:
        print("  -> passe fuseki F6...", flush=True)
        analyzer._run_fuseki_pass(game_id, sgf_path, pool.deep())
    pool.stop_deep()
    g_row = analyzer.db.conn.execute(
        "SELECT analyzed_deep FROM games WHERE id = ?",
        (game_id,),
    ).fetchone()
    if analyzer._deep_middle_enabled():
        analyzer._analyze_deep(pool.deep(), game_id, sgf_path)
        pool.stop_deep()
    elif not g_row or not g_row["analyzed_deep"]:
        print("  -> deep milieu desactive (quick conserve)", flush=True)
        analyzer._complete_full_analysis(game_id)
    print("  -> Human SL + export...", flush=True)
    analyzer._enrich_after_analysis(game_id, pool.human())


def run_pipelined_batch(analyzer, pending: list, *, katago_restart_sec: float) -> int:
    """Quick en rafale puis fuseki+SL — pas de 2 processus CUDA en meme temps."""
    kcfg = analyzer.katago_cfg
    quick_cfg = kcfg.get("quick_config_parallel") or kcfg["config"]
    deep_cfg = kcfg.get("deep_config_parallel") or kcfg.get("deep_config", quick_cfg)

    pool = _KataGoPool(
        analyzer.cfg,
        katago_restart_sec=katago_restart_sec,
        quick_config=quick_cfg,
        deep_config=deep_cfg,
    )
    lot_size = len(pending)
    analyzed = 0

    print(
        f"  Pipeline lot optimise : quick x{lot_size} puis fuseki+SL "
        f"(1 moteur GPU actif — evite blocages CUDA 12 Go)",
        flush=True,
    )

    if analyzer.auto_report:
        analyzer._refresh_report()

    try:
        print(f"\n--- Phase quick ({lot_size} partie(s)) ---", flush=True)
        for i, row in enumerate(pending, 1):
            game_id = row["id"]
            sgf_path = resolve_path(row["sgf_path"])
            print(
                f"\n[Quick] partie #{game_id} ({i}/{lot_size}): {sgf_path.name}",
                flush=True,
            )
            if not row["analyzed_quick"]:
                print("  -> pass quick...", flush=True)
                try:
                    analyzer._analyze_one(
                        pool.quick(),
                        game_id,
                        sgf_path,
                        kcfg["quick_max_visits"],
                        "quick",
                    )
                except Exception as exc:
                    print(
                        f"  ERREUR quick partie #{game_id}: "
                        f"{analyzer._format_analysis_error(exc)}",
                        flush=True,
                    )
                    pool.stop_quick()
                    if analyzer._is_unrecoverable_analysis_error(exc):
                        analyzer._skip_broken_game(game_id, exc)
                    continue
            else:
                print("  -> quick deja fait", flush=True)

        pool.stop_quick()

        print(f"\n--- Phase fuseki + Human SL ({lot_size} partie(s)) ---", flush=True)
        for i, row in enumerate(pending, 1):
            game_id = row["id"]
            sgf_path = resolve_path(row["sgf_path"])
            skip_row = analyzer.db.conn.execute(
                "SELECT analysis_skip_reason FROM games WHERE id = ?",
                (game_id,),
            ).fetchone()
            if skip_row and skip_row["analysis_skip_reason"]:
                print(
                    f"\n[Post] partie #{game_id} ignoree : "
                    f"{skip_row['analysis_skip_reason']}",
                    flush=True,
                )
                analyzed += 1
                continue
            g_done = analyzer.db.conn.execute(
                "SELECT analyzed_deep FROM games WHERE id = ?",
                (game_id,),
            ).fetchone()
            if g_done and g_done["analyzed_deep"]:
                print(f"\n[Post] partie #{game_id} deja terminee, ignoree", flush=True)
                analyzed += 1
                continue
            print(
                f"\n[Post] partie #{game_id} ({i}/{lot_size}): fuseki + Human SL",
                flush=True,
            )
            try:
                _post_quick_pass(analyzer, pool, game_id, sgf_path)
                analyzed += 1
                print(
                    f"  Partie #{game_id} terminee ({analyzed}/{lot_size} du lot).",
                    flush=True,
                )
            except Exception as exc:
                print(f"  ERREUR post partie #{game_id}: {exc}", flush=True)
                pool.stop_deep()
                if analyzer._is_unrecoverable_analysis_error(exc):
                    analyzer._skip_broken_game(game_id, exc)
                    analyzed += 1
    finally:
        pool.stop_all()

    return analyzed
