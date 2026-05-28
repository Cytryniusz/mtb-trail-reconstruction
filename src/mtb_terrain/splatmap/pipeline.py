"""
splatmap_pipeline.py
====================
Generacja splatmapy 4-warstwowej z chmury LiDAR + sladu GPS,
gotowej do importu jako Terrain Layers w Unity URP.

Warstwy splatmapy (kanaly RGBA):
  R = GROUND        - ogolny teren (ASPRS class 2, gladki)
  G = PATH          - sciezka MTB (bufor wokol sladu GPS)
  B = UNDERGROWTH   - wegetacja (ASPRS classes 3, 4, 5)
  A = ROCK          - kamieniste / wyboiste obszary (class 2 + wysoka chropowatosc Z)

Pipeline:
  1. Wczytanie pelnej chmury LAZ/LAS (z klasyfikacja ASPRS)
  2. Wczytanie sladu GPS z GPKG (warstwa punktowa)
  3. Rasteryzacja kazdej warstwy do siatki o podanej rozdzielczosci
  4. Detekcja 'rock' z lokalnej chropowatosci ground points
  5. Bufor + gaussian falloff dla path z GPS
  6. Smoothing przejsc miedzy warstwami
  7. Normalizacja (suma kanalow per piksel = 1)
  8. Eksport: splatmap.png (RGBA) + preview.png (RGB pseudo-realistyczny) + report JSON

Uruchomienie:
    python splatmap_pipeline.py --las flow.laz --gpkg track.gpkg
    python splatmap_pipeline.py --las flow.laz --gpkg track.gpkg \\
        --resolution 0.5 --output-size 1024

Format Unity: import PNG jako Texture2D z R/G/B/A swizzlowanymi na cztery
Terrain Layers w komponencie Terrain (RGBA = waga kazdej warstwy).
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image
from scipy import ndimage


# ============================================================================
# Konfiguracja
# ============================================================================

# ASPRS LAS Specification klasy punktow
ASPRS_UNCLASSIFIED = 1
ASPRS_GROUND = 2
ASPRS_LOW_VEG = 3
ASPRS_MED_VEG = 4
ASPRS_HIGH_VEG = 5
ASPRS_BUILDING = 6
ASPRS_NOISE = 7
ASPRS_WATER = 9


@dataclass
class SplatmapConfig:
    """Konfiguracja generacji splatmapy."""

    # Rozdzielczosc rastra
    resolution_m_per_pixel: float = 0.5
    """Wielkosc piksela w metrach na rastrze pre-resize.
    0.5 m/px to dobry balans dla trasy MTB."""

    # Mapowanie klas ASPRS -> warstwy
    ground_classes: tuple = (ASPRS_GROUND,)
    undergrowth_classes: tuple = (ASPRS_LOW_VEG, ASPRS_MED_VEG, ASPRS_HIGH_VEG)

    # Path z GPS
    path_buffer_m: float = 1.5
    """Szerokosc bufora wokol sladu GPS (m). Dla single-track MTB: 1-2 m."""

    path_falloff_m: float = 1.5
    """Sigma gaussowskiego zaniku przy krawedzi path (m).
    Daje miekkie przejscie path -> ground."""

    # Rock detection z chropowatosci
    rock_detection_enabled: bool = True
    rock_roughness_threshold_m: float = 0.25
    """Min odchylenie standardowe Z w komorce rastra do oznaczenia jako rock (m).
    Dla typowej trasy lesnej: 0.20-0.35 m."""

    # Globalny smoothing splatmapy
    smoothing_sigma_m: float = 1.0
    """Sigma gaussowskiego rozmycia kazdego kanalu (m).
    Daje naturalne przejscia miedzy warstwami."""

    # Output
    output_size: int = 1024
    """Docelowy bok PNG (potega 2 - wymog Unity Terrain).
    0 = nie zmieniaj rozmiaru pre-resize rastra."""

    bits_per_channel: int = 8
    """8 lub 16. Unity Terrain Layers obsluguje oba."""

    padding_m: float = 5.0
    """Margines wokol bbox chmury (m). Zapobiega ucinaniu na krawedzi."""

    # Fallback dla pikseli bez zadnych danych
    default_fallback_layer: int = 0
    """Indeks warstwy (0..3) przypisywanej pikselom bez danych. 0 = GROUND."""


@dataclass
class SplatmapBounds:
    """Geograficzne granice splatmapy (w CRS, np. EPSG:2180)."""
    xmin: float
    ymin: float
    xmax: float
    ymax: float

    @property
    def width_m(self) -> float: return self.xmax - self.xmin

    @property
    def height_m(self) -> float: return self.ymax - self.ymin

    def to_dict(self) -> dict:
        return {"xmin": self.xmin, "ymin": self.ymin,
                "xmax": self.xmax, "ymax": self.ymax,
                "width_m": self.width_m, "height_m": self.height_m}


# ============================================================================
# Wczytywanie danych
# ============================================================================


def load_lidar_classified(path: Path) -> dict:
    """Wczytuje LAZ/LAS z klasyfikacja ASPRS."""
    try:
        import laspy
    except ImportError:
        raise ImportError(
            "Wymagana biblioteka laspy: pip install laspy[lazrs]"
        )

    las = laspy.read(str(path))
    xyz = np.column_stack([
        np.asarray(las.x, dtype=float),
        np.asarray(las.y, dtype=float),
        np.asarray(las.z, dtype=float),
    ])
    classification = np.asarray(las.classification, dtype=np.uint8)

    intensity = None
    if "intensity" in set(las.point_format.dimension_names):
        intensity = np.asarray(las.intensity, dtype=float)

    return {
        "xyz": xyz,
        "classification": classification,
        "intensity": intensity,
        "n_points": len(xyz),
    }


def load_gps_xy(gpkg_path: Path, layer: str = "track_points",
                target_epsg: str = "EPSG:2180") -> np.ndarray:
    """Wczytuje slad GPS z GPKG, sortuje po point_id/distance_km."""
    try:
        import geopandas as gpd
    except ImportError:
        raise ImportError("Wymagana biblioteka geopandas")

    track = gpd.read_file(gpkg_path, layer=layer)
    if track.empty:
        raise ValueError(f"Warstwa '{layer}' w {gpkg_path} jest pusta.")

    if track.crs is None:
        print(f"  Uwaga: warstwa '{layer}' bez CRS. Zakladam {target_epsg}.")
    elif track.crs.to_string().upper() != target_epsg:
        print(f"  Reprojekcja GPS: {track.crs} -> {target_epsg}")
        track = track.to_crs(target_epsg)

    if "point_id" in track.columns:
        track = track.sort_values("point_id")
    elif "distance_km" in track.columns:
        track = track.sort_values("distance_km")

    from typing import cast
    from shapely.geometry import Point

    return np.array([(cast(Point, p).x, cast(Point, p).y) for p in track.geometry], dtype=float)


# ============================================================================
# Rasteryzacja
# ============================================================================


def determine_bounds(points_xy: np.ndarray, padding_m: float) -> SplatmapBounds:
    """Bbox z chmury z marginesem."""
    return SplatmapBounds(
        xmin=float(points_xy[:, 0].min()) - padding_m,
        ymin=float(points_xy[:, 1].min()) - padding_m,
        xmax=float(points_xy[:, 0].max()) + padding_m,
        ymax=float(points_xy[:, 1].max()) + padding_m,
    )


def compute_raster_size(bounds: SplatmapBounds, m_per_pixel: float) -> tuple[int, int]:
    """Zwraca (raster_w, raster_h) w pikselach."""
    raster_w = max(int(round(bounds.width_m / m_per_pixel)), 1)
    raster_h = max(int(round(bounds.height_m / m_per_pixel)), 1)
    return raster_w, raster_h


def points_to_pixel_indices(points_xy: np.ndarray, bounds: SplatmapBounds,
                            raster_w: int, raster_h: int) -> tuple:
    """Konwertuje wspolrzedne XY na indeksy pikseli (col, row). Y rosnie w gore,
    row 0 jest na gorze obrazka => row = h - 1 - (y - ymin) / h_m * h_px"""
    col = ((points_xy[:, 0] - bounds.xmin) / bounds.width_m * raster_w).astype(int)
    row = (raster_h - 1 -
           ((points_xy[:, 1] - bounds.ymin) / bounds.height_m * raster_h)
           ).astype(int)
    valid = (col >= 0) & (col < raster_w) & (row >= 0) & (row < raster_h)
    return col, row, valid


def rasterize_density(points_xy: np.ndarray, mask: np.ndarray,
                      bounds: SplatmapBounds,
                      raster_w: int, raster_h: int) -> np.ndarray:
    """Rasteryzuje punkty z maski jako mape gestosci (count na piksel)."""
    raster = np.zeros((raster_h, raster_w), dtype=np.float32)
    if mask.sum() == 0:
        return raster
    sel = points_xy[mask]
    col, row, valid = points_to_pixel_indices(sel, bounds, raster_w, raster_h)
    np.add.at(raster, (row[valid], col[valid]), 1.0)
    return raster


def rasterize_std_z(points_xyz: np.ndarray, mask: np.ndarray,
                    bounds: SplatmapBounds,
                    raster_w: int, raster_h: int) -> np.ndarray:
    """Liczy odchylenie standardowe Z w kazdej komorce rastra (chropowatosc).

    Uzywa wzoru: Var(Z) = E[Z^2] - E[Z]^2 na bin.
    """
    if mask.sum() == 0:
        return np.zeros((raster_h, raster_w), dtype=np.float32)

    sel = points_xyz[mask]
    col, row, valid = points_to_pixel_indices(sel[:, :2], bounds, raster_w, raster_h)
    z = sel[:, 2]
    c, r, z = col[valid], row[valid], z[valid]

    sum_z = np.zeros((raster_h, raster_w), dtype=np.float64)
    sum_z2 = np.zeros((raster_h, raster_w), dtype=np.float64)
    count = np.zeros((raster_h, raster_w), dtype=np.float32)

    np.add.at(sum_z, (r, c), z)
    np.add.at(sum_z2, (r, c), z * z)
    np.add.at(count, (r, c), 1.0)

    safe_count = np.maximum(count, 1.0)
    mean_z = sum_z / safe_count
    var_z = np.maximum(sum_z2 / safe_count - mean_z ** 2, 0.0)

    std_z = np.sqrt(var_z).astype(np.float32)
    # Komorki z 1 punktem nie maja sensownej std -> 0
    std_z[count < 2] = 0.0
    return std_z


# ============================================================================
# Path mask z GPS
# ============================================================================


def build_path_mask(
    gps_xy: np.ndarray,
    bounds: SplatmapBounds,
    raster_w: int,
    raster_h: int,
    m_per_pixel: float,
    buffer_m: float,
    falloff_m: float,
) -> np.ndarray:
    """Generuje mape path: pelne 1.0 w buforze + gaussian falloff na zewnatrz."""
    raster = np.zeros((raster_h, raster_w), dtype=np.float32)

    col, row, valid = points_to_pixel_indices(gps_xy, bounds, raster_w, raster_h)

    # Rasteryzacja linii laczacych kolejne punkty GPS (interpolacja liniowa)
    for i in range(len(gps_xy) - 1):
        if not (valid[i] and valid[i + 1]):
            continue
        c0, r0 = col[i], row[i]
        c1, r1 = col[i + 1], row[i + 1]
        n_steps = max(abs(c1 - c0), abs(r1 - r0), 1) + 1
        cs = np.linspace(c0, c1, n_steps).astype(int)
        rs = np.linspace(r0, r1, n_steps).astype(int)
        cs = np.clip(cs, 0, raster_w - 1)
        rs = np.clip(rs, 0, raster_h - 1)
        raster[rs, cs] = 1.0

    # Dilate do szerokosci bufora (twardy core)
    buffer_pixels = max(int(round(buffer_m / m_per_pixel)), 1)
    raster = ndimage.binary_dilation(
        raster > 0, iterations=buffer_pixels
    ).astype(np.float32)

    # Gaussian falloff na zewnatrz bufora (miekkie krawedzie)
    sigma_pixels = falloff_m / m_per_pixel
    if sigma_pixels > 0:
        blurred = ndimage.gaussian_filter(raster, sigma=sigma_pixels)
        # Wewnatrz bufora trzymamy 1.0, na zewnatrz wstawiamy blur
        core = raster > 0
        raster = np.where(core, 1.0, blurred)
        if raster.max() > 0:
            raster = np.clip(raster, 0.0, 1.0)

    return raster


# ============================================================================
# Glowny pipeline
# ============================================================================


def build_splatmap(
    lidar: dict,
    gps_xy: np.ndarray,
    bounds: SplatmapBounds,
    config: SplatmapConfig,
) -> tuple[np.ndarray, dict]:
    """
    Buduje 4-kanalowa splatmape o ksztalcie (H, W, 4).
    Zwraca (splatmap, statystyki).
    """
    raster_w, raster_h = compute_raster_size(bounds, config.resolution_m_per_pixel)
    print(f"  Raster pre-resize: {raster_w} x {raster_h} px "
          f"({config.resolution_m_per_pixel} m/px)")

    xyz = lidar["xyz"]
    cls = lidar["classification"]

    # ---- Maski klas
    mask_ground = np.isin(cls, config.ground_classes)
    mask_undergrowth = np.isin(cls, config.undergrowth_classes)

    n_ground = int(mask_ground.sum())
    n_under = int(mask_undergrowth.sum())
    print(f"  Punkty wg klas: ground={n_ground:,}  undergrowth={n_under:,}")

    if n_ground == 0:
        print("  OSTRZEZENIE: brak punktow ground (klasa 2). "
              "Sprawdz parametry --ground-classes lub klasyfikacje LAZ.")
    if n_under == 0:
        print("  OSTRZEZENIE: brak punktow wegetacji (klasy 3-5). "
              "Splatmap bedzie miala pusta warstwe B.")

    # ---- Rasteryzacja warstw
    points_xy = xyz[:, :2]
    layer_ground = rasterize_density(points_xy, mask_ground, bounds, raster_w, raster_h)
    layer_under = rasterize_density(points_xy, mask_undergrowth, bounds, raster_w, raster_h)

    # ---- Path layer z GPS
    if len(gps_xy) >= 2:
        layer_path = build_path_mask(
            gps_xy, bounds, raster_w, raster_h,
            config.resolution_m_per_pixel,
            config.path_buffer_m,
            config.path_falloff_m,
        )
    else:
        print("  OSTRZEZENIE: za malo punktow GPS na path. Pusta warstwa G.")
        layer_path = np.zeros((raster_h, raster_w), dtype=np.float32)

    # ---- Rock detection z chropowatosci ground points
    if config.rock_detection_enabled and n_ground > 0:
        std_z = rasterize_std_z(xyz, mask_ground, bounds, raster_w, raster_h)
        layer_rock = (std_z > config.rock_roughness_threshold_m).astype(np.float32)
        # Skaluj na podstawie tego jak bardzo przekracza prog
        layer_rock = layer_rock * np.clip(
            (std_z - config.rock_roughness_threshold_m) / config.rock_roughness_threshold_m,
            0.0, 1.0,
        )
        print(f"  Rock detection: max std(Z)={float(std_z.max()):.2f} m, "
              f"pixels rock={int((layer_rock > 0).sum()):,}")
    else:
        layer_rock = np.zeros((raster_h, raster_w), dtype=np.float32)

    # ---- Normalizacja gestosci ground/undergrowth (kazda warstwa do [0,1])
    if layer_ground.max() > 0:
        layer_ground /= np.percentile(layer_ground[layer_ground > 0], 95)
        layer_ground = np.clip(layer_ground, 0.0, 1.0)
    if layer_under.max() > 0:
        layer_under /= np.percentile(layer_under[layer_under > 0], 95)
        layer_under = np.clip(layer_under, 0.0, 1.0)

    # ---- Smoothing kazdej warstwy
    if config.smoothing_sigma_m > 0:
        sigma_px = config.smoothing_sigma_m / config.resolution_m_per_pixel
        layer_ground = ndimage.gaussian_filter(layer_ground, sigma=sigma_px)
        layer_under = ndimage.gaussian_filter(layer_under, sigma=sigma_px)
        # Path i rock juz maja swoj smoothing
        layer_rock = ndimage.gaussian_filter(layer_rock, sigma=sigma_px * 0.5)

    # ---- Stack RGBA
    splatmap = np.stack([layer_ground, layer_path, layer_under, layer_rock], axis=-1)

    # ---- Path nadpisuje ground gdzie GPS przechodzi (path > 0.5)
    #      Path jest 'twardszy' - my chcemy widzialna sciezke nawet w gestych
    #      obszarach ground
    path_dominant = layer_path > 0.5
    splatmap[..., 0] = np.where(path_dominant,
                                 splatmap[..., 0] * (1.0 - layer_path),
                                 splatmap[..., 0])

    # ---- Fallback: piksele bez zadnej warstwy -> domyslna warstwa (zwykle ground)
    sums = splatmap.sum(axis=-1)
    empty_mask = sums < 0.01
    if empty_mask.any():
        splatmap[empty_mask, config.default_fallback_layer] = 1.0
        print(f"  Fallback ground na {int(empty_mask.sum()):,} pikselach bez danych.")

    # ---- Normalizacja sum per piksel = 1 (wymog Unity)
    sums = splatmap.sum(axis=-1, keepdims=True)
    splatmap = splatmap / np.maximum(sums, 1e-6)

    stats = {
        "raster_size_pre_resize": [raster_w, raster_h],
        "n_points_ground": n_ground,
        "n_points_undergrowth": n_under,
        "layer_coverage_pct": {
            "ground":      round(float((splatmap[..., 0] > 0.1).mean() * 100), 1),
            "path":        round(float((splatmap[..., 1] > 0.1).mean() * 100), 1),
            "undergrowth": round(float((splatmap[..., 2] > 0.1).mean() * 100), 1),
            "rock":        round(float((splatmap[..., 3] > 0.1).mean() * 100), 1),
        },
    }
    return splatmap, stats


# ============================================================================
# Resize do potegi 2 + eksport
# ============================================================================


def resize_splatmap(splatmap: np.ndarray, target_size: int) -> np.ndarray:
    """Resize do (target_size, target_size). Re-normalizacja po resize."""
    h, w, c = splatmap.shape

    # Kazdy kanal osobno - PIL.Image obsluguje resize z dobra interpolacja
    out = np.zeros((target_size, target_size, c), dtype=np.float32)
    for ch in range(c):
        img = Image.fromarray((splatmap[..., ch] * 255).astype(np.uint8), mode="L")
        img_resized = img.resize((target_size, target_size), Image.Resampling.BILINEAR)
        out[..., ch] = np.array(img_resized, dtype=np.float32) / 255.0

    # Re-normalizacja po resize
    sums = out.sum(axis=-1, keepdims=True)
    out = out / np.maximum(sums, 1e-6)
    return out


def save_splatmap_png(splatmap: np.ndarray, path: Path, bits: int = 8) -> None:
    """Zapisuje 4-kanalowa splatmape jako PNG RGBA."""
    if bits == 16:
        arr = (splatmap * 65535).clip(0, 65535).astype(np.uint16)
        # PIL: zapisz jako 4 osobne kanaly 16-bit (mode I;16) -> uzyj OpenEXR/TIFF
        # Najprosciej: spakuj do RGBA 16-bit jako PNG
        img = Image.fromarray(arr, mode="RGBA")
        img.save(path, optimize=True)
    else:
        arr = (splatmap * 255).clip(0, 255).astype(np.uint8)
        Image.fromarray(arr, mode="RGBA").save(path, optimize=True)


def create_preview_rgb(splatmap: np.ndarray, path: Path) -> None:
    """Zapisuje preview RGB - kazda warstwa ma pseudo-realistyczny kolor."""
    layer_colors = np.array([
        [0.55, 0.42, 0.30],   # GROUND - braz
        [0.78, 0.65, 0.45],   # PATH - jasny braz / piasek
        [0.30, 0.55, 0.20],   # UNDERGROWTH - zielen lesna
        [0.50, 0.48, 0.45],   # ROCK - szary kamien
    ], dtype=np.float32)

    h, w, _ = splatmap.shape
    preview = np.zeros((h, w, 3), dtype=np.float32)
    for i in range(4):
        preview += splatmap[..., i:i + 1] * layer_colors[i]

    arr = (preview * 255).clip(0, 255).astype(np.uint8)
    Image.fromarray(arr, mode="RGB").save(path, optimize=True)


def save_layer_debug(splatmap: np.ndarray, output_dir: Path) -> None:
    """Zapisuje kazda warstwe jako osobny PNG grayscale do debugowania."""
    names = ["ground", "path", "undergrowth", "rock"]
    for i, name in enumerate(names):
        arr = (splatmap[..., i] * 255).clip(0, 255).astype(np.uint8)
        Image.fromarray(arr, mode="L").save(
            output_dir / f"layer_{i}_{name}.png", optimize=True
        )


# ============================================================================
# Pipeline glowny
# ============================================================================


def run_pipeline(
    las_path: Path,
    gpkg_path: Optional[Path],
    output_dir: Path,
    config: Optional[SplatmapConfig] = None,
    gpkg_layer: str = "track_points",
    bounds_override: Optional[SplatmapBounds] = None,
    save_debug_layers: bool = True,
) -> dict:
    """Pelny pipeline: LAZ + GPKG -> splatmap.png + preview + raport JSON."""
    config = config or SplatmapConfig()
    output_dir.mkdir(parents=True, exist_ok=True)

    print()
    print("=" * 86)
    print(f"  Splatmap pipeline")
    print(f"  LAZ:  {las_path}")
    print(f"  GPKG: {gpkg_path if gpkg_path else '(brak - warstwa PATH bedzie pusta)'}")
    print(f"  Output: {output_dir.resolve()}")
    print("=" * 86)

    # ---- 1. Wczytanie
    print("\n[1/5] Wczytywanie LAZ...")
    lidar = load_lidar_classified(las_path)
    print(f"  Punktow: {lidar['n_points']:,}")
    unique_cls, counts = np.unique(lidar["classification"], return_counts=True)
    print(f"  Klasy ASPRS w pliku: " +
          ", ".join(f"{int(c)}({int(n):,})" for c, n in zip(unique_cls, counts)))

    gps_xy = np.empty((0, 2))
    if gpkg_path:
        print("\n[2/5] Wczytywanie sladu GPS...")
        gps_xy = load_gps_xy(gpkg_path, layer=gpkg_layer)
        print(f"  Punktow GPS: {len(gps_xy):,}")
    else:
        print("\n[2/5] Pomijam GPS (brak --gpkg).")

    # ---- 2. Bounds
    print("\n[3/5] Wyznaczanie bbox...")
    bounds = bounds_override or determine_bounds(lidar["xyz"][:, :2], config.padding_m)
    print(f"  Bbox: X [{bounds.xmin:.1f}, {bounds.xmax:.1f}] "
          f"({bounds.width_m:.1f} m)")
    print(f"        Y [{bounds.ymin:.1f}, {bounds.ymax:.1f}] "
          f"({bounds.height_m:.1f} m)")

    # ---- 3. Splatmap
    print("\n[4/5] Generowanie warstw splatmapy...")
    splatmap, layer_stats = build_splatmap(lidar, gps_xy, bounds, config)

    # ---- 4. Resize do output_size
    if config.output_size > 0:
        print(f"\n  Resize do {config.output_size}x{config.output_size}...")
        splatmap = resize_splatmap(splatmap, config.output_size)

    # ---- 5. Eksport
    print("\n[5/5] Eksport plikow...")
    splatmap_path = output_dir / "splatmap.png"
    preview_path = output_dir / "splatmap_preview.png"

    save_splatmap_png(splatmap, splatmap_path, bits=config.bits_per_channel)
    create_preview_rgb(splatmap, preview_path)
    print(f"  Splatmap RGBA: {splatmap_path.name}")
    print(f"  Preview RGB:   {preview_path.name}")

    if save_debug_layers:
        save_layer_debug(splatmap, output_dir)
        print(f"  Debug warstwy: layer_0_ground.png .. layer_3_rock.png")

    # ---- Raport JSON
    report = {
        "input_las": str(las_path.resolve()),
        "input_gpkg": str(gpkg_path.resolve()) if gpkg_path else None,
        "output_dir": str(output_dir.resolve()),
        "config": asdict(config),
        "bounds": bounds.to_dict(),
        "stats": layer_stats,
        "final_size": list(splatmap.shape[:2]),
        "channel_mapping": {
            "R": "GROUND",
            "G": "PATH",
            "B": "UNDERGROWTH",
            "A": "ROCK",
        },
    }
    report_path = output_dir / "splatmap_report.json"
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
    print(f"  Raport: {report_path.name}")

    print()
    print("Pokrycie warstw (% pikseli z waga >0.1):")
    for layer, pct in layer_stats["layer_coverage_pct"].items():
        print(f"  {layer:12s} {pct:5.1f}%")

    return report


# ============================================================================
# CLI
# ============================================================================


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generacja splatmapy 4-warstwowej z LiDAR + GPS dla Unity Terrain. "
            "Trzecia faza po gps_lidar_int_delaunay.py + mesh_pipeline.py."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--las", type=Path, required=True,
        help="Sciezka do pliku LAZ/LAS z klasyfikacja ASPRS (pelna chmura, nie ground-only).",
    )
    parser.add_argument(
        "--gpkg", type=Path, default=None,
        help="Sciezka do GPKG ze sladem GPS (opcjonalnie). "
             "Bez tego warstwa PATH bedzie pusta.",
    )
    parser.add_argument("--gpkg-layer", default="track_points")
    parser.add_argument(
        "--output-dir", type=Path, default=Path("results/splatmap"),
        help="Katalog wyjsciowy.",
    )

    parser.add_argument("--resolution", type=float, default=0.5,
        help="Wielkosc piksela rastra (m/px).")
    parser.add_argument("--output-size", type=int, default=1024,
        help="Docelowy bok PNG (potega 2 wymagana przez Unity).")
    parser.add_argument("--path-buffer", type=float, default=1.5,
        help="Szerokosc bufora wokol GPS path (m).")
    parser.add_argument("--path-falloff", type=float, default=1.5,
        help="Sigma gaussowskiego zaniku krawedzi path (m).")
    parser.add_argument("--rock-threshold", type=float, default=0.25,
        help="Prog std(Z) na komorke dla detekcji rock (m).")
    parser.add_argument("--no-rock", action="store_true",
        help="Wylacz detekcje rock (warstwa A bedzie pusta).")
    parser.add_argument("--smoothing", type=float, default=1.0,
        help="Sigma gaussowskiego smoothingu warstw (m).")
    parser.add_argument("--no-debug-layers", action="store_true",
        help="Nie zapisuj 4 osobnych PNG dla debugowania warstw.")

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not args.las.exists():
        raise FileNotFoundError(f"Brak pliku LAZ: {args.las}")
    if args.gpkg and not args.gpkg.exists():
        raise FileNotFoundError(f"Brak pliku GPKG: {args.gpkg}")

    config = SplatmapConfig(
        resolution_m_per_pixel=args.resolution,
        path_buffer_m=args.path_buffer,
        path_falloff_m=args.path_falloff,
        rock_detection_enabled=not args.no_rock,
        rock_roughness_threshold_m=args.rock_threshold,
        smoothing_sigma_m=args.smoothing,
        output_size=args.output_size,
    )

    run_pipeline(
        las_path=args.las,
        gpkg_path=args.gpkg,
        output_dir=args.output_dir,
        config=config,
        gpkg_layer=args.gpkg_layer,
        save_debug_layers=not args.no_debug_layers,
    )


if __name__ == "__main__":
    main()
