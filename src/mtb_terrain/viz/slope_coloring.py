from __future__ import annotations
import numpy as np

_SLOPE_CLASSES: list[tuple[float, tuple[float, float, float]]] = [
    (5.0,          (0.133, 0.773, 0.369)),  # zielony      #22c55e
    (10.0,         (0.992, 0.878, 0.278)),  # żółty        #fde047
    (20.0,         (0.976, 0.451, 0.086)),  # pomarańczowy #f97316
    (float("inf"), (0.937, 0.267, 0.267)),  # czerwony     #ef4444
]


def compute_slope_percent(gps_points: np.ndarray) -> np.ndarray:
    """Return (N-1,) slope in percent for each segment between consecutive GPS points."""
    dxy = np.sqrt(np.sum(np.diff(gps_points[:, :2], axis=0) ** 2, axis=1))
    dxy = np.where(dxy < 1e-9, 1e-9, dxy)
    dz = np.abs(np.diff(gps_points[:, 2]))
    return dz / dxy * 100.0


def compute_slope_colors(gps_points: np.ndarray) -> np.ndarray:
    """Return (N-1, 3) RGB array with discrete slope class colors."""
    slopes = compute_slope_percent(gps_points)
    colors = np.empty((len(slopes), 3), dtype=float)
    for i, slope in enumerate(slopes):
        for threshold, color in _SLOPE_CLASSES:
            if slope < threshold:
                colors[i] = color
                break
    return colors
