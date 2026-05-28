from __future__ import annotations

import argparse

from pathlib import Path

import numpy as np

from mtb_terrain.viz.slope_coloring import compute_slope_colors
from mtb_terrain.viz.elevation_profile import show_elevation_profile
from mtb_terrain.mesh.terrain import build_delaunay_mesh, build_poisson_mesh, register_mesh_toggle


# Domyslne sciezki sa wzgledne wzgledem cwd (czyli korzenia repo przy uruchamianiu skryptow z scripts/).
# Mozesz je nadpisac przez argumenty CLI albo edytujac configs/default.yaml.
_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_GPKG_PATH = _REPO_ROOT / "processed" / "flow--jump_epsg2180.gpkg"
DEFAULT_PLY_PATH = _REPO_ROOT / "processed" / "78142_1411124_M-34-89-C-c-2-3-2_ground.laz"
DEFAULT_CROPPED_PLY_PATH = _REPO_ROOT / "processed" / "flow--jump_buffer30m_ground.ply"
DEFAULT_TRACK_LAYER = "track_points"
DEFAULT_EPSG = "EPSG:2180"
DEFAULT_RESULTS_DIR = _REPO_ROOT / "results"


def import_dependencies():
    try:
        import geopandas as gpd
        import open3d as o3d
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "Do integracji GPS + LiDAR potrzebne sa biblioteki geopandas i open3d. "
            "Zainstaluj je w aktywnym srodowisku, np.: pip install geopandas open3d"
        ) from exc

    return gpd, o3d


def load_point_cloud(path: Path, o3d):
    """Wczytuje chmure punktow z PLY/LAS/LAZ. Dla LAZ/LAS uzywa laspy i zachowuje kolory RGB."""
    if path.suffix.lower() in (".laz", ".las"):
        try:
            import laspy
        except ImportError:
            raise ImportError(
                "Do odczytu LAZ/LAS potrzebna jest biblioteka laspy. "
                "Zainstaluj: pip install laspy[lazrs]"
            )
        las = laspy.read(str(path))
        xyz = np.column_stack([
            np.asarray(las.x, dtype=float),
            np.asarray(las.y, dtype=float),
            np.asarray(las.z, dtype=float),
        ])
        pc = o3d.geometry.PointCloud()
        pc.points = o3d.utility.Vector3dVector(xyz)

        dims = set(las.point_format.dimension_names)
        if {"red", "green", "blue"}.issubset(dims):
            r = np.asarray(las.red, dtype=float)
            g = np.asarray(las.green, dtype=float)
            b = np.asarray(las.blue, dtype=float)
            scale = 65535.0 if r.max() > 255 else 255.0
            pc.colors = o3d.utility.Vector3dVector(
                np.column_stack([r, g, b]) / scale
            )
        return pc

    return o3d.io.read_point_cloud(str(path))


def validate_input_path(path: Path, label: str) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Nie znaleziono {label}: {path}")


def load_gps_xy(gpkg_path: Path, layer: str) -> tuple[np.ndarray, object]:
    gpd, _ = import_dependencies()
    track = gpd.read_file(gpkg_path, layer=layer)

    if track.empty:
        raise ValueError(f"Warstwa '{layer}' w pliku {gpkg_path} jest pusta.")

    if track.crs is None:
        print(f"Uwaga: warstwa '{layer}' nie ma CRS. Zakladam {DEFAULT_EPSG}.")
    elif track.crs.to_string().upper() != DEFAULT_EPSG:
        print(f"Transformacja GPS: {track.crs} -> {DEFAULT_EPSG}")
        track = track.to_crs(DEFAULT_EPSG)

    if not all(track.geometry.geom_type == "Point"):
        raise ValueError(
            f"Warstwa '{layer}' musi zawierac punkty. "
            "Uzyj domyslnej warstwy 'track_points' z flow--jump_epsg2180.gpkg."
        )

    if "point_id" in track.columns:
        track = track.sort_values("point_id")
    elif "distance_km" in track.columns:
        track = track.sort_values("distance_km")

    gps_xy = np.array([(point.x, point.y) for point in track.geometry], dtype=float)
    if len(gps_xy) < 2:
        raise ValueError("Do nalozenia sladu potrzeba co najmniej dwoch punktow GPS.")

    return gps_xy, track

def smooth_gps_track(
    gps_xy: np.ndarray,
    window_length: int = 11,
    polyorder: int = 3,
) -> np.ndarray:
    """
    Wygładzenie sladu GPS metoda Savitzky-Golay.

    Savitzky-Golay zachowuje ksztalt lukow (szczyty, doliny) lepiej niz
    Gaussian - wazne dla profilu nachylenia i analizy trasy MTB.
    Okno dziala na wspolrzednych X i Y niezaleznie.
    """
    try:
        from scipy.signal import savgol_filter
    except ImportError:
        print("Uwaga: scipy niedostepne, pomijam wygładzenie GPS.")
        return gps_xy

    n = len(gps_xy)
    wl = min(window_length, n if n % 2 == 1 else n - 1)
    if wl < polyorder + 2:
        print(f"Uwaga: za malo punktow GPS ({n}) na wygładzenie. Pomijam.")
        return gps_xy

    smoothed = np.column_stack([
        savgol_filter(gps_xy[:, 0], wl, polyorder),
        savgol_filter(gps_xy[:, 1], wl, polyorder),
    ])

    delta = float(np.sqrt(((smoothed - gps_xy) ** 2).sum(axis=1)).mean())
    print(f"Wygładzenie GPS (Savitzky-Golay, window={wl}, order={polyorder}): "
          f"srednie przesuniecie XY = {delta:.3f} m")
    return smoothed

# Kluczowe punkty kontrolne palety Viridis (percepcyjnie jednorodna, czytelna w skali szarosci).
_VIRIDIS = np.array([
    [0.267, 0.005, 0.329],
    [0.283, 0.141, 0.458],
    [0.254, 0.265, 0.530],
    [0.207, 0.372, 0.553],
    [0.164, 0.471, 0.558],
    [0.128, 0.566, 0.551],
    [0.135, 0.659, 0.518],
    [0.267, 0.749, 0.441],
    [0.478, 0.821, 0.318],
    [0.741, 0.873, 0.150],
    [0.993, 0.906, 0.144],
], dtype=float)


def color_points_by_height(points: np.ndarray) -> np.ndarray:
    z_values = points[:, 2]
    z_min = float(np.nanmin(z_values))
    z_max = float(np.nanmax(z_values))
    z_range = max(z_max - z_min, 1e-9)
    normalized = np.clip((z_values - z_min) / z_range, 0.0, 1.0)

    n = len(_VIRIDIS)
    idx = normalized * (n - 1)
    lo = np.floor(idx).astype(int).clip(0, n - 2)
    t = (idx - lo)[:, None]
    return _VIRIDIS[lo] * (1.0 - t) + _VIRIDIS[lo + 1] * t


def points_near_gps_polyline_mask(
    points_xy: np.ndarray,
    gps_xy: np.ndarray,
    buffer_distance: float,
    chunk_size: int = 50_000,
) -> np.ndarray:
    segments_start = gps_xy[:-1]
    segments_end = gps_xy[1:]
    segment_vectors = segments_end - segments_start
    segment_lengths_sq = np.sum(segment_vectors**2, axis=1)
    valid_segments = segment_lengths_sq > 1e-12

    segments_start = segments_start[valid_segments]
    segment_vectors = segment_vectors[valid_segments]
    segment_lengths_sq = segment_lengths_sq[valid_segments]

    if len(segments_start) == 0:
        raise ValueError("Slad GPS nie zawiera poprawnych odcinkow do buforowania.")

    buffer_distance_sq = buffer_distance**2
    keep_mask = np.zeros(len(points_xy), dtype=bool)

    for start in range(0, len(points_xy), chunk_size):
        chunk = points_xy[start : start + chunk_size]
        min_distances_sq = np.full(len(chunk), np.inf, dtype=float)

        for segment_start, segment_vector, segment_length_sq in zip(
            segments_start,
            segment_vectors,
            segment_lengths_sq,
        ):
            point_vectors = chunk - segment_start
            t = np.sum(point_vectors * segment_vector, axis=1) / segment_length_sq
            t = np.clip(t, 0.0, 1.0)
            projection = segment_start + t[:, None] * segment_vector
            distances_sq = np.sum((chunk - projection) ** 2, axis=1)
            min_distances_sq = np.minimum(min_distances_sq, distances_sq)

        keep_mask[start : start + len(chunk)] = min_distances_sq <= buffer_distance_sq

    return keep_mask


def nearest_lidar_z(
    lidar_points: np.ndarray,
    gps_xy: np.ndarray,
    chunk_size: int = 50_000,
) -> tuple[np.ndarray, np.ndarray]:
    nearest_z = np.empty(len(gps_xy), dtype=float)
    nearest_distance = np.full(len(gps_xy), np.inf, dtype=float)

    for start in range(0, len(lidar_points), chunk_size):
        chunk = lidar_points[start : start + chunk_size]
        distances_sq = (
            (chunk[:, 0, None] - gps_xy[None, :, 0]) ** 2
            + (chunk[:, 1, None] - gps_xy[None, :, 1]) ** 2
        )
        chunk_nearest_indices = np.argmin(distances_sq, axis=0)
        chunk_distances_sq = distances_sq[chunk_nearest_indices, np.arange(len(gps_xy))]
        better_mask = chunk_distances_sq < nearest_distance
        nearest_distance[better_mask] = chunk_distances_sq[better_mask]
        nearest_z[better_mask] = chunk[chunk_nearest_indices[better_mask], 2]

    return nearest_z, np.sqrt(nearest_distance)


def gps_z_from_attribute(track, fallback_z: np.ndarray) -> np.ndarray:
    if "elevation" not in track.columns:
        print("Brak kolumny 'elevation' w GPKG. Uzywam wysokosci z LiDAR.")
        return fallback_z

    elevations = np.array(track["elevation"], dtype=float)
    missing_mask = ~np.isfinite(elevations)
    if missing_mask.any():
        elevations[missing_mask] = fallback_z[missing_mask]
        print("Czesc punktow GPS nie ma wysokosci. Braki uzupelniono z LiDAR.")

    return elevations


def make_cylinder_between(o3d, start: np.ndarray, end: np.ndarray, radius: float):
    vector = end - start
    length = float(np.linalg.norm(vector))
    if length <= 1e-9:
        return None

    cylinder = o3d.geometry.TriangleMesh.create_cylinder(
        radius=radius,
        height=length,
        resolution=12,
        split=1,
    )
    cylinder.paint_uniform_color([1.0, 0.05, 0.02])

    direction = vector / length
    z_axis = np.array([0.0, 0.0, 1.0])
    rotation_axis = np.cross(z_axis, direction)
    rotation_axis_norm = float(np.linalg.norm(rotation_axis))

    if rotation_axis_norm > 1e-9:
        rotation_axis = rotation_axis / rotation_axis_norm
        angle = float(np.arccos(np.clip(np.dot(z_axis, direction), -1.0, 1.0)))
        rotation = o3d.geometry.get_rotation_matrix_from_axis_angle(rotation_axis * angle)
        cylinder.rotate(rotation, center=(0.0, 0.0, 0.0))
    elif direction[2] < 0:
        rotation = o3d.geometry.get_rotation_matrix_from_axis_angle(
            np.array([np.pi, 0.0, 0.0])
        )
        cylinder.rotate(rotation, center=(0.0, 0.0, 0.0))

    cylinder.translate((start + end) / 2.0)
    return cylinder


def build_track_geometry(
    o3d,
    gps_points_local: np.ndarray,
    tube_radius: float,
    point_radius: float,
    show_points: bool,
    track_style: str,
    slope_colors: np.ndarray | None = None,
) -> list:
    geometries = []
    if track_style == "line":
        lines = [[index, index + 1] for index in range(len(gps_points_local) - 1)]
        line_set = o3d.geometry.LineSet(
            points=o3d.utility.Vector3dVector(gps_points_local),
            lines=o3d.utility.Vector2iVector(lines),
        )
        if slope_colors is not None:
            line_set.colors = o3d.utility.Vector3dVector(slope_colors.tolist())
        else:
            line_set.colors = o3d.utility.Vector3dVector([[1.0, 0.0, 0.0] for _ in lines])
        geometries.append(line_set)
        return geometries

    for idx, (start, end) in enumerate(zip(gps_points_local[:-1], gps_points_local[1:])):
        cylinder = make_cylinder_between(o3d, start, end, tube_radius)
        if cylinder is not None:
            if slope_colors is not None:
                cylinder.paint_uniform_color(slope_colors[idx].tolist())
            geometries.append(cylinder)

    if show_points:
        for point in gps_points_local:
            sphere = o3d.geometry.TriangleMesh.create_sphere(
                radius=point_radius,
                resolution=10,
            )
            sphere.paint_uniform_color([1.0, 0.0, 0.0])
            sphere.translate(point)
            geometries.append(sphere)

    return geometries


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Integracja sladu GPS EPSG:2180 z odfiltrowana chmura punktow PLY."
    )
    parser.add_argument(
        "--gpkg",
        type=Path,
        default=DEFAULT_GPKG_PATH,
        help="Sciezka do GPKG ze sladem GPS w EPSG:2180.",
    )
    parser.add_argument(
        "--ply",
        type=Path,
        default=DEFAULT_PLY_PATH,
        help="Sciezka do chmury punktow (PLY, LAZ, LAS) w EPSG:2180.",
    )
    parser.add_argument(
        "--layer",
        default=DEFAULT_TRACK_LAYER,
        help="Warstwa punktowa GPKG z kolejnymi punktami sladu.",
    )
    parser.add_argument(
        "--z-source",
        choices=("lidar", "gps"),
        default="lidar",
        help="Zrodlo wysokosci sladu: najblizszy LiDAR albo kolumna elevation z GPKG.",
    )
    parser.add_argument(
        "--track-offset",
        type=float,
        default=0.5,
        help="Podniesienie sladu nad terenem w metrach (0 = slad lezy na powierzchni).",
    )
    parser.add_argument(
        "--voxel-size",
        type=float,
        default=0.0,
        help="Opcjonalna decymacja chmury do wizualizacji, np. 0.5 albo 1.0 m.",
    )
    parser.add_argument(
        "--tube-radius",
        type=float,
        default=1.0,
        help="Promien czerwonej rury reprezentujacej slad GPS.",
    )
    parser.add_argument(
        "--point-radius",
        type=float,
        default=2.0,
        help="Promien czerwonych punktow sladu GPS.",
    )
    parser.add_argument(
        "--hide-gps-points",
        action="store_true",
        help="Pokaz tylko linie sladu, bez czerwonych punktow.",
    )
    parser.add_argument(
        "--track-style",
        choices=("line", "tube"),
        default="line",
        help="Sposob rysowania sladu GPS. Domyslnie cienka linia.",
    )
    parser.add_argument(
        "--buffer-distance",
        type=float,
        default=30.0,
        help="Bufor GPS w metrach uzywany do wyciecia chmury punktow.",
    )
    parser.add_argument(
        "--no-buffer-crop",
        action="store_true",
        help="Nie przycinaj chmury do bufora GPS.",
    )
    parser.add_argument(
        "--output-ply",
        type=Path,
        default=DEFAULT_CROPPED_PLY_PATH,
        help="Sciezka zapisu chmury wycietej do bufora GPS.",
    )
    parser.add_argument(
        "--no-save-cropped",
        action="store_true",
        help="Nie zapisuj wycietej chmury PLY.",
    )
    parser.add_argument(
        "--max-lidar-distance",
        type=float,
        default=None,
        help=(
            "Opcjonalnie pokaz tylko punkty GPS oddalone od najblizszego punktu "
            "LiDAR nie wiecej niz podana liczba metrow, np. 30."
        ),
    )
    parser.add_argument(
        "--slope-coloring",
        action="store_true",
        help="Koloruj slad GPS wg 4 klas nachylenia zamiast jednolitego czerwonego.",
    )
    parser.add_argument(
        "--show-profile",
        action="store_true",
        help="Pokaz profil wysokosciowy trasy (matplotlib).",
    )
    parser.add_argument(
        "--save-profile",
        type=Path,
        default=None,
        help="Zapisz profil wysokosciowy jako PNG pod podana sciezka.",
    )
    parser.add_argument(
        "--mesh",
        action="store_true",
        help="Generuj siatki terenu Delaunay + Poisson (klawisz M w oknie Open3D).",
    )
    parser.add_argument(
        "--post-process", 
        action="store_true",
        help="Po wygenerowaniu mesh-a uruchom mesh_pipeline (cleanup + LOD)."
    )
    parser.add_argument(
        "--smooth-gps",
        action="store_true",
        help="Wygladz slad GPS metoda Savitzky-Golay przed korekcja Z.",
    )
    parser.add_argument(
        "--smooth-window",
        type=int,
        default=11,
        help="Dlugosc okna Savitzky-Golay (nieparzysta, im wieksza tym gladziej).",
    )
    parser.add_argument(
        "--smooth-polyorder",
        type=int,
        default=3,
        help="Stopien wielomianu Savitzky-Golay (3 = kubiczny).",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    validate_input_path(args.gpkg, "pliku GPKG")
    validate_input_path(args.ply, "pliku chmury punktow")

    gpd, o3d = import_dependencies()
    gps_xy, track = load_gps_xy(args.gpkg, args.layer)

    if args.smooth_gps:
        gps_xy = smooth_gps_track(gps_xy, args.smooth_window, args.smooth_polyorder)

    point_cloud = load_point_cloud(args.ply, o3d)

    if point_cloud.is_empty():
        raise ValueError(f"Chmura punktow jest pusta albo nieczytelna: {args.ply}")

    original_lidar_count = len(point_cloud.points)

    if not args.no_buffer_crop:
        lidar_points_before_crop = np.asarray(point_cloud.points)
        buffer_mask = points_near_gps_polyline_mask(
            lidar_points_before_crop[:, :2],
            gps_xy,
            args.buffer_distance,
        )
        kept_indices = np.flatnonzero(buffer_mask).tolist()
        if not kept_indices:
            raise ValueError(
                "Bufor GPS nie przecina chmury punktow. "
                "Sprawdz pokrycie danych albo zwieksz --buffer-distance."
            )

        point_cloud = point_cloud.select_by_index(kept_indices)
        if not args.no_save_cropped:
            args.output_ply.parent.mkdir(parents=True, exist_ok=True)
            o3d.io.write_point_cloud(str(args.output_ply), point_cloud)

    if args.voxel_size > 0:
        point_cloud = point_cloud.voxel_down_sample(voxel_size=args.voxel_size)

    lidar_points = np.asarray(point_cloud.points)
    if not point_cloud.has_colors():
        point_cloud.colors = o3d.utility.Vector3dVector(color_points_by_height(lidar_points))

    lidar_z, lidar_distances = nearest_lidar_z(lidar_points, gps_xy)
    gps_z = lidar_z if args.z_source == "lidar" else gps_z_from_attribute(track, lidar_z)
    gps_points = np.column_stack([gps_xy, gps_z + args.track_offset])

    effective_max_lidar_distance = args.max_lidar_distance
    if effective_max_lidar_distance is None and not args.no_buffer_crop:
        effective_max_lidar_distance = args.buffer_distance

    if effective_max_lidar_distance is not None:
        close_mask = lidar_distances <= effective_max_lidar_distance
        if close_mask.sum() < 2:
            raise ValueError(
                "Po przycieciu widoku GPS zostalo mniej niz 2 punkty GPS. "
                "Zwieksz prog odleglosci albo uruchom bez przyciecia."
            )
        gps_points = gps_points[close_mask]
        lidar_distances = lidar_distances[close_mask]

    center = lidar_points.mean(axis=0)
    point_cloud.translate(-center)
    gps_points_local = gps_points - center

    slope_colors = None
    if args.slope_coloring or args.show_profile or args.save_profile is not None:
        slope_colors = compute_slope_colors(gps_points)

    track_geometries = build_track_geometry(
        o3d=o3d,
        gps_points_local=gps_points_local,
        tube_radius=args.tube_radius,
        point_radius=args.point_radius,
        show_points=not args.hide_gps_points,
        track_style=args.track_style,
        slope_colors=slope_colors,
    )

    if args.show_profile or args.save_profile is not None:
        if slope_colors is None:
            slope_colors = compute_slope_colors(gps_points)
        profile_path = args.save_profile
        if args.show_profile and profile_path is None:
            DEFAULT_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
            profile_path = DEFAULT_RESULTS_DIR / "elevation_profile.png"
        show_elevation_profile(
            gps_points,
            slope_colors,
            save_path=profile_path,
        )
        if profile_path is not None:
            print(f"Zapisano profil wysokosciowy: {profile_path.resolve()}")

    axes = o3d.geometry.TriangleMesh.create_coordinate_frame(size=25.0, origin=(0, 0, 0))
    geometries = [point_cloud, axes, *track_geometries]

    has_rgb = point_cloud.has_colors()
    print("Integracja GPS + LiDAR")
    print(f"Chmura punktow: {args.ply.resolve()}")
    print(f"Kolory chmury:  {'RGB z pliku' if has_rgb else 'Viridis wg wysokosci'}")
    print(f"Slad GPKG: {args.gpkg.resolve()} / warstwa: {args.layer}")
    print(f"Liczba punktow chmury przed wycieciem: {original_lidar_count}")
    if not args.no_buffer_crop:
        print(f"Bufor GPS do wyciecia chmury: {args.buffer_distance:.2f} m")
        print(f"Zapis wycietej chmury: {args.output_ply.resolve()}")
    print(f"Liczba punktow chmury w wizualizacji: {len(lidar_points)}")
    print(f"Liczba punktow GPS: {len(gps_points)}")
    if effective_max_lidar_distance is not None:
        print(f"Przyciecie GPS do odleglosci od LiDAR <= {effective_max_lidar_distance:.2f} m")
    print(
        "Odleglosc XY punktow GPS od najblizszego punktu LiDAR: "
        f"min={np.min(lidar_distances):.2f} m, "
        f"avg={np.mean(lidar_distances):.2f} m, "
        f"max={np.max(lidar_distances):.2f} m"
    )
    print("Czerwony slad jest przesuniety lokalnie razem z chmura tylko na potrzeby renderingu.")

    delaunay = None
    poisson = None
    if args.mesh:
        print("Budowanie siatek terenu... (moze chwile potrwac)")
        mesh_max_edge = args.buffer_distance if not args.no_buffer_crop else None
        delaunay = build_delaunay_mesh(lidar_points, o3d, max_edge_length=mesh_max_edge)
        poisson = build_poisson_mesh(point_cloud, o3d, voxel_size=args.voxel_size)

        DEFAULT_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        for name, mesh in (("delaunay", delaunay), ("poisson", poisson)):
            export_mesh = o3d.geometry.TriangleMesh(mesh)
            export_mesh.translate(center)
            out_path = DEFAULT_RESULTS_DIR / f"mesh_{name}.ply"
            o3d.io.write_triangle_mesh(str(out_path), export_mesh)
            print(f"Zapisano siatke {name}: {out_path.resolve()}")

        print("Siatki gotowe. Klawisz M: przelacza miedzy chmura punktow / Delaunay / Poisson")

        if args.mesh and args.post_process:
            from mtb_terrain.mesh.pipeline import run_pipeline
            run_pipeline(
                input_mesh_path=DEFAULT_RESULTS_DIR / "mesh_delaunay.ply",
                output_dir=DEFAULT_RESULTS_DIR / "lod",
            )

    vis = o3d.visualization.Visualizer()
    vis.create_window(
        window_name="GPS EPSG:2180 + odfiltrowana chmura LiDAR EPSG:2180",
        width=1920,
        height=1080,
    )
    for g in geometries:
        vis.add_geometry(g)

    if args.mesh:
        register_mesh_toggle(vis, o3d, point_cloud, delaunay, poisson)

    opt = vis.get_render_option()
    opt.line_width = 10.0
    opt.point_size = 3.0
    vis.run()
    vis.destroy_window()

if __name__ == "__main__":
    main()
