from __future__ import annotations

import argparse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import gpxpy
import matplotlib.pyplot as plt

try:
    import geopandas as gpd
    from shapely.geometry import LineString, Point
except ModuleNotFoundError as exc:
    raise ModuleNotFoundError(
        "Do transformacji GPX do EPSG:2180 potrzebne sa biblioteki geopandas i shapely. "
        "Zainstaluj je w aktywnym srodowisku Pythona, np. poleceniem: pip install geopandas shapely"
    ) from exc

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_GPX_PATH = _REPO_ROOT / "data" / "gps_trace" / "flow--jump.gpx"
DEFAULT_OUTPUT_PATH = Path("gps_trace_visualization.png")
DEFAULT_PROCESSED_DIR = Path("processed")
DEFAULT_PROCESSED_GPX = "flow--jump_processed_2180.gpx"
DEFAULT_PROCESSED_GPKG = "flow--jump_epsg2180.gpkg"
DEFAULT_BUFFER_DISTANCE_M = 15.0
WGS84_EPSG = "EPSG:4326"
POLAND_1992_EPSG = "EPSG:2180"


@dataclass
class GpsPoint:
    latitude: float
    longitude: float
    x_2180: float
    y_2180: float
    elevation: float | None
    time: datetime | None
    distance_km: float


def load_gpx(path: Path):
    with path.open("r", encoding="utf-8") as gpx_file:
        return gpxpy.parse(gpx_file)


def extract_points(gpx) -> list[GpsPoint]:
    raw_points: list[dict] = []
    previous_point = None
    total_distance_m = 0.0

    for track in gpx.tracks:
        for segment in track.segments:
            for point in segment.points:
                if previous_point is not None:
                    distance_m = previous_point.distance_2d(point) or 0.0
                    total_distance_m += distance_m

                raw_points.append(
                    {
                        "latitude": point.latitude,
                        "longitude": point.longitude,
                        "elevation": point.elevation,
                        "time": point.time,
                        "distance_km": total_distance_m / 1000,
                        "geometry": Point(point.longitude, point.latitude),
                    }
                )
                previous_point = point

    if not raw_points:
        return []

    wgs84_points = gpd.GeoDataFrame(raw_points, geometry="geometry", crs=WGS84_EPSG)
    projected_points = wgs84_points.to_crs(POLAND_1992_EPSG)

    points: list[GpsPoint] = []
    for row in projected_points.to_dict("records"):
        geometry = row["geometry"]
        points.append(
            GpsPoint(
                latitude=row["latitude"],
                longitude=row["longitude"],
                x_2180=geometry.x,
                y_2180=geometry.y,
                elevation=row["elevation"],
                time=row["time"],
                distance_km=row["distance_km"],
            )
        )

    return points


def print_summary(points: list[GpsPoint]) -> None:
    elevations = [point.elevation for point in points if point.elevation is not None]

    # Obliczenie D+ (suma przewyzszenia) i D- (suma obnizenia)
    elevation_gain = 0.0
    elevation_loss = 0.0
    for i in range(1, len(elevations)):
        diff = elevations[i] - elevations[i - 1]
        if diff > 0:
            elevation_gain += diff
        else:
            elevation_loss += abs(diff)

    # Nachylenie segmentow i klasy trudnosci
    SLOPE_CLASSES = {
        "Lagodny (< 5%)":        (0.0,  5.0),
        "Umiarkowany (5-15%)":   (5.0, 15.0),
        "Stromy (15-30%)":      (15.0, 30.0),
        "Bardzo stromy (>30%)": (30.0, float("inf")),
    }
    class_distances = dict.fromkeys(SLOPE_CLASSES, 0.0)

    for i in range(1, len(points)):
        elev_curr = points[i].elevation
        elev_prev = points[i - 1].elevation
        if elev_curr is None or elev_prev is None:
            continue
        dx = points[i].x_2180 - points[i - 1].x_2180
        dy = points[i].y_2180 - points[i - 1].y_2180
        dz = elev_curr - elev_prev
        dist_2d = (dx ** 2 + dy ** 2) ** 0.5
        if dist_2d < 0.01:
            continue
        slope_pct = abs(dz / dist_2d) * 100.0
        for name, (low, high) in SLOPE_CLASSES.items():
            if low <= slope_pct < high:
                class_distances[name] += dist_2d / 1000.0
                break

    total_classified = sum(class_distances.values())

    print("=" * 45)
    print("Podsumowanie trasy GPX")
    print("=" * 45)
    print(f"Transformacja ukladu: {WGS84_EPSG} -> {POLAND_1992_EPSG}")
    print(f"Liczba punktow:       {len(points)}")
    print(f"Dlugosc trasy:        {points[-1].distance_km:.2f} km")

    if points[0].time and points[-1].time:
        duration = points[-1].time - points[0].time
        print(f"Czas trwania:         {duration}")

    if elevations:
        print(f"Wysokosc min:         {min(elevations):.1f} m n.p.m.")
        print(f"Wysokosc max:         {max(elevations):.1f} m n.p.m.")
        print(f"Suma przewyzszenia D+:{elevation_gain:.0f} m")
        print(f"Suma obnizenia D-:    {elevation_loss:.0f} m")

    if total_classified > 0:
        print()
        print("Klasy trudnosci (wg nachylenia):")
        for name, dist in class_distances.items():
            pct = dist / total_classified * 100
            print(f"  {name:<26} {dist:.2f} km  ({pct:.1f}%)")

    print("=" * 45)

def plot_trace(points: list[GpsPoint], output_path: Path, show_plot: bool) -> None:
    x_coordinates = [point.x_2180 for point in points]
    y_coordinates = [point.y_2180 for point in points]
    distances = [point.distance_km for point in points]
    elevations = [point.elevation for point in points]

    fig, (route_ax, elevation_ax) = plt.subplots(
        nrows=1,
        ncols=2,
        figsize=(13, 6),
        constrained_layout=True,
    )

    route_ax.plot(x_coordinates, y_coordinates, color="#1f77b4", linewidth=2)
    route_ax.scatter(x_coordinates[0], y_coordinates[0], color="green", label="Start", zorder=3)
    route_ax.scatter(x_coordinates[-1], y_coordinates[-1], color="red", label="Koniec", zorder=3)
    route_ax.set_title("Slad GPS w EPSG:2180")
    route_ax.set_xlabel("X [m]")
    route_ax.set_ylabel("Y [m]")
    route_ax.grid(True, linestyle="--", alpha=0.4)
    route_ax.legend()
    route_ax.set_aspect("equal", adjustable="box")

    if any(elevation is not None for elevation in elevations):
        elevation_ax.plot(distances, elevations, color="#d62728", linewidth=2)
        elevation_ax.fill_between(distances, elevations, alpha=0.15, color="#d62728")
    elevation_ax.set_title("Profil wysokosci")
    elevation_ax.set_xlabel("Dystans [km]")
    elevation_ax.set_ylabel("Wysokosc [m n.p.m.]")
    elevation_ax.grid(True, linestyle="--", alpha=0.4)

    fig.suptitle("Bialka Tatrzanska - wizualizacja trasy GPX")
    fig.savefig(output_path, dpi=150)
    print(f"Zapisano wykres: {output_path.resolve()}")

    if show_plot:
        plt.show()
    else:
        plt.close(fig)

def save_processed_gpx(gpx, points: list[GpsPoint], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    point_index = 0

    for track in gpx.tracks:
        for segment in track.segments:
            for point in segment.points:
                processed_point = points[point_index]
                point.extensions = [
                    extension
                    for extension in point.extensions
                    if extension.tag not in {"x_2180", "y_2180", "epsg"}
                ]

                x_extension = ET.Element("x_2180")
                x_extension.text = f"{processed_point.x_2180:.3f}"
                y_extension = ET.Element("y_2180")
                y_extension.text = f"{processed_point.y_2180:.3f}"
                epsg_extension = ET.Element("epsg")
                epsg_extension.text = POLAND_1992_EPSG

                point.extensions.extend([x_extension, y_extension, epsg_extension])
                point_index += 1

    with output_path.open("w", encoding="utf-8") as processed_file:
        processed_file.write(gpx.to_xml())

    print(f"Zapisano przetworzony GPX: {output_path.resolve()}")


def save_projected_trace(points: list[GpsPoint], output_path: Path, buffer_distance_m: float) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    point_records = [
        {
            "point_id": point_id,
            "source_lat": point.latitude,
            "source_lon": point.longitude,
            "elevation": point.elevation,
            "time": point.time.isoformat() if point.time else None,
            "distance_km": point.distance_km,
            "geometry": Point(point.x_2180, point.y_2180),
        }
        for point_id, point in enumerate(points, start=1)
    ]
    projected_points = gpd.GeoDataFrame(
        point_records,
        geometry="geometry",
        crs=POLAND_1992_EPSG,
    )
    projected_points.to_file(output_path, layer="track_points", driver="GPKG")

    if len(points) > 1:
        line_geometry = LineString(
            [(point.x_2180, point.y_2180) for point in points]
        )
        projected_line = gpd.GeoDataFrame(
            [
                {
                    "name": output_path.stem,
                    "points_count": len(points),
                    "length_km": points[-1].distance_km,
                    "geometry": line_geometry,
                }
            ],
            geometry="geometry",
            crs=POLAND_1992_EPSG,
        )
        projected_line.to_file(output_path, layer="track_line", driver="GPKG")

        projected_buffer = gpd.GeoDataFrame(
            [
                {
                    "name": f"{output_path.stem}_buffer",
                    "buffer_m": buffer_distance_m,
                    "width_m": buffer_distance_m * 2,
                    "geometry": line_geometry.buffer(buffer_distance_m),
                }
            ],
            geometry="geometry",
            crs=POLAND_1992_EPSG,
        )
        projected_buffer.to_file(output_path, layer="track_buffer", driver="GPKG")

    print(f"Zapisano przetworzony slad EPSG:2180: {output_path.resolve()}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Przetwarzanie i wizualizacja pliku GPX.")
    parser.add_argument(
        "--gpx",
        type=Path,
        default=DEFAULT_GPX_PATH,
        help="Sciezka do pliku GPX.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="Sciezka do pliku PNG z wykresem.",
    )
    parser.add_argument(
        "--processed-dir",
        type=Path,
        default=DEFAULT_PROCESSED_DIR,
        help="Folder na przetworzony plik GPX.",
    )
    parser.add_argument(
        "--processed-gpx",
        type=str,
        default=DEFAULT_PROCESSED_GPX,
        help="Nazwa pomocniczego pliku GPX z metrycznymi wspolrzednymi w extensions.",
    )
    parser.add_argument(
        "--processed-gpkg",
        type=str,
        default=DEFAULT_PROCESSED_GPKG,
        help="Nazwa pliku GeoPackage z geometria w EPSG:2180.",
    )
    parser.add_argument(
        "--buffer-distance",
        type=float,
        default=DEFAULT_BUFFER_DISTANCE_M,
        help="Odleglosc bufora od osi sladu w metrach.",
    )
    parser.add_argument(
        "--no-show",
        action="store_true",
        help="Nie otwieraj okna z wykresem, tylko zapisz PNG.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not args.gpx.exists():
        raise FileNotFoundError(f"Nie znaleziono pliku GPX: {args.gpx}")

    gpx = load_gpx(args.gpx)
    points = extract_points(gpx)

    if not points:
        raise ValueError("Plik GPX nie zawiera punktow trasy.")

    print_summary(points)
    plot_trace(points, args.output, show_plot=not args.no_show)
    save_projected_trace(
        points,
        args.processed_dir / args.processed_gpkg,
        args.buffer_distance,
    )
    save_processed_gpx(gpx, points, args.processed_dir / args.processed_gpx)


if __name__ == "__main__":
    main()
