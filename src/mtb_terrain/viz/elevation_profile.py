from __future__ import annotations

from pathlib import Path

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np

_LEGEND_ENTRIES = [
    ((0.133, 0.773, 0.369), "Łagodny (0–5%)"),
    ((0.992, 0.878, 0.278), "Umiarkowany (5–10%)"),
    ((0.976, 0.451, 0.086), "Stromy (10–20%)"),
    ((0.937, 0.267, 0.267), "Bardzo stromy (>20%)"),
]

_BG_DARK = "#0f172a"
_FIG_BG = "#1e293b"


def compute_distances_km(gps_points: np.ndarray) -> np.ndarray:
    """Return (N,) cumulative XY distances in km."""
    dxy = np.sqrt(np.sum(np.diff(gps_points[:, :2], axis=0) ** 2, axis=1))
    return np.concatenate([[0.0], np.cumsum(dxy)]) / 1000.0


def show_elevation_profile(
    gps_points: np.ndarray,
    slope_colors: np.ndarray,
    save_path: Path | None = None,
) -> None:
    distances = compute_distances_km(gps_points)
    elevations = gps_points[:, 2]

    fig, ax = plt.subplots(figsize=(12, 4))
    fig.patch.set_facecolor(_FIG_BG)
    ax.set_facecolor(_BG_DARK)

    for i, color in enumerate(slope_colors):
        ax.fill_between(
            distances[i : i + 2],
            elevations[i : i + 2],
            alpha=0.85,
            color=color,
            linewidth=0,
        )

    ax.plot(distances, elevations, color="white", linewidth=1.0, alpha=0.9)

    patches = [mpatches.Patch(color=color, label=label) for color, label in _LEGEND_ENTRIES]
    ax.legend(
        handles=patches,
        loc="upper left",
        fontsize=8,
        facecolor=_BG_DARK,
        labelcolor="white",
        edgecolor="#444",
    )
    ax.set_xlabel("Dystans [km]", color="white")
    ax.set_ylabel("Wysokość [m n.p.m.]", color="white")
    ax.set_title("Profil wysokościowy trasy MTB", color="white")
    ax.grid(True, alpha=0.3, color="#555")
    ax.tick_params(colors="white")
    for spine in ax.spines.values():
        spine.set_edgecolor("#444")

    if save_path is not None:
        fig.savefig(
            str(save_path),
            dpi=150,
            bbox_inches="tight",
            facecolor=fig.get_facecolor(),
        )

    plt.show(block=False)
