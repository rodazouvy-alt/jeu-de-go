from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .config import resolve_path
from .katago import KataGoAnalysis


def _parse_cfg_value(config_text: str, key: str) -> str | None:
    prefix = f"{key} ="
    for line in config_text.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped.split("=", 1)[1].strip()
    return None


def check_katago(cfg: dict[str, Any], *, benchmark: bool = True) -> int:
    """Vérifie binaire, modèle, config CUDA et débit GPU."""
    import subprocess

    katago = cfg["katago"]
    exe = Path(katago["executable"])
    model = Path(katago["model"])
    config = resolve_path(katago["config"])
    deep_config = resolve_path(katago.get("deep_config", katago["config"]))

    print("=== Vérification KataGo ===\n")

    running = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq katago.exe"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    if "katago.exe" in running.lower():
        print("  !!  katago.exe deja actif (Lizzie ou analyse en cours)")
        print("      Fermez-le pour un benchmark fiable.\n")

    ok = True

    for label, path in (
        ("Exécutable", exe),
        ("Modèle", model),
        ("Config quick", config),
        ("Config deep", deep_config),
    ):
        if path.exists():
            print(f"  OK  {label}: {path}")
        else:
            print(f"  !!  {label} introuvable: {path}")
            ok = False

    if not ok:
        print("\nCorrigez config.yaml avant de lancer l'analyse.")
        return 1

    quick_text = config.read_text(encoding="utf-8", errors="replace")
    print("\n--- Config quick (fichier) ---")
    for key in (
        "numAnalysisThreads",
        "numSearchThreadsPerAnalysisThread",
        "nnMaxBatchSize",
        "maxVisits",
        "cudaUseFP16",
        "cudaDeviceToUse",
    ):
        val = _parse_cfg_value(quick_text, key)
        if val is not None:
            print(f"  {key} = {val}")

    print("\n--- Démarrage moteur (stderr KataGo) ---")
    engine = KataGoAnalysis(cfg)
    t0 = time.perf_counter()
    engine.start()
    startup_s = time.perf_counter() - t0

    for line in engine.startup_log:
        print(f"  {line}")

    cuda_ok = any("cuda" in line.lower() for line in engine.startup_log)
    net_ok = any("loaded neural net" in line.lower() for line in engine.startup_log)

    print(f"\n  Chargement modèle : {startup_s:.1f}s")
    print(f"  CUDA détecté      : {'oui' if cuda_ok else 'non (vérifiez pilote NVIDIA)'}")
    print(f"  Réseau chargé     : {'oui' if net_ok else 'incertain'}")

    if benchmark:
        print("\n--- Benchmark GPU (position test, 200 visits) ---")
        t1 = time.perf_counter()
        responses = engine.analyze_game(
            moves=[("B", "D4"), ("W", "Q16")],
            komi=7.5,
            max_visits=200,
            game_id="bench",
            analyze_turns=[0, 1, 2],
            timeout_seconds=120,
        )
        bench_s = time.perf_counter() - t1
        visits = 200 * len(responses)
        vps = visits / bench_s if bench_s > 0 else 0
        print(f"  {len(responses)} positions en {bench_s:.1f}s -> ~{vps:.0f} visits/s")
        if vps >= 150:
            print("  Débit : bon pour le scan rapide")
        elif vps >= 80:
            print("  Débit : acceptable (GPU partiellement utilisé)")
        else:
            print("  Débit : faible — fermez Lizzie ou vérifiez CUDA")

    engine.stop()

    print("\n--- Lecture charge GPU ---")
    print("  Le Gestionnaire des tâches affiche souvent 20-40% en « 3D ».")
    print("  C'est normal : KataGo alterne calcul CPU (arbre) et GPU (réseau).")
    print("  Référence fiable : nvidia-smi (souvent 80-100% pendant l'analyse).")

    print("\n=== KataGo prêt ===" if ok else "\n=== Problèmes détectés ===")
    return 0 if ok else 1
