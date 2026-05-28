"""
lidar_cleanup.py
================
Filtracja szumow na poziomie chmury punktow (SOR + ROR + usuniecie klasy 7).
Pierwsza faza pipeline-u — uruchom PRZED gps_lidar_int_delaunay.py.

Uruchomienie:
    python lidar_cleanup.py --input data/lidar/78142_1411124_M-34-89-C-c-2-3-2.laz
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def pdal_pre_cleanup(
    input_path: Path,
    output_path: Path,
    sor_mean_k: int = 12,
    sor_multiplier: float = 2.2,
    ror_radius: float = 1.0,
    ror_min_neighbors: int = 4,
) -> bool:
    """
    Filtracja szumow chmury punktow przez PDAL:
      - Statistical Outlier Removal (SOR)
      - Radius Outlier Removal (ROR)
      - Usuniecie klasy 7 (noise ASPRS)
    Zwraca True jesli sukces, False jesli PDAL niedostepny.
    """
    try:
        import pdal
    except ImportError:
        print("PDAL niedostepny. Zainstaluj: conda install -c conda-forge pdal python-pdal")
        return False

    pipeline_json = {
        "pipeline": [
            str(input_path),
            {
                "type": "filters.outlier",
                "method": "statistical",
                "mean_k": sor_mean_k,
                "multiplier": sor_multiplier,
            },
            {
                "type": "filters.outlier",
                "method": "radius",
                "radius": ror_radius,
                "min_k": ror_min_neighbors,
            },
            {
                "type": "filters.range",
                "limits": "Classification![7:7]",
            },
            str(output_path),
        ]
    }

    pipeline = pdal.Pipeline(json.dumps(pipeline_json))
    n_points = pipeline.execute()
    print(f"PDAL: pozostalo {n_points:,} punktow po SOR + ROR.")
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Filtracja szumow LiDAR przez PDAL (SOR + ROR).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--input", type=Path, required=True,
        help="Sciezka do pelnej chmury LAZ/LAS.",
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Sciezka wyjsciowa. Domyslnie: <input>_filtered.laz.",
    )
    parser.add_argument("--sor-mean-k", type=int, default=12)
    parser.add_argument("--sor-multiplier", type=float, default=2.2)
    parser.add_argument("--ror-radius", type=float, default=1.0)
    parser.add_argument("--ror-min-neighbors", type=int, default=4)
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not args.input.exists():
        raise FileNotFoundError(f"Brak pliku: {args.input}")

    output = args.output or args.input.with_name(
        args.input.stem + "_filtered" + args.input.suffix
    )

    n_before = None
    try:
        import laspy
        las = laspy.read(str(args.input))
        n_before = len(las.x)
        print(f"Punktow przed filtracja: {n_before:,}")
    except ImportError:
        pass

    print(f"Wejscie: {args.input}")
    print(f"Wyjscie: {output}")
    print()

    success = pdal_pre_cleanup(
        input_path=args.input,
        output_path=output,
        sor_mean_k=args.sor_mean_k,
        sor_multiplier=args.sor_multiplier,
        ror_radius=args.ror_radius,
        ror_min_neighbors=args.ror_min_neighbors,
    )

    if success and n_before:
        try:
            import laspy
            las_out = laspy.read(str(output))
            n_after = len(las_out.x)
            removed = n_before - n_after
            print(f"Usunieto outlierow: {removed:,} ({removed / n_before * 100:.2f}%)")
        except ImportError:
            pass


if __name__ == "__main__":
    main()