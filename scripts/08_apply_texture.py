"""
08_apply_texture.py
==================
Nakladanie ortofotomapy na siatki LOD jako tekstury (UV projection).
Osmy krok pipeline-u — po 06_process_ortho.py i 05_generate_lod.py.

Uruchomienie:
    python scripts/08_apply_texture.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Dodaj src/ do sciezki importow
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mtb_terrain.texture.pipeline import run_pipeline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Nakladanie ortofotomapy na siatke jako tekstury (UV projection).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--mesh", nargs="+", type=Path,
        default=[Path("results/lod/filled/mesh_LOD0_unity_filled.obj")],
        help="Plik(i) OBJ siatki.",
    )
    parser.add_argument(
        "--ortho-report", type=Path,
        default=Path("results/ortho/ortho_report.json"),
    )
    parser.add_argument(
        "--ortho-image", type=Path,
        default=Path("results/ortho/ortho.png"),
    )
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path("results/textured"),
    )
    parser.add_argument(
        "--world-bounds", action="store_true",
        help="Uzyj world_bounds_epsg2180 zamiast unity_bounds.",
    )
    parser.add_argument(
        "--vertical-axis", choices=["y", "z"], default="z",
        help="Os wysokosci w OBJ. Twoje pliki Open3D maja Z w gore.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    missing = [p for p in args.mesh if not p.exists()]
    if missing:
        raise FileNotFoundError(f"Brak plikow mesh: {missing}")
    if not args.ortho_report.exists():
        raise FileNotFoundError(f"Brak ortho_report: {args.ortho_report}")
    if not args.ortho_image.exists():
        raise FileNotFoundError(f"Brak ortho_image: {args.ortho_image}")

    run_pipeline(
        mesh_paths=args.mesh,
        ortho_report_path=args.ortho_report,
        ortho_image_path=args.ortho_image,
        output_dir=args.output_dir,
        use_unity_bounds=not args.world_bounds,
        vertical_axis=args.vertical_axis,
    )


if __name__ == "__main__":
    main()