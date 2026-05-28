"""run_all.py
============
Uruchom caly pipeline od surowego LAZ + GPX do gotowych assetow Unity.

Uzycie:
    python scripts/run_all.py --laz data/lidar/X.laz --gpx data/gps_trace/Y.gpx \\
                              --ortho-tifs data/ortho/A.tif data/ortho/B.tif

Pomija etapy ktorych wyniki juz istnieja (idempotentnie).
"""
from __future__ import annotations
import argparse
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _REPO_ROOT / "scripts"
_PROCESSED = _REPO_ROOT / "processed"
_RESULTS = _REPO_ROOT / "results"


def step(label: str, cmd: list[str], skip_if_exists: Path | None = None) -> None:
    if skip_if_exists and skip_if_exists.exists():
        print(f"[PASS] {label} -- juz istnieje: {skip_if_exists.name}")
        return
    print(f"\n[RUN]  {label}")
    print(f"       {' '.join(cmd)}")
    result = subprocess.run([sys.executable, *cmd], cwd=_REPO_ROOT)
    if result.returncode != 0:
        sys.exit(f"[FAIL] {label} -- exit code {result.returncode}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Pelny pipeline LiDAR -> Unity.")
    p.add_argument("--laz", type=Path, required=True, help="Surowy plik LAZ/LAS.")
    p.add_argument("--gpx", type=Path, required=True, help="Surowy plik GPX trasy.")
    p.add_argument("--ortho-tifs", type=Path, nargs="+", required=True,
                   help="Pliki GeoTIFF z ortofotomapa (kafle).")
    p.add_argument("--skip-mesh", action="store_true",
                   help="Pomin generacje mesh-a (np. gdy juz istnieje).")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    _PROCESSED.mkdir(parents=True, exist_ok=True)
    _RESULTS.mkdir(parents=True, exist_ok=True)

    laz_filtered = _PROCESSED / f"{args.laz.stem}_filtered.laz"
    laz_ground = _PROCESSED / f"{args.laz.stem}_filtered_ground.laz"
    gpkg = _PROCESSED / f"{args.gpx.stem}_epsg2180.gpkg"
    mesh = _RESULTS / "mesh_delaunay.ply"

    step("01 Cleanup LAZ", ["scripts/01_clean_lidar.py", "--input", str(args.laz),
                            "--output", str(laz_filtered)],
         skip_if_exists=laz_filtered)

    step("02 Extract ground", ["scripts/02_extract_ground.py",
                               "--input", str(laz_filtered),
                               "--output", str(laz_ground)],
         skip_if_exists=laz_ground)

    step("03 Process GPS", ["scripts/03_process_gps.py", "--gpx", str(args.gpx),
                            "--no-show"],
         skip_if_exists=gpkg)

    if not args.skip_mesh:
        step("04 Build mesh", ["scripts/04_build_mesh.py",
                               "--gpkg", str(gpkg), "--ply", str(laz_ground),
                               "--mesh"],
             skip_if_exists=mesh)

    step("05 Generate LOD", ["scripts/05_generate_lod.py", "--input", str(mesh)])

    step("06 Process ortho", ["scripts/06_process_ortho.py",
                              "--tifs", *[str(t) for t in args.ortho_tifs],
                              "--gpkg", str(gpkg)])

    step("07 Build splatmap", ["scripts/07_build_splatmap.py",
                               "--las", str(laz_filtered), "--gpkg", str(gpkg)])

    print("\n[DONE] Wszystkie etapy zakonczone. Wyniki w:", _RESULTS)


if __name__ == "__main__":
    main()
