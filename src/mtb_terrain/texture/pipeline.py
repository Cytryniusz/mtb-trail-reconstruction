"""
mtb_terrain.texture.pipeline
============================
Nakladanie ortofotomapy na siatke jako tekstury przez planarna projekcje UV.

Oblicza wspolrzedne UV dla kazdego wierzcholka siatki na podstawie jego
pozycji w plaszczyznie poziomej oraz granic ortofoto z ortho_report.json.
Zapisuje OBJ z gotowymi UV + plik .mtl wskazujacy na ortho.png.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Optional

import numpy as np
import open3d as o3d


def load_ortho_bounds(report_path: Path, use_unity_bounds: bool = True) -> dict:
    with open(report_path, encoding="utf-8") as f:
        report = json.load(f)

    georef = report["georef"]

    if use_unity_bounds:
        if "unity_bounds" not in georef:
            raise ValueError(
                "ortho_report.json nie zawiera 'unity_bounds'. "
                "Wygeneruj ortofoto z --pipeline-report (zawiera centroid) "
                "albo uzyj --world-bounds dla mesh-y georeferencyjnych."
            )
        b = georef["unity_bounds"]
        source = "unity_bounds (wycentrowane)"
    else:
        b = georef["world_bounds_epsg2180"]
        source = "world_bounds_epsg2180 (oryginalne)"

    bounds = {
        "xmin": b["xmin"], "ymin": b["ymin"],
        "xmax": b["xmax"], "ymax": b["ymax"],
        "source": source,
    }
    bounds["width_m"] = bounds["xmax"] - bounds["xmin"]
    bounds["height_m"] = bounds["ymax"] - bounds["ymin"]
    return bounds


def compute_planar_uv(
    vertices: np.ndarray,
    bounds: dict,
    horizontal_axis: int = 0,
    depth_axis: int = 2,
    flip_v: bool = True,
) -> np.ndarray:
    u_raw = vertices[:, horizontal_axis]
    v_raw = vertices[:, depth_axis]

    u = (u_raw - bounds["xmin"]) / bounds["width_m"]
    v = (v_raw - bounds["ymin"]) / bounds["height_m"]

    if flip_v:
        v = 1.0 - v

    return np.column_stack([u, v])


def uv_coverage_stats(uv: np.ndarray) -> dict:
    in_range = (
        (uv[:, 0] >= 0) & (uv[:, 0] <= 1) &
        (uv[:, 1] >= 0) & (uv[:, 1] <= 1)
    )
    return {
        "u_min": round(float(uv[:, 0].min()), 4),
        "u_max": round(float(uv[:, 0].max()), 4),
        "v_min": round(float(uv[:, 1].min()), 4),
        "v_max": round(float(uv[:, 1].max()), 4),
        "vertices_in_uv_range_pct": round(float(in_range.mean() * 100), 2),
        "vertices_outside": int((~in_range).sum()),
    }


def write_obj_with_uv(
    mesh: o3d.geometry.TriangleMesh,
    uv: np.ndarray,
    output_obj: Path,
    texture_filename: str,
    material_name: str = "ortho_material",
) -> None:
    vertices = np.asarray(mesh.vertices)
    triangles = np.asarray(mesh.triangles)
    has_normals = mesh.has_vertex_normals()
    normals = np.asarray(mesh.vertex_normals) if has_normals else None

    mtl_path = output_obj.with_suffix(".mtl")

    with open(output_obj, "w", encoding="utf-8") as f:
        f.write("# Wygenerowano przez mtb_terrain.texture.pipeline\n")
        f.write(f"# Wierzcholki: {len(vertices):,}  Trojkaty: {len(triangles):,}\n")
        f.write(f"mtllib {mtl_path.name}\n")
        f.write(f"usemtl {material_name}\n\n")

        for vx in vertices:
            f.write(f"v {vx[0]:.6f} {vx[1]:.6f} {vx[2]:.6f}\n")
        for uvc in uv:
            f.write(f"vt {uvc[0]:.6f} {uvc[1]:.6f}\n")
        if normals is not None:
            for n in normals:
                f.write(f"vn {n[0]:.6f} {n[1]:.6f} {n[2]:.6f}\n")
        f.write("\n")

        if normals is not None:
            for tri in triangles:
                a, b, c = tri[0] + 1, tri[1] + 1, tri[2] + 1
                f.write(f"f {a}/{a}/{a} {b}/{b}/{b} {c}/{c}/{c}\n")
        else:
            for tri in triangles:
                a, b, c = tri[0] + 1, tri[1] + 1, tri[2] + 1
                f.write(f"f {a}/{a} {b}/{b} {c}/{c}\n")

    with open(mtl_path, "w", encoding="utf-8") as f:
        f.write(f"# Material dla {output_obj.name}\n")
        f.write(f"newmtl {material_name}\n")
        f.write("Ka 1.000 1.000 1.000\n")
        f.write("Kd 1.000 1.000 1.000\n")
        f.write("Ks 0.000 0.000 0.000\n")
        f.write("d 1.0\n")
        f.write("illum 1\n")
        f.write(f"map_Kd {texture_filename}\n")


def process_mesh(
    mesh_path: Path,
    bounds: dict,
    ortho_image_path: Path,
    output_dir: Path,
    vertical_axis: str = "z",
    copy_texture: bool = True,
) -> dict:
    print(f"\n--- {mesh_path.name} ---")

    mesh = o3d.io.read_triangle_mesh(str(mesh_path))
    if len(mesh.triangles) == 0:
        raise ValueError(f"Pusty mesh: {mesh_path}")
    if not mesh.has_vertex_normals():
        mesh.compute_vertex_normals()

    vertices = np.asarray(mesh.vertices)
    print(f"  Wierzcholki: {len(vertices):,}  Trojkaty: {len(mesh.triangles):,}")

    if vertical_axis == "y":
        horizontal_axis, depth_axis = 0, 2
    else:
        horizontal_axis, depth_axis = 0, 1

    print(f"  Os wysokosci: {vertical_axis.upper()} | "
          f"UV z osi {'X-Z' if vertical_axis == 'y' else 'X-Y'}")

    uv = compute_planar_uv(vertices, bounds, horizontal_axis, depth_axis)
    stats = uv_coverage_stats(uv)

    print(f"  UV zakres: U[{stats['u_min']}, {stats['u_max']}] "
          f"V[{stats['v_min']}, {stats['v_max']}]")
    print(f"  Pokrycie w [0,1]: {stats['vertices_in_uv_range_pct']}% "
          f"({stats['vertices_outside']:,} poza)")

    if stats["vertices_in_uv_range_pct"] < 95:
        print("  OSTRZEZENIE: ponad 5% wierzcholkow poza zakresem UV. "
              "Sprawdz zgodnosc ukladu (unity vs georef) i --vertical-axis.")

    output_dir.mkdir(parents=True, exist_ok=True)
    out_obj = output_dir / f"{mesh_path.stem}_textured.obj"
    texture_name = ortho_image_path.name

    write_obj_with_uv(mesh, uv, out_obj, texture_name)
    print(f"  Zapisano: {out_obj.name} + {out_obj.with_suffix('.mtl').name}")

    if copy_texture:
        dest_texture = output_dir / texture_name
        if not dest_texture.exists():
            shutil.copy2(ortho_image_path, dest_texture)
            print(f"  Skopiowano teksture: {texture_name}")

    return {"mesh": mesh_path.name, "output": out_obj.name, "uv_stats": stats}


def run_pipeline(
    mesh_paths: list[Path],
    ortho_report_path: Path,
    ortho_image_path: Path,
    output_dir: Path,
    use_unity_bounds: bool = True,
    vertical_axis: str = "z",
) -> dict:
    print("=" * 70)
    print("  Nakladanie ortofoto na siatke (planarna projekcja UV)")
    print(f"  Mesh-y: {len(mesh_paths)}")
    print(f"  Ortofoto: {ortho_image_path.name}")
    print("=" * 70)

    bounds = load_ortho_bounds(ortho_report_path, use_unity_bounds)
    print(f"\nGranice ortofoto ({bounds['source']}):")
    print(f"  X[{bounds['xmin']:.1f}, {bounds['xmax']:.1f}] ({bounds['width_m']:.1f} m)")
    print(f"  Y[{bounds['ymin']:.1f}, {bounds['ymax']:.1f}] ({bounds['height_m']:.1f} m)")

    results = []
    for mesh_path in mesh_paths:
        results.append(process_mesh(
            mesh_path, bounds, ortho_image_path, output_dir, vertical_axis
        ))

    report = {
        "ortho_report": str(ortho_report_path.resolve()),
        "ortho_image": str(ortho_image_path.resolve()),
        "bounds_used": bounds,
        "vertical_axis": vertical_axis,
        "processed": results,
    }
    report_path = output_dir / "texturing_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"\nRaport: {report_path}")
    print("=" * 70)
    print("  Gotowe. Importuj _textured.obj do Unity — tekstura zaladuje sie z MTL.")
    print("=" * 70)
    return report