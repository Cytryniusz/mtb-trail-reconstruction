"""
mesh_pipeline.py
================
Cleanup + LOD generation dla mesh-a Delaunay 2.5D z chmury LiDAR.

Druga faza pipeline-u po `gps_lidar_int_delaunay.py`:
1. (opcjonalnie) PDAL pre-cleanup chmury punktow (SOR + ROR)
2. Mesh cleanup w Open3D (duplikaty, dlugie krawedzie, Taubin smoothing, dziury)
3. Generacja 4 poziomow LOD przez Quadric Edge Collapse Decimation
4. Eksport do PLY + OBJ w dwoch wariantach:
   - georeferencyjny (oryginalny CRS, do QGIS/CloudCompare)
   - Unity-ready (wycentrowany do origin, by uniknac problemow z float32)
5. Raport statystyk w JSON-ie - gotowy material do rozdzialu eksperymentow w pracy

Uruchomienie:
    # 1. Najpierw wygeneruj surowy mesh:
    python gps_lidar_int_delaunay.py --mesh

    # 2. Potem przepuszczenie przez pipeline:
    python mesh_pipeline.py
    # albo z wlasnymi parametrami:
    python mesh_pipeline.py --max-edge-length 2.5 --lod-targets 120000 40000 12000 4000

Mozna tez importowac jako modul:
    from mesh_pipeline import run_pipeline, CleanupConfig, LODConfig
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import open3d as o3d


# ============================================================================
# 1. PDAL pre-cleanup chmury punktow (opcjonalne)
# ============================================================================

PDAL_AVAILABLE = False
try:
    import pdal  # type: ignore

    PDAL_AVAILABLE = True
except ImportError:
    pass


def pdal_pre_cleanup(
    input_path: Path,
    output_path: Path,
    sor_mean_k: int = 12,
    sor_multiplier: float = 2.2,
    ror_radius: float = 1.0,
    ror_min_neighbors: int = 4,
) -> bool:
    """
    Pre-cleanup chmury punktow LAZ/LAS w PDAL:
    - Statistical Outlier Removal (SOR)
    - Radius Outlier Removal (ROR)
    - Usuniecie klasy 7 (noise) jesli istnieje

    Zwraca True jesli sukces, False jesli PDAL niedostepny.
    Argumenty zgodne z konwencjami PDAL filters.outlier.
    """
    if not PDAL_AVAILABLE:
        print("  PDAL niedostepny -- pomijam pre-cleanup chmury punktow.")
        print("  (Doinstaluj: `pip install pdal` + binarka PDAL z conda-forge)")
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
    print(f"  PDAL: pozostalo {n_points} punktow po SOR + ROR.")
    return True


# ============================================================================
# 2. Mesh cleanup
# ============================================================================


@dataclass
class CleanupConfig:
    """Konfiguracja cleanup mesh-a Delaunay 2.5D."""

    max_edge_length: float = 3.0
    """Maks. dlugosc krawedzi trojkata [m]. Trojkaty z dluzszymi krawedziami
    sa usuwane jako artefakty interpolacji Delaunay przez puste obszary
    (typowo na brzegu bufora GPS)."""

    smoothing_iterations: int = 5
    """Liczba iteracji Taubin smoothing. 0 = wylacz smoothing."""

    taubin_lambda: float = 0.5
    """Parametr lambda Taubin smoothing (zwykle 0.4-0.5)."""

    taubin_mu: float = -0.53
    """Parametr mu Taubin smoothing. Wartosc ujemna kompensuje skurczenie
    powodowane przez lambda. Standardowa para: lambda=0.5, mu=-0.53."""

    fill_holes: bool = False
    """Czy probowac wypelnic male dziury w mesh-u.

    UWAGA: domyslnie WYLACZONE. Open3D `fill_holes` (tensor API) ma tendencje
    do traktowania zewnetrznego brzegu mesh-a jako jednej duzej dziury i
    wypelniania jej gigantycznym trojkatem. Wlaczaj tylko jesli wiesz, ze
    masz drobne wewnetrzne dziury w mesh-u."""

    max_hole_size: int = 100
    """Open3D `fill_holes(hole_size=N)` interpretuje N jako maks. liczbe
    krawedzi boundary loop, NIE jako powierzchnie. Mniejsze N = bezpieczniej."""

    second_edge_filter_pass: bool = True
    """Po smoothingu i fill_holes wykonaj ponowny filtr dlugich krawedzi.
    Safety net na wypadek artefaktow generowanych przez te kroki."""

    remove_unreferenced: bool = True
    """Usun wierzcholki nieuczestniczace w zadnym trojkacie."""


def remove_long_edge_triangles(
    mesh: o3d.geometry.TriangleMesh,
    max_edge: float,
) -> o3d.geometry.TriangleMesh:
    """
    Filtruje trojkaty, ktorych dowolna krawedz przekracza max_edge.

    Typowe artefakty rozwiazane przez ten krok:
    - dlugie 'wiazania' Delaunay przez przerwy w chmurze punktow,
    - trojkaty na brzegu bufora GPS spinajace odlegle punkty.
    """
    vertices = np.asarray(mesh.vertices)
    triangles = np.asarray(mesh.triangles)

    a = vertices[triangles[:, 0]]
    b = vertices[triangles[:, 1]]
    c = vertices[triangles[:, 2]]

    edge1 = np.linalg.norm(b - a, axis=1)
    edge2 = np.linalg.norm(c - b, axis=1)
    edge3 = np.linalg.norm(a - c, axis=1)

    max_edges = np.maximum.reduce([edge1, edge2, edge3])
    keep_mask = max_edges <= max_edge

    filtered = o3d.geometry.TriangleMesh()
    filtered.vertices = mesh.vertices
    filtered.triangles = o3d.utility.Vector3iVector(triangles[keep_mask])

    if mesh.has_vertex_colors():
        filtered.vertex_colors = mesh.vertex_colors
    if mesh.has_vertex_normals():
        filtered.vertex_normals = mesh.vertex_normals

    filtered.remove_unreferenced_vertices()
    return filtered


def mesh_cleanup(
    mesh: o3d.geometry.TriangleMesh,
    config: CleanupConfig,
    verbose: bool = True,
) -> o3d.geometry.TriangleMesh:
    """Pelny cleanup pipeline dla mesh-a Delaunay 2.5D."""
    cleaned = o3d.geometry.TriangleMesh(mesh)
    initial_v = len(cleaned.vertices)
    initial_f = len(cleaned.triangles)

    def log(msg: str) -> None:
        if verbose:
            print(f"  {msg}")

    log(f"Start: {initial_v:,} v / {initial_f:,} f")

    # 1. Usuniecie duplikatow i zdegenerowanych trojkatow
    cleaned.remove_duplicated_vertices()
    cleaned.remove_duplicated_triangles()
    cleaned.remove_degenerate_triangles()
    if config.remove_unreferenced:
        cleaned.remove_unreferenced_vertices()
    log(f"Po dedup + degenerate: {len(cleaned.vertices):,} v / "
        f"{len(cleaned.triangles):,} f")

    # 2. Filtr dlugich krawedzi (artefakty Delaunay na brzegach)
    if config.max_edge_length > 0:
        cleaned = remove_long_edge_triangles(cleaned, config.max_edge_length)
        log(f"Po filtrze krawedzi (>{config.max_edge_length}m): "
            f"{len(cleaned.vertices):,} v / {len(cleaned.triangles):,} f")

    # 3. Wypelnienie malych dziur (Open3D tensor API)
    if config.fill_holes and len(cleaned.triangles) > 0:
        try:
            tmesh = o3d.t.geometry.TriangleMesh.from_legacy(cleaned)
            tmesh = tmesh.fill_holes(hole_size=config.max_hole_size)
            cleaned = tmesh.to_legacy()
            log(f"Po fill_holes (max {config.max_hole_size} m^2): "
                f"{len(cleaned.vertices):,} v / {len(cleaned.triangles):,} f")
        except Exception as exc:
            log(f"fill_holes pominiete: {exc}")

    # 4. Taubin smoothing (zachowuje feature edges lepiej niz Laplacian)
    if config.smoothing_iterations > 0:
        cleaned = cleaned.filter_smooth_taubin(
            number_of_iterations=config.smoothing_iterations,
            lambda_filter=config.taubin_lambda,
            mu=config.taubin_mu,
        )
        # Taubin moze wyjechac z kolorami poza [0,1] -- clipujemy
        if cleaned.has_vertex_colors():
            colors = np.asarray(cleaned.vertex_colors)
            cleaned.vertex_colors = o3d.utility.Vector3dVector(
                np.clip(colors, 0.0, 1.0)
            )
        log(f"Po Taubin smoothing ({config.smoothing_iterations} iter, "
            f"lambda={config.taubin_lambda}, mu={config.taubin_mu})")

    # 5. Safety net: powtorny filtr dlugich krawedzi po fill_holes / smoothing
    if config.second_edge_filter_pass and config.max_edge_length > 0:
        before = len(cleaned.triangles)
        cleaned = remove_long_edge_triangles(cleaned, config.max_edge_length)
        after = len(cleaned.triangles)
        if before != after:
            log(f"Drugi filtr krawedzi: usuneto {before - after:,} trojkatow "
                f"-> {after:,} f")

    # 6. Normalne i orientacja
    cleaned.compute_vertex_normals()
    cleaned.compute_triangle_normals()
    try:
        cleaned.orient_triangles()
    except Exception:
        pass

    final_v = len(cleaned.vertices)
    final_f = len(cleaned.triangles)
    reduction = (1 - final_f / max(initial_f, 1)) * 100
    log(f"Wynik: {final_v:,} v / {final_f:,} f (redukcja {reduction:.1f}%)")

    return cleaned


# ============================================================================
# 3. LOD generation (Quadric Edge Collapse Decimation)
# ============================================================================


@dataclass
class LODConfig:
    """Konfiguracja generacji LOD-ow.

    Wartosci domyslne dobrane pod scene Unity z URP:
    - LOD0: pierwszy plan pod kamera (0-80 m)
    - LOD1: srednia odleglosc (80-250 m)
    - LOD2: tlo (250-800 m)
    - LOD3: skybox / streaming culling (>800 m)
    """

    targets: tuple = (150_000, 50_000, 15_000, 5_000)

    def names(self) -> tuple[str, ...]:
        return tuple(f"LOD{i}" for i in range(len(self.targets)))


def generate_lods(
    mesh: o3d.geometry.TriangleMesh,
    config: LODConfig,
    verbose: bool = True,
    max_edge_length: float = 3.0,
) -> dict[str, o3d.geometry.TriangleMesh]:
    """
    Generuje LOD-y od najbardziej szczegolowego do najprostszego.
    Kazdy kolejny LOD jest decymowany z poprzedniego (kaskadowo).
    """

    def log(msg: str) -> None:
        if verbose:
            print(f"  {msg}")

    lods: dict[str, o3d.geometry.TriangleMesh] = {}
    current = o3d.geometry.TriangleMesh(mesh)
    current_faces = len(current.triangles)

    for name, target in zip(config.names(), config.targets):
        if target >= current_faces:
            log(f"{name}: zachowuje {current_faces:,} f (target {target:,} >= aktualne)")
            lod = o3d.geometry.TriangleMesh(current)
        else:
            log(f"{name}: decymacja {current_faces:,} -> {target:,} f...")
            lod = current.simplify_quadric_decimation(
                target_number_of_triangles=target
            )

        # Post-decimation filtr krawedzi: QECD moze generowac nowe
        # dlugie krawedzie na brzegach przy kolapsowaniu trojkatow
        if max_edge_length > 0:
            before_filter = len(lod.triangles)
            lod = remove_long_edge_triangles(lod, max_edge_length)
            after_filter = len(lod.triangles)
            if before_filter != after_filter:
                log(f"  Filtr krawedzi >{max_edge_length}m: "
                    f"usunieto {before_filter - after_filter:,} trojkatow")

        lod.compute_vertex_normals()
        lod.compute_triangle_normals()
        actual = len(lod.triangles)
        log(f"  -> {len(lod.vertices):,} v / {actual:,} f")

        lods[name] = lod
        current = lod
        current_faces = actual

    return lods


# ============================================================================
# 4. Statystyki i raport
# ============================================================================


def compute_mesh_stats(mesh: o3d.geometry.TriangleMesh) -> dict:
    """
    Zwraca slownik metryk dla raportu (gotowe do wstawienia w tabeli w pracy).
    """
    vertices = np.asarray(mesh.vertices)
    triangles = np.asarray(mesh.triangles)
    n_v = int(len(vertices))
    n_f = int(len(triangles))

    if n_f == 0:
        return {"n_vertices": n_v, "n_faces": 0}

    a = vertices[triangles[:, 0]]
    b = vertices[triangles[:, 1]]
    c = vertices[triangles[:, 2]]
    areas = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)

    normals_raw = np.cross(b - a, c - a)
    norms = np.linalg.norm(normals_raw, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    normals = normals_raw / norms
    slope_deg = np.degrees(np.arccos(np.clip(np.abs(normals[:, 2]), 0, 1)))

    bbox = mesh.get_axis_aligned_bounding_box()
    extent = bbox.get_extent()
    surface_area = float(areas.sum())

    return {
        "n_vertices": n_v,
        "n_faces": n_f,
        "bbox_extent_m": [round(float(x), 2) for x in extent],
        "surface_area_m2": round(surface_area, 2),
        "vertices_per_m2": round(n_v / max(surface_area, 1e-6), 3),
        "triangle_area_median_m2": round(float(np.median(areas)), 4),
        "triangle_area_p95_m2": round(float(np.percentile(areas, 95)), 4),
        "triangle_area_max_m2": round(float(areas.max()), 4),
        "slope_mean_deg": round(float(slope_deg.mean()), 2),
        "slope_median_deg": round(float(np.median(slope_deg)), 2),
        "edge_manifold": bool(mesh.is_edge_manifold(allow_boundary_edges=True)),
        "vertex_manifold": bool(mesh.is_vertex_manifold()),
    }


def print_summary_table(report: dict) -> None:
    """Tabela porownawcza LOD-ow w konsoli (gotowa do wstawienia w pracy)."""
    print()
    print("=" * 86)
    print("PODSUMOWANIE — KASKADA LOD")
    print("=" * 86)

    headers = ["Etap", "Vertices", "Faces", "Pole [m^2]", "v/m^2",
               "MaxTri [m^2]", "Manifold"]
    widths = [12, 12, 12, 14, 10, 14, 10]

    def row(values: list) -> str:
        return "".join(str(v).ljust(w) for v, w in zip(values, widths))

    print(row(headers))
    print("-" * sum(widths))

    rows_data = [
        ("ZRODLO", report["raw_stats"]),
        ("CLEANED", report["cleaned_stats"]),
    ]
    for name, data in report["lods"].items():
        rows_data.append((name, data["stats"]))

    for label, stats in rows_data:
        manifold_ok = stats.get("edge_manifold") and stats.get("vertex_manifold")
        max_tri = stats.get("triangle_area_max_m2", "-")
        print(row([
            label,
            f"{stats['n_vertices']:,}",
            f"{stats['n_faces']:,}",
            f"{stats.get('surface_area_m2', 0):,.1f}",
            f"{stats.get('vertices_per_m2', 0):.2f}",
            f"{max_tri:,.2f}" if isinstance(max_tri, (int, float)) else max_tri,
            "OK" if manifold_ok else "X",
        ]))
    print("=" * 86)


# ============================================================================
# 5. Eksport
# ============================================================================


def export_mesh_variants(
    mesh: o3d.geometry.TriangleMesh,
    output_dir: Path,
    name: str,
    unity_centroid: Optional[np.ndarray] = None,
    formats: tuple = ("ply", "obj"),
) -> dict[str, str]:
    """
    Eksportuje mesh w dwoch wariantach:
    - mesh_NAME.<ext>         -- georeferencja zachowana (oryginalny CRS)
    - mesh_NAME_unity.<ext>   -- wycentrowany do origin (XY bliskie 0, Z = elewacja)

    Drugi wariant jest niezbedny dla Unity: float32 zaczyna gubic precyzje
    powyzej ok. 10^5, a wspolrzedne EPSG:2180 sa rzedu 5x10^5.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}

    for fmt in formats:
        out_geo = output_dir / f"mesh_{name}.{fmt}"
        o3d.io.write_triangle_mesh(str(out_geo), mesh, write_ascii=False)
        paths[f"geo_{fmt}"] = str(out_geo)

    if unity_centroid is not None:
        unity_mesh = o3d.geometry.TriangleMesh(mesh)
        unity_mesh.translate(-unity_centroid)
        for fmt in formats:
            out_unity = output_dir / f"mesh_{name}_unity.{fmt}"
            o3d.io.write_triangle_mesh(str(out_unity), unity_mesh, write_ascii=False)
            paths[f"unity_{fmt}"] = str(out_unity)

    return paths


# ============================================================================
# 6. Pipeline glowny
# ============================================================================


def run_pipeline(
    input_mesh_path: Path,
    output_dir: Path,
    cleanup_config: Optional[CleanupConfig] = None,
    lod_config: Optional[LODConfig] = None,
    export_formats: tuple = ("ply", "obj"),
    save_intermediate: bool = True,
) -> dict:
    """
    Pelny pipeline: wczytaj mesh -> cleanup -> LOD-y -> eksport -> raport JSON.

    Zwraca slownik raportu (taki sam jak zapisany do pipeline_report.json).
    """
    cleanup_config = cleanup_config or CleanupConfig()
    lod_config = lod_config or LODConfig()
    output_dir.mkdir(parents=True, exist_ok=True)

    print()
    print("=" * 86)
    print(f"  Mesh pipeline: {input_mesh_path.name}")
    print(f"  Output: {output_dir.resolve()}")
    print("=" * 86)

    # ---- Wczytanie
    raw_mesh = o3d.io.read_triangle_mesh(str(input_mesh_path))
    if len(raw_mesh.triangles) == 0:
        raise ValueError(f"Mesh wejsciowy pusty: {input_mesh_path}")

    raw_stats = compute_mesh_stats(raw_mesh)
    print(f"\nWczytano: {raw_stats['n_vertices']:,} v / "
          f"{raw_stats['n_faces']:,} f, "
          f"bbox {raw_stats['bbox_extent_m']} m")

    # Centroid dla wersji Unity (zachowamy go w raporcie do reverse-mapy w Unity)
    bbox = raw_mesh.get_axis_aligned_bounding_box()
    centroid = np.asarray(bbox.get_center())
    print(f"Centroid (translacja Unity): "
          f"X={centroid[0]:.1f}, Y={centroid[1]:.1f}, Z={centroid[2]:.1f}")

    # ---- 1. Cleanup
    print("\n[1/3] Cleanup mesh-a...")
    cleaned = mesh_cleanup(raw_mesh, cleanup_config)
    cleaned_stats = compute_mesh_stats(cleaned)

    if save_intermediate:
        intermediate_path = output_dir / "mesh_cleaned.ply"
        o3d.io.write_triangle_mesh(str(intermediate_path), cleaned, write_ascii=False)
        print(f"  Zapisano (intermediate): {intermediate_path.name}")

    # ---- 2. LOD-y
    print("\n[2/3] Generowanie LOD-ow...")
    lods = generate_lods(
        cleaned,
        lod_config,
        max_edge_length=cleanup_config.max_edge_length,
    )

    # ---- 3. Eksport + raport
    print("\n[3/3] Eksport LOD-ow...")
    report: dict = {
        "input": str(input_mesh_path.resolve()),
        "output_dir": str(output_dir.resolve()),
        "unity_centroid": [float(c) for c in centroid],
        "cleanup_config": asdict(cleanup_config),
        "lod_config": asdict(lod_config),
        "raw_stats": raw_stats,
        "cleaned_stats": cleaned_stats,
        "lods": {},
    }

    for name, lod_mesh in lods.items():
        paths = export_mesh_variants(
            lod_mesh,
            output_dir,
            name,
            unity_centroid=centroid,
            formats=export_formats,
        )
        stats = compute_mesh_stats(lod_mesh)
        report["lods"][name] = {"stats": stats, "files": paths}
        print(f"  {name}: {stats['n_faces']:,} f -> "
              f"{len(paths)} plikow ({', '.join(export_formats)})")

    # ---- Raport JSON
    report_path = output_dir / "pipeline_report.json"
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
    print(f"\nRaport: {report_path}")

    print_summary_table(report)

    return report


# ============================================================================
# CLI
# ============================================================================


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Cleanup + LOD pipeline dla mesh-a Delaunay 2.5D. "
            "Druga faza po gps_lidar_int_delaunay.py."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("results/mesh_delaunay.ply"),
        help="Sciezka do mesh-a wejsciowego.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("results/lod"),
        help="Katalog wyjsciowy dla LOD-ow.",
    )
    parser.add_argument(
        "--max-edge-length", type=float, default=3.0,
        help="Maks. dlugosc krawedzi trojkata [m].",
    )
    parser.add_argument(
        "--smoothing-iter", type=int, default=5,
        help="Liczba iteracji Taubin smoothing (0 = wylacz).",
    )
    parser.add_argument("--taubin-lambda", type=float, default=0.5)
    parser.add_argument("--taubin-mu", type=float, default=-0.53)
    parser.add_argument(
        "--fill-holes", action="store_true",
        help="Wlacz wypelnianie dziur (uwaga: Open3D moze pomylic boundary z dziura).",
    )
    parser.add_argument(
        "--max-hole-size", type=int, default=100,
        help="Maks. liczba krawedzi boundary loop dla fill_holes.",
    )
    parser.add_argument(
        "--lod-targets", type=int, nargs=4,
        default=[150_000, 50_000, 15_000, 5_000],
        metavar=("LOD0", "LOD1", "LOD2", "LOD3"),
        help="Liczba trojkatow w kolejnych poziomach LOD.",
    )
    parser.add_argument(
        "--formats", nargs="+", default=["ply", "obj"],
        choices=["ply", "obj"],
        help="Formaty eksportu.",
    )
    parser.add_argument(
        "--no-intermediate", action="store_true",
        help="Nie zapisuj mesh-a posredniego po samym cleanup.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not args.input.exists():
        raise FileNotFoundError(
            f"Brak pliku wejsciowego: {args.input}.\n"
            f"Uruchom najpierw: python gps_lidar_int_delaunay.py --mesh"
        )

    cleanup_config = CleanupConfig(
        max_edge_length=args.max_edge_length,
        smoothing_iterations=args.smoothing_iter,
        taubin_lambda=args.taubin_lambda,
        taubin_mu=args.taubin_mu,
        fill_holes=args.fill_holes,
        max_hole_size=args.max_hole_size,
    )
    lod_config = LODConfig(targets=tuple(args.lod_targets))

    run_pipeline(
        input_mesh_path=args.input,
        output_dir=args.output_dir,
        cleanup_config=cleanup_config,
        lod_config=lod_config,
        export_formats=tuple(args.formats),
        save_intermediate=not args.no_intermediate,
    )


if __name__ == "__main__":
    main()
