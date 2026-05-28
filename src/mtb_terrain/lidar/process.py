from __future__ import annotations

import argparse
import json
from pathlib import Path


DEFAULT_DATA_DIR = Path("data")
DEFAULT_OUTPUT_DIR = Path("processed")
DEFAULT_RESULTS_DIR = Path("results")
DEFAULT_RESOLUTION_M = 1.0
DEFAULT_NODATA = -9999.0


def import_pdal():
    try:
        import pdal
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "Do klasyfikacji chmury LAZ potrzebna jest biblioteka pdal. "
            "Zainstaluj ja w aktywnym srodowisku, np. przez conda-forge: "
            "conda install -c conda-forge pdal python-pdal"
        ) from exc

    return pdal


def import_plotting_libraries():
    try:
        import matplotlib.pyplot as plt
        import numpy as np
        import rasterio
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "Do wyswietlenia DTM potrzebne sa biblioteki matplotlib, numpy i rasterio. "
            "Zainstaluj je w aktywnym srodowisku, np.: pip install matplotlib numpy rasterio"
        ) from exc

    return plt, np, rasterio


def find_default_laz(data_dir: Path) -> Path | None:
    laz_files = sorted(data_dir.rglob("*.laz"))
    return laz_files[0] if laz_files else None


def run_pdal_pipeline(stages: list[dict], description: str) -> None:
    pdal = import_pdal()
    pipeline = pdal.Pipeline(json.dumps(stages))
    point_count = pipeline.execute()
    print(f"{description}: przetworzono {point_count} punktow")


def build_ground_classification_stages(
    laz_path: Path,
    smrf_slope: float,
    smrf_window: float,
    smrf_threshold: float,
    smrf_scalar: float,
) -> list[dict]:
    return [
        {
            "type": "readers.las",
            "filename": str(laz_path),
        },
        {
            "type": "filters.smrf",
            "slope": smrf_slope,
            "window": smrf_window,
            "threshold": smrf_threshold,
            "scalar": smrf_scalar,
        },
    ]


def save_ground_laz(
    laz_path: Path,
    output_laz_path: Path,
    smrf_slope: float,
    smrf_window: float,
        _threshold: float,
    smrf_scalar: float,
) -> None:
    output_laz_path.parent.mkdir(parents=True, exist_ok=True)
    stages = build_ground_classification_stages(
        laz_path,
        smrf_slope=smrf_slope,
        smrf_window=smrf_window,
        smrf_threshold=smrf_threshold,
        smrf_scalar=smrf_scalar,
    )
    stages.extend(
        [
            {
                "type": "filters.range",
                "limits": "Classification[2:2]",
            },
            {
                "type": "writers.las",
                "filename": str(output_laz_path),
                "compression": "laszip",
            },
        ]
    )

    run_pdal_pipeline(stages, "Klasyfikacja i zapis odfiltrowanej chmury ground")


def create_dtm_from_ground_laz(
    ground_laz_path: Path,
    output_tif_path: Path,
    resolution_m: float,
    nodata: float,
) -> None:
    output_tif_path.parent.mkdir(parents=True, exist_ok=True)

    stages: list[dict] = [
        {
            "type": "readers.las",
            "filename": str(ground_laz_path),
        },
        {
            "type": "writers.gdal",
            "filename": str(output_tif_path),
            "resolution": resolution_m,
            "output_type": "mean",
            "data_type": "float32",
            "nodata": nodata,
        },
    ]

    run_pdal_pipeline(stages, "Generowanie DTM z odfiltrowanej chmury ground")


def save_classified_laz(
    laz_path: Path,
    output_laz_path: Path,
    smrf_slope: float,
    smrf_window: float,
    smrf_threshold: float,
    smrf_scalar: float,
) -> None:
    output_laz_path.parent.mkdir(parents=True, exist_ok=True)
    stages = build_ground_classification_stages(
        laz_path,
        smrf_slope=smrf_slope,
        smrf_window=smrf_window,
        smrf_threshold=smrf_threshold,
        smrf_scalar=smrf_scalar,
    )
    stages.append(
        {
            "type": "writers.las",
            "filename": str(output_laz_path),
            "compression": "laszip",
        }
    )
    run_pdal_pipeline(stages, "Zapis calej chmury z klasyfikacja ground/non-ground")


def create_dtm(
    laz_path: Path,
    output_tif_path: Path,
    resolution_m: float,
    nodata: float,
    smrf_slope: float,
    smrf_window: float,
    smrf_threshold: float,
    smrf_scalar: float,
) -> None:
    output_tif_path.parent.mkdir(parents=True, exist_ok=True)

    stages = build_ground_classification_stages(
        laz_path,
        smrf_slope=smrf_slope,
        smrf_window=smrf_window,
        smrf_threshold=smrf_threshold,
        smrf_scalar=smrf_scalar,
    )
    stages.extend(
        [
            {
                "type": "filters.range",
                "limits": "Classification[2:2]",
            },
            {
                "type": "writers.gdal",
                "filename": str(output_tif_path),
                "resolution": resolution_m,
                "output_type": "mean",
                "data_type": "float32",
                "nodata": nodata,
            },
        ]
    )

    run_pdal_pipeline(stages, "Klasyfikacja ground/non-ground i generowanie DTM")


def plot_dtm(dtm_path: Path, png_output_path: Path | None, show_plot: bool) -> None:
    plt, np, rasterio = import_plotting_libraries()

    with rasterio.open(dtm_path) as dataset:
        dtm = dataset.read(1, masked=True)
        bounds = dataset.bounds
        crs = dataset.crs

    valid_values = dtm.compressed()
    if valid_values.size == 0:
        raise ValueError("Wygenerowany DTM nie zawiera poprawnych pikseli.")

    fig, ax = plt.subplots(figsize=(10, 8), constrained_layout=True)
    image = ax.imshow(
        dtm,
        cmap="terrain",
        extent=[bounds.left, bounds.right, bounds.bottom, bounds.top],
        origin="upper",
    )
    min_height = float(np.nanmin(valid_values))
    max_height = float(np.nanmax(valid_values))
    contour_step = max((max_height - min_height) / 12, 0.5)
    contour_levels = np.arange(min_height, max_height, contour_step)
    if contour_levels.size > 1:
        ax.contour(
            dtm,
            levels=contour_levels,
            colors="black",
            alpha=0.25,
            linewidths=0.5,
            extent=[bounds.left, bounds.right, bounds.bottom, bounds.top],
        )
    fig.colorbar(image, ax=ax, label="Wysokosc [m]")
    ax.set_title("DTM - goly model terenu")
    ax.set_xlabel("X [m]")
    ax.set_ylabel("Y [m]")
    ax.set_aspect("equal", adjustable="box")
    if crs:
        ax.text(
            0.01,
            0.01,
            str(crs),
            transform=ax.transAxes,
            fontsize=8,
            color="black",
            bbox={"facecolor": "white", "alpha": 0.7, "edgecolor": "none"},
        )

    if png_output_path is not None:
        png_output_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(png_output_path, dpi=160)
        print(f"Zapisano podglad DTM: {png_output_path.resolve()}")

    if show_plot:
        plt.show()
    else:
        plt.close(fig)


def parse_args() -> argparse.Namespace:
    default_laz = find_default_laz(DEFAULT_DATA_DIR)

    parser = argparse.ArgumentParser(
        description=(
            "Wczytuje plik LAZ, klasyfikuje punkty ground/non-ground filtrem SMRF, "
            "zapisuje odfiltrowana chmure ground w folderze processed, "
            "generuje raster DTM i wyswietla go w oknie."
        )
    )
    parser.add_argument(
        "laz",
        nargs="?",
        type=Path,
        default=default_laz,
        help="Sciezka do pliku .laz. Jezeli pominiesz, skrypt uzyje pierwszego .laz z folderu data.",
    )
    parser.add_argument(
        "--dtm",
        type=Path,
        default=None,
        help="Sciezka wynikowego GeoTIFF z DTM.",
    )
    parser.add_argument(
        "--ground-laz",
        type=Path,
        default=None,
        help="Sciezka wynikowej odfiltrowanej chmury punktow ground. Domyslnie: processed/<nazwa>_ground.laz.",
    )
    parser.add_argument(
        "--classified-laz",
        type=Path,
        default=None,
        help="Opcjonalna sciezka zapisu calej chmury LAZ z klasyfikacja SMRF.",
    )
    parser.add_argument(
        "--png",
        type=Path,
        default=None,
        help="Opcjonalna sciezka zapisu podgladu PNG.",
    )
    parser.add_argument(
        "--resolution",
        type=float,
        default=DEFAULT_RESOLUTION_M,
        help="Rozdzielczosc DTM w metrach.",
    )
    parser.add_argument(
        "--nodata",
        type=float,
        default=DEFAULT_NODATA,
        help="Wartosc NoData w wynikowym rastrze.",
    )
    parser.add_argument(
        "--smrf-slope",
        type=float,
        default=0.2,
        help="Parametr slope filtra SMRF.",
    )
    parser.add_argument(
        "--smrf-window",
        type=float,
        default=16.0,
        help="Parametr window filtra SMRF w metrach.",
    )
    parser.add_argument(
        "--smrf-threshold",
        type=float,
        default=0.45,
        help="Parametr threshold filtra SMRF w metrach.",
    )
    parser.add_argument(
        "--smrf-scalar",
        type=float,
        default=1.2,
        help="Parametr scalar filtra SMRF.",
    )
    parser.add_argument(
        "--no-show",
        action="store_true",
        help="Nie otwieraj okna z DTM, tylko wygeneruj pliki wynikowe.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.laz is None:
        raise FileNotFoundError("Nie podano pliku LAZ i nie znaleziono zadnego .laz w folderze data.")
    if not args.laz.exists():
        raise FileNotFoundError(f"Nie znaleziono pliku LAZ: {args.laz}")
    if args.resolution <= 0:
        raise ValueError("Rozdzielczosc DTM musi byc wieksza od zera.")

    ground_laz_path = args.ground_laz or DEFAULT_OUTPUT_DIR / f"{args.laz.stem}_ground.laz"
    dtm_path = args.dtm or DEFAULT_OUTPUT_DIR / f"{args.laz.stem}_dtm.tif"
    png_path = args.png if args.png is not None else DEFAULT_RESULTS_DIR / f"{args.laz.stem}_dtm.png"

    print(f"Plik LAZ: {args.laz.resolve()}")
    print(f"Rozdzielczosc DTM: {args.resolution} m")

    save_ground_laz(
        laz_path=args.laz,
        output_laz_path=ground_laz_path,
        smrf_slope=args.smrf_slope,
        smrf_window=args.smrf_window,
        smrf_threshold=args.smrf_threshold,
        smrf_scalar=args.smrf_scalar,
    )
    print(f"Zapisano odfiltrowana chmure ground: {ground_laz_path.resolve()}")

    create_dtm_from_ground_laz(
        ground_laz_path=ground_laz_path,
        output_tif_path=dtm_path,
        resolution_m=args.resolution,
        nodata=args.nodata,
    )
    print(f"Zapisano DTM GeoTIFF: {dtm_path.resolve()}")

    if args.classified_laz is not None:
        save_classified_laz(
            laz_path=args.laz,
            output_laz_path=args.classified_laz,
            smrf_slope=args.smrf_slope,
            smrf_window=args.smrf_window,
            smrf_threshold=args.smrf_threshold,
            smrf_scalar=args.smrf_scalar,
        )
        print(f"Zapisano sklasyfikowany LAZ: {args.classified_laz.resolve()}")

    plot_dtm(dtm_path, png_path, show_plot=not args.no_show)


if __name__ == "__main__":
    main()
