"""Nakladanie ortofotomapy na siatke terenu jako tekstury (UV projection)."""

from mtb_terrain.texture.pipeline import (
    run_pipeline,
    load_ortho_bounds,
    compute_planar_uv,
    process_mesh,
)

__all__ = [
    "run_pipeline",
    "load_ortho_bounds",
    "compute_planar_uv",
    "process_mesh",
]