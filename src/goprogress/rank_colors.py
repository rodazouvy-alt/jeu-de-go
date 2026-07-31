"""Couleurs diagramme / légende pour les rangs Human SL."""
from __future__ import annotations

from .human_sl import RANK_LABELS, RANK_ORDER

IA_BORDER_DEFAULT = "#15803d"
SL_COLOR = "#78350f"

# Du plus fort au plus faible
RANK_STRENGTH: tuple[str, ...] = (
    "23", "20", "9", "8", "7", "6", "5", "4", "3",
)

# Pourtour mince sur le plateau (couleur du rang le plus fort)
RANK_RING_BORDER: dict[str, str] = {
    "23": "#0a0a0a",   # noir — pro 23
    "20": "#dc2626",   # rouge — pro 20
    "9": "#f8fafc",    # blanc — 9d
    "8": "#92400e",    # marron — 8d
    "7": "#1d4ed8",    # bleu — 7d
    "6": "#0d9488",    # turquoise — 6d
    "5": "#16a34a",    # vert — 5d
    "4": "#ea580c",    # orange — 4d
    "3": "#ca8a04",    # ambre — 3d
}

# Légende : pastilles pleines (fond, texte)
RANK_STYLES: dict[str, tuple[str, str]] = {
    "3": ("#eab308", "#422006"),
    "3d": ("#eab308", "#422006"),
    "4": ("#f97316", "#ffffff"),
    "4d": ("#f97316", "#ffffff"),
    "5": ("#22c55e", "#ffffff"),
    "5d": ("#22c55e", "#ffffff"),
    "6": ("#14b8a6", "#ffffff"),
    "6d": ("#14b8a6", "#ffffff"),
    "7": ("#3b82f6", "#ffffff"),
    "7d": ("#3b82f6", "#ffffff"),
    "8": ("#92400e", "#ffffff"),
    "8d": ("#92400e", "#ffffff"),
    "9": ("#f4f4f5", "#18181b"),
    "9d": ("#f4f4f5", "#18181b"),
    "20": ("#dc2626", "#ffffff"),
    "23": ("#18181b", "#ffffff"),
    "pro 20": ("#dc2626", "#ffffff"),
    "pro 23": ("#18181b", "#ffffff"),
    "SL": ("#78350f", "#ffffff"),
}

# Texte lisible sur fond crème (pastilles plateau)
RANK_TEXT_ON_LIGHT: dict[str, str] = {
    "23": "#0a0a0a",
    "20": "#b91c1c",
    "9": "#18181b",
    "8": "#78350f",
    "7": "#1d4ed8",
    "6": "#0f766e",
    "5": "#15803d",
    "4": "#c2410c",
    "3": "#a16207",
}

RANK_KEY_TO_NORM: dict[str, str] = {
    "rank_3d": "3", "rank_4d": "4", "rank_5d": "5", "rank_6d": "6",
    "rank_7d": "7", "rank_8d": "8", "rank_9d": "9",
    "proyear_2020": "20", "proyear_2023": "23",
}


def rank_diagram_label(rank_key: str) -> str:
    return rank_display_label(RANK_LABELS.get(rank_key, rank_key.replace("rank_", "")))


def rank_display_label(label: str) -> str:
    """Libellé affiché partout (dashboard + diagrammes)."""
    if not label or label == "SL":
        return "SL"
    norm = _norm_rank_label(label)
    if norm == "23":
        return "pro 23"
    if norm == "20":
        return "pro 20"
    if norm.isdigit() and len(norm) == 1:
        return f"{norm}d"
    if label.endswith("d") or label.startswith("pro "):
        return label
    return label


def _norm_rank_label(label: str) -> str:
    if label == "SL":
        return "SL"
    low = label.lower().strip()
    if low.startswith("pro"):
        digits = "".join(c for c in label if c.isdigit())
        if digits in ("23", "2023"):
            return "23"
        if digits in ("20", "2020"):
            return "20"
    norm = label.replace("d", "").replace("+", "").strip()
    if norm.startswith("p"):
        return "20" if "20" in norm else "23"
    if norm.isdigit():
        return norm
    return norm


def rank_text_on_light(label: str) -> str:
    """Couleur de texte lisible sur fond crème."""
    return RANK_TEXT_ON_LIGHT.get(_norm_rank_label(label), "#334155")


def rank_swatch_style(label: str) -> tuple[str, str]:
    if label in RANK_STYLES:
        return RANK_STYLES[label]
    norm = _norm_rank_label(label)
    if norm in RANK_STYLES:
        return RANK_STYLES[norm]
    key = f"{norm}d" if norm.isdigit() and len(norm) == 1 else norm
    if key in RANK_STYLES:
        return RANK_STYLES[key]
    return ("#64748b", "#ffffff")


def rank_ring_border(label: str) -> str:
    """Pourtour mince = couleur du joueur le plus fort."""
    norm = _norm_rank_label(label)
    if norm == "SL":
        return SL_COLOR
    return RANK_RING_BORDER.get(norm, IA_BORDER_DEFAULT)


def strongest_rank_label(labels: list[str]) -> str:
    if not labels:
        return "SL"
    norms = {_norm_rank_label(lbl): lbl for lbl in labels}
    for strength in RANK_STRENGTH:
        if strength in norms:
            return norms[strength]
    return labels[0]


def strongest_rank_border(labels: list[str]) -> str:
    if not labels:
        return IA_BORDER_DEFAULT
    return rank_ring_border(strongest_rank_label(labels))


def rank_label_color(label: str) -> str:
    return rank_ring_border(label)


def sort_ranks_by_strength(labels: list[str]) -> list[str]:
    order = {s: i for i, s in enumerate(RANK_STRENGTH)}
    return sorted(labels, key=lambda lbl: order.get(_norm_rank_label(lbl), 99))


def rank_compact_label(label: str) -> str:
    """Libellé court pour mini-pastilles sur le plateau."""
    norm = _norm_rank_label(label)
    if norm in ("20", "23"):
        return norm
    if norm.isdigit():
        return norm
    return label[:3]


_PRO_RANK_KEYS = frozenset({"proyear_2020", "proyear_2023"})


def human_consensus_label(
    ranks_same: list[str],
    human_ranks: dict[str, str] | None,
) -> str | None:
    """Libellé légende : consensus, consensus hors pro, ou N profils."""
    if not ranks_same:
        return None
    same = set(ranks_same)
    if not human_ranks:
        return f"{len(ranks_same)} profils" if len(ranks_same) > 1 else None

    dan_labels: list[str] = []
    pro_labels: list[str] = []
    for rank_key in RANK_ORDER:
        if rank_key not in human_ranks:
            continue
        gtp = (human_ranks[rank_key] or "").upper().strip()
        if not gtp or gtp == "PASS":
            continue
        lbl = rank_diagram_label(rank_key)
        if rank_key in _PRO_RANK_KEYS:
            pro_labels.append(lbl)
        else:
            dan_labels.append(lbl)

    active = dan_labels + pro_labels
    if active and len(same) == len(active) and all(lbl in same for lbl in active):
        return "consensus"

    dan_in = [lbl for lbl in dan_labels if lbl in same]
    pro_in = [lbl for lbl in pro_labels if lbl in same]
    if (
        dan_labels
        and len(dan_in) == len(dan_labels)
        and pro_labels
        and len(pro_in) < len(pro_labels)
    ):
        return "consensus hors pro"

    if len(ranks_same) > 1:
        return f"{len(ranks_same)} profils"
    return None


def katago_border_color(rank_labels: list[str]) -> str:
    """Pourtour mince des pastilles IA selon le rang humain le plus fort sur ce coup."""
    return strongest_rank_border(rank_labels)


def ranks_by_gtp(human_ranks: dict[str, str] | None) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    if not human_ranks:
        return out
    for rank_key in RANK_ORDER:
        gtp = human_ranks.get(rank_key)
        if not gtp:
            continue
        key = gtp.upper().strip()
        if not key or key == "PASS":
            continue
        out.setdefault(key, []).append(rank_diagram_label(rank_key))
    return out
