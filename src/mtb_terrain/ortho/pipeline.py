"""
ortho_pipeline.py
=================
Przetwarzanie ortofotomap GeoTIFF do tekstury terenu dla Unity URP.

Pipeline:
  1. Inspekcja i walidacja plikow GeoTIFF (CRS, rozdzielczosc, zasiag)
  2. Mozaikowanie kafli (merge) w pamieci przez rasterio
  3. Wyznaczenie bbox trasy z GPKG lub pipeline_report.json
  4. Kadrowanie (crop) do bbox trasy + margines
  5. Reprojekcja jesli CRS rozni sie od docelowego (opcjonalnie)
  6. Resize do potegi 2 (wymog Unity Texture2D)
  7. Eksport ortho.png (RGB) + ortho_preview.png + ortho_report.json
  8. Zapis metadanych georef do JSON (bounds, pixel_size, centroid Unity)

Uruchomienie:
    python ortho_pipeline.py ^
        --tifs data/ortho/81435.tif data/ortho/83893.tif ^
        --gpkg processed/flow--jump_epsg2180.gpkg ^
        --output-dir results/ortho

    # Lub z pipeline_report zamiast GPKG:
    python ortho_pipeline.py ^
        --tifs data/ortho/*.tif ^
        --pipeline-report results/lod/pipeline_report.json ^
        --output-dir results/ortho

Format wyjsciowy Unity:
    ortho.png        - tekstura RGB gotowa do importu (Texture2D, sRGB ON)
    ortho_report.json - metadane do UV mappingu i dokumentacji pracy
    ortho_preview.png - miniatura do weryfikacji wizualnej
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import Resampling
from rasterio.merge import merge as rasterio_merge

# ============================================================================
# Konfiguracja
# ============================================================================

@dataclass
class OrthoConfig:
    """Konfiguracja pipeline-u ortofotomap."""

    padding_m: float = 35.0
    """Margines wokol bbox trasy [m]. Powinien byc >= bufor GPS mesh-a (30m)
    zeby ortofoto pokrywalo caly teren mesh-a z zapasem."""

    output_size: int = 4096
    """Docelowy bok PNG [px] (potega 2). 4096 dla maksymalnej jakosci
    przy rozdzielczosci zrodlowej 25cm/px."""

    target_crs: str = "EPSG:2180"
    """Docelowy uklad wspolrzednych. Musi byc zgodny z CRS danych LiDAR."""

    resample_method: str = "bilinear"
    """Metoda interpolacji przy resize: bilinear / nearest / cubic."""

    preview_size: int = 512
    """Bok miniatury podgladowej [px]."""

    jpeg_quality: int = 95
    """Jakosc JPEG jesli eksportujemy .jpg (nie uzywane dla PNG)."""


# ============================================================================
# 1. Inspekcja i walidacja
# ============================================================================

def inspect_tifs(paths: list[Path]) -> dict:
    """
    Sprawdza CRS, rozdzielczosc i zasiag kazdego pliku GeoTIFF.
    Rzuca ValueError jesli pliki sa niespojne (rozny CRS, rozna rozdzielczosc).
    """
    print("\n[1/6] Inspekcja plikow GeoTIFF...")
    infos = []
    for p in paths:
        with rasterio.open(str(p)) as src:
            info = {
                "path": str(p),
                "name": p.name,
                "crs": src.crs.to_string(),
                "width": src.width,
                "height": src.height,
                "bands": src.count,
                "dtype": src.dtypes[0],
                "res_x": round(src.res[0], 6),
                "res_y": round(src.res[1], 6),
                "bounds": {
                    "left": src.bounds.left,
                    "bottom": src.bounds.bottom,
                    "right": src.bounds.right,
                    "top": src.bounds.top,
                },
                "nodata": src.nodata,
            }
            infos.append(info)
            print(f"  {p.name}: {src.width}x{src.height}px | "
                  f"{src.crs} | {src.res[0]:.4f}m/px | "
                  f"X[{src.bounds.left:.0f},{src.bounds.right:.0f}] "
                  f"Y[{src.bounds.bottom:.0f},{src.bounds.top:.0f}]")

    # Walidacja spojnosci
    crs_set = {i["crs"] for i in infos}
    if len(crs_set) > 1:
        raise ValueError(
            f"Pliki maja rozne uklady wspolrzednych: {crs_set}. "
            f"Zunifikuj CRS przed uruchomieniem (np. gdalwarp -t_srs EPSG:2180)."
        )

    res_set = {(i["res_x"], i["res_y"]) for i in infos}
    if len(res_set) > 1:
        print(f"  Uwaga: rozne rozdzielczosci {res_set}. "
              f"Rasterio merge ujednolici przez resampling.")

    source_crs = infos[0]["crs"]
    print(f"  CRS zrodlowy: {source_crs} "
          f"{'(zgodny z docelowym)' if source_crs == 'EPSG:2180' else '-> wymagana reprojekcja'}")

    return {"files": infos, "source_crs": source_crs}


# ============================================================================
# 2. Bbox trasy
# ============================================================================

def bbox_from_gpkg(gpkg_path: Path, layer: str = "track_points",
                   padding_m: float = 35.0) -> dict:
    """Wyznacza bbox z warstwy punktowej GPKG + margines."""
    try:
        import geopandas as gpd
    except ImportError as exc:
        raise ImportError("Wymagane: pip install geopandas") from exc

    gdf = gpd.read_file(str(gpkg_path), layer=layer)
    if gdf.empty:
        raise ValueError(f"Pusta warstwa '{layer}' w {gpkg_path}")

    b = gdf.total_bounds  # [xmin, ymin, xmax, ymax]
    return {
        "xmin": float(b[0]) - padding_m,
        "ymin": float(b[1]) - padding_m,
        "xmax": float(b[2]) + padding_m,
        "ymax": float(b[3]) + padding_m,
        "source": f"GPKG:{layer}",
        "padding_m": padding_m,
    }


def bbox_from_pipeline_report(report_path: Path, padding_m: float = 35.0) -> dict:
    """Wyznacza bbox z pipeline_report.json (centroid + bbox_extent)."""
    with open(report_path, encoding="utf-8") as f:
        report = json.load(f)

    # Centroid Unity jest w oryginalnych wspolrzednych przed translacja
    centroid = report["unity_centroid"]
    # bbox_extent z raw_stats
    extent = report["raw_stats"]["bbox_extent_m"]

    cx, cy = centroid[0], centroid[1]
    half_x = extent[0] / 2 + padding_m
    half_y = extent[1] / 2 + padding_m

    return {
        "xmin": cx - half_x,
        "ymin": cy - half_y,
        "xmax": cx + half_x,
        "ymax": cy + half_y,
        "source": f"pipeline_report:{report_path.name}",
        "padding_m": padding_m,
    }


def validate_bbox_in_tifs(bbox: dict, tif_infos: list[dict]) -> None:
    """Sprawdza czy bbox trasy miesci sie w zasiegu kafli TIF."""
    all_left   = min(i["bounds"]["left"]   for i in tif_infos)
    all_right  = max(i["bounds"]["right"]  for i in tif_infos)
    all_bottom = min(i["bounds"]["bottom"] for i in tif_infos)
    all_top    = max(i["bounds"]["top"]    for i in tif_infos)

    if (bbox["xmin"] < all_left or bbox["xmax"] > all_right or
            bbox["ymin"] < all_bottom or bbox["ymax"] > all_top):
        print("  OSTRZEZENIE: bbox trasy wykracza poza zasiag kafli TIF!")
        print(f"  Bbox trasy:  X[{bbox['xmin']:.0f},{bbox['xmax']:.0f}] "
              f"Y[{bbox['ymin']:.0f},{bbox['ymax']:.0f}]")
        print(f"  Bbox kafli:  X[{all_left:.0f},{all_right:.0f}] "
              f"Y[{all_bottom:.0f},{all_top:.0f}]")
        print("  Kadrowanie zostanie ograniczone do dostepnych danych.")
    else:
        w = bbox["xmax"] - bbox["xmin"]
        h = bbox["ymax"] - bbox["ymin"]
        print(f"  Bbox trasy: {w:.0f} x {h:.0f} m — miesci sie w zakresie kafli.")


# ============================================================================
# 3+4. Mozaikowanie i kadrowanie
# ============================================================================

def merge_and_crop(
    tif_paths: list[Path],
    bbox: dict,
    resample: Resampling = Resampling.bilinear,
) -> tuple[np.ndarray, dict]:
    """
    Mozaikuje kafle TIF i wycina obszar bbox.
    Zwraca (array_HxWx3, meta_dict).

    Uzywamy rasterio.merge z parametrem bounds zeby wczytac tylko
    potrzebny fragment — oszczedza RAM przy duzych mozaikach.
    """
    print("\n[3/6] Mozaikowanie kafli i kadrowanie do bbox trasy...")

    crop_bounds = (bbox["xmin"], bbox["ymin"], bbox["xmax"], bbox["ymax"])

    datasets = [rasterio.open(str(p)) for p in tif_paths]
    try:
        mosaic, transform = rasterio_merge(
            datasets,
            bounds=crop_bounds,
            resampling=resample,
            method="first",  # priorytet: pierwszy kafel w miejscach nakladania
        )
    finally:
        for ds in datasets:
            ds.close()

    # mosaic shape: (bands, H, W) -> (H, W, bands)
    arr = np.moveaxis(mosaic, 0, -1)  # -> H x W x 3

    print(f"  Mozaika po kadrze: {arr.shape[1]} x {arr.shape[0]} px "
          f"({arr.shape[1]*0.25:.0f} x {arr.shape[0]*0.25:.0f} m)")
    print(f"  Zakres wyjscia: {arr.dtype}, min={arr.min()}, max={arr.max()}")

    meta = {
        "crop_bounds": {
            "xmin": crop_bounds[0],
            "ymin": crop_bounds[1],
            "xmax": crop_bounds[2],
            "ymax": crop_bounds[3],
        },
        "raw_width_px":  int(arr.shape[1]),
        "raw_height_px": int(arr.shape[0]),
        "pixel_size_m": round(abs(transform.a), 6),
        "transform": list(transform)[:6],
    }

    return arr, meta


# ============================================================================
# 5. Resize do potegi 2
# ============================================================================

def next_power_of_two(n: int) -> int:
    return 2 ** math.ceil(math.log2(n))


def resize_to_power_of_two(
    arr: np.ndarray,
    target_size: int,
    resample_method: str = "bilinear",
) -> np.ndarray:
    """
    Resize obrazu do kwadratu target_size x target_size (potega 2).
    Uzywa PIL dla dobrej jakosci interpolacji przy skalowaniu.
    """
    print(f"\n[4/6] Resize do {target_size}x{target_size}px (potega 2)...")

    resample_map = {
        "bilinear": Image.Resampling.BILINEAR,
        "nearest":  Image.Resampling.NEAREST,
        "cubic":    Image.Resampling.BICUBIC,
        "lanczos":  Image.Resampling.LANCZOS,
    }
    pil_resample = resample_map.get(resample_method, Image.Resampling.BILINEAR)

    img = Image.fromarray(arr.astype(np.uint8), mode="RGB")
    orig_w, orig_h = img.size
    img_resized = img.resize((target_size, target_size), pil_resample)

    scale_x = target_size / orig_w
    scale_y = target_size / orig_h
    print(f"  {orig_w}x{orig_h} -> {target_size}x{target_size} "
          f"(scale: {scale_x:.3f}x, {scale_y:.3f}y)")

    if abs(scale_x - scale_y) > 0.05:
        print(f"  Uwaga: ortofoto zostanie lekko znieksztalcone "
              f"(proporcje {orig_w}:{orig_h} != 1:1). "
              f"Alternatywnie uzyj --output-size 0 zeby zachowac proporcje.")

    return np.array(img_resized)


# ============================================================================
# 6. Eksport i raport
# ============================================================================

def save_png(arr: np.ndarray, path: Path) -> None:
    Image.fromarray(arr.astype(np.uint8), mode="RGB").save(
        str(path), optimize=True
    )


def save_preview(arr: np.ndarray, path: Path, size: int = 512) -> None:
    img = Image.fromarray(arr.astype(np.uint8), mode="RGB")
    img.thumbnail((size, size), Image.Resampling.LANCZOS)
    img.save(str(path), optimize=True)


def build_unity_georef(bbox: dict, output_size: int,
                       unity_centroid: list | None = None) -> dict:
    """
    Oblicza parametry georeferencji dla Unity:
    - world_bounds: zasiag obszaru w oryginalnych wspolrzednych EPSG:2180
    - unity_bounds: zasiag po translacji do origin Unity (centroid = 0,0)
    - pixel_size_m: wielkosc piksela w metrach po resize
    - uv_scale: skalowanie UV dla Terrain (zwykle 1.0)

    Te wartosci sa potrzebne do poprawnego UV mappingu mesha w Unity.
    """
    w_m = bbox["xmax"] - bbox["xmin"]
    h_m = bbox["ymax"] - bbox["ymin"]
    pixel_size = max(w_m, h_m) / output_size

    georef = {
        "world_bounds_epsg2180": bbox,
        "width_m": round(w_m, 2),
        "height_m": round(h_m, 2),
        "pixel_size_m_after_resize": round(pixel_size, 4),
        "output_size_px": output_size,
    }

    if unity_centroid:
        cx, cy = unity_centroid[0], unity_centroid[1]
        georef["unity_bounds"] = {
            "xmin": round(bbox["xmin"] - cx, 2),
            "ymin": round(bbox["ymin"] - cy, 2),
            "xmax": round(bbox["xmax"] - cx, 2),
            "ymax": round(bbox["ymax"] - cy, 2),
        }
        georef["unity_centroid"] = unity_centroid

    georef["unity_import_note"] = (
        "W Unity: Texture Type=Default, sRGB=ON, "
        "Compression=None, Generate MipMaps=ON, "
        "Wrap Mode=Clamp, Filter Mode=Bilinear."
    )

    return georef


# ============================================================================
# Pipeline glowny
# ============================================================================

def run_pipeline(
    tif_paths: list[Path],
    output_dir: Path,
    gpkg_path: Path | None = None,
    pipeline_report_path: Path | None = None,
    config: OrthoConfig | None = None,
) -> dict:
    """Pelny pipeline: GeoTIFF -> ortho.png + metadane."""
    config = config or OrthoConfig()
    output_dir.mkdir(parents=True, exist_ok=True)

    print()
    print("=" * 70)
    print("  Ortho pipeline — przetwarzanie ortofotomap")
    print(f"  Pliki TIF: {len(tif_paths)}")
    print(f"  Output:    {output_dir.resolve()}")
    print("=" * 70)

    # 1. Inspekcja
    tif_info = inspect_tifs(tif_paths)

    # 2. Bbox trasy
    print("\n[2/6] Wyznaczanie bbox trasy...")
    unity_centroid = None

    if gpkg_path:
        bbox = bbox_from_gpkg(gpkg_path, padding_m=config.padding_m)
        print(f"  Zrodlo bbox: GPKG ({gpkg_path.name})")
    elif pipeline_report_path:
        bbox = bbox_from_pipeline_report(pipeline_report_path, config.padding_m)
        with open(pipeline_report_path) as f:
            rpt = json.load(f)
        unity_centroid = rpt.get("unity_centroid")
        print("  Zrodlo bbox: pipeline_report.json")
    else:
        raise ValueError("Podaj --gpkg lub --pipeline-report jako zrodlo bbox trasy.")

    print(f"  Bbox: X[{bbox['xmin']:.0f}, {bbox['xmax']:.0f}] "
          f"Y[{bbox['ymin']:.0f}, {bbox['ymax']:.0f}] "
          f"({bbox['xmax']-bbox['xmin']:.0f}x{bbox['ymax']-bbox['ymin']:.0f} m)")
    validate_bbox_in_tifs(bbox, tif_info["files"])

    # 3+4. Mozaika + crop
    resample_enum = getattr(Resampling, config.resample_method, Resampling.bilinear)
    arr_raw, crop_meta = merge_and_crop(tif_paths, bbox, resample_enum)

    # 5. Resize
    arr_final = resize_to_power_of_two(arr_raw, config.output_size,
                                       config.resample_method)

    # 6. Eksport
    print("\n[5/6] Eksport plikow...")
    ortho_path   = output_dir / "ortho.png"
    preview_path = output_dir / "ortho_preview.png"
    report_path  = output_dir / "ortho_report.json"

    save_png(arr_final, ortho_path)
    save_preview(arr_final, preview_path, config.preview_size)
    print(f"  ortho.png:         {ortho_path.name} "
          f"({config.output_size}x{config.output_size}px, "
          f"{ortho_path.stat().st_size//1024//1024} MB)")
    print(f"  ortho_preview.png: miniatura {config.preview_size}px")

    # Raport
    georef = build_unity_georef(bbox, config.output_size, unity_centroid)
    report = {
        "input_files": [str(p.resolve()) for p in tif_paths],
        "output_dir": str(output_dir.resolve()),
        "config": {
            "padding_m": config.padding_m,
            "output_size": config.output_size,
            "target_crs": config.target_crs,
            "resample_method": config.resample_method,
        },
        "source_crs": tif_info["source_crs"],
        "reprojection_needed": tif_info["source_crs"] != config.target_crs,
        "source_resolution_m": tif_info["files"][0]["res_x"],
        "crop": crop_meta,
        "georef": georef,
        "output_files": {
            "ortho_png": str(ortho_path.resolve()),
            "preview_png": str(preview_path.resolve()),
        },
    }

    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print("  ortho_report.json: metadane + georef dla Unity")

    print("\n[6/6] Weryfikacja...")
    print(f"  Rozdzielczosc zrodlowa:  {tif_info['files'][0]['res_x']:.3f} m/px")
    print(f"  Rozdzielczosc po resize: "
          f"{georef['pixel_size_m_after_resize']:.3f} m/px")
    print(f"  Utrata szczegolowosci:   "
          f"{'BRAK (powiekszono)' if georef['pixel_size_m_after_resize'] <= tif_info['files'][0]['res_x'] else 'jest (zaagregowano)'}")
    print(f"\n  Obszar: {georef['width_m']:.0f} x {georef['height_m']:.0f} m")
    print(f"  CRS:    {tif_info['source_crs']} (brak reprojekcji)")

    print()
    print("=" * 70)
    print("  Pipeline zakonczony. Pliki gotowe do Unity.")
    print("=" * 70)

    return report


# ============================================================================
# CLI
# ============================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Przetwarzanie ortofotomap GeoTIFF do tekstury PNG dla Unity. "
            "Realizuje procedury georeferencji i dopasowania jako tekstur "
            "do modelu terenu zgodnie z zakresem pracy inzynierskiej."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--tifs", nargs="+", type=Path, required=True,
        help="Lista plikow GeoTIFF (mozna podac wiele).",
    )
    parser.add_argument(
        "--gpkg", type=Path, default=None,
        help="GPKG ze sladem GPS do wyznaczenia bbox trasy.",
    )
    parser.add_argument(
        "--pipeline-report", type=Path, default=None,
        help="results/lod/pipeline_report.json jako alternatywne zrodlo bbox.",
    )
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path("results/ortho"),
    )
    parser.add_argument(
        "--padding", type=float, default=35.0,
        help="Margines wokol bbox trasy [m].",
    )
    parser.add_argument(
        "--output-size", type=int, default=4096,
        help="Docelowy bok PNG [px], musi byc potega 2.",
    )
    parser.add_argument(
        "--resample", default="bilinear",
        choices=["bilinear", "nearest", "cubic", "lanczos"],
        help="Metoda interpolacji przy resize.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    missing = [p for p in args.tifs if not p.exists()]
    if missing:
        raise FileNotFoundError(f"Brak plikow TIF: {missing}")
    if args.gpkg and not args.gpkg.exists():
        raise FileNotFoundError(f"Brak pliku GPKG: {args.gpkg}")
    if args.pipeline_report and not args.pipeline_report.exists():
        raise FileNotFoundError(f"Brak pipeline_report: {args.pipeline_report}")
    if not args.gpkg and not args.pipeline_report:
        raise ValueError("Podaj --gpkg lub --pipeline-report.")

    # Walidacja potegi 2
    if args.output_size > 0 and (args.output_size & (args.output_size - 1)) != 0:
        raise ValueError(
            f"--output-size {args.output_size} nie jest potega 2. "
            f"Uzyj: 512, 1024, 2048, 4096 lub 8192."
        )

    config = OrthoConfig(
        padding_m=args.padding,
        output_size=args.output_size,
        resample_method=args.resample,
    )

    run_pipeline(
        tif_paths=args.tifs,
        output_dir=args.output_dir,
        gpkg_path=args.gpkg,
        pipeline_report_path=args.pipeline_report,
        config=config,
    )


if __name__ == "__main__":
    main()
