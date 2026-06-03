"""
extract_ground.py
=================
Ekstrakcja punktow klasy 2 (ASPRS ground) z odfiltrowanej chmury LAZ.
Drugi krok pipeline-u — uruchom po lidar_cleanup.py.

Uruchomienie:
    python extract_ground.py --input processed/78142_filtered.laz
"""
from __future__ import annotations

import argparse
from pathlib import Path

import laspy
import numpy as np


def extract_class(
    input_path: Path,
    output_path: Path,
    asprs_class: int = 2,
) -> tuple[int, int]:
    """
    Filtruje chmure do wybranej klasy ASPRS.
    Zwraca (n_przed, n_po).
    """
    las = laspy.read(str(input_path))
    n_before = len(las.x)

    mask = np.asarray(las.classification) == asprs_class
    if not mask.any():
        raise ValueError(
            f"Brak punktow klasy {asprs_class} w pliku {input_path}. "
            f"Dostepne klasy: {np.unique(np.asarray(las.classification)).tolist()}"
        )

    las_out = las[mask]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    las_out.write(str(output_path))

    return n_before, int(mask.sum())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ekstrakcja ground-only (klasa 2 ASPRS) z chmury LAZ.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--input", type=Path, required=True,
        help="Wejsciowy plik LAZ (po lidar_cleanup.py).",
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Wyjsciowy plik LAZ. Domyslnie: <input>_ground.laz.",
    )
    parser.add_argument(
        "--class", dest="asprs_class", type=int, default=2,
        help="Klasa ASPRS do ekstrakcji (domyslnie 2 = ground).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not args.input.exists():
        raise FileNotFoundError(f"Brak pliku: {args.input}")

    output = args.output or args.input.with_name(
        args.input.stem + "_ground" + args.input.suffix
    )

    print(f"Wejscie:  {args.input}")
    print(f"Wyjscie:  {output}")
    print(f"Klasa:    {args.asprs_class} (ASPRS)")

    n_before, n_after = extract_class(args.input, output, args.asprs_class)
    pct = n_after / n_before * 100

    print(f"Przed:    {n_before:,} punktow")
    print(f"Po:       {n_after:,} punktow ground ({pct:.1f}% calej chmury)")
    print(f"Zapisano: {output.resolve()}")


if __name__ == "__main__":
    main()
