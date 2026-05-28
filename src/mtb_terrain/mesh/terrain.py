from __future__ import annotations
import numpy as np

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


def _color_by_height(points: np.ndarray) -> np.ndarray:
    z = points[:, 2]
    z_min, z_max = float(np.nanmin(z)), float(np.nanmax(z))
    z_range = max(z_max - z_min, 1e-9)
    normalized = np.clip((z - z_min) / z_range, 0.0, 1.0)
    n = len(_VIRIDIS)
    idx = normalized * (n - 1)
    lo = np.floor(idx).astype(int).clip(0, n - 2)
    t = (idx - lo)[:, None]
    return _VIRIDIS[lo] * (1.0 - t) + _VIRIDIS[lo + 1] * t


def build_delaunay_mesh(
    points: np.ndarray,
    o3d,
    max_edge_length: float | None = None,
):
    """Triangulacja Delaunay na XY, kolory Viridis wg Z.

    Gdy max_edge_length jest podane, odrzuca trójkąty z krawędziami dłuższymi
    niż próg — usuwa "rozciągnięte" trójkąty w pustych obszarach poza buforem.
    """
    try:
        from scipy.spatial import Delaunay
    except ImportError:
        raise ImportError(
            "Do siatki Delaunay potrzebna jest biblioteka scipy. "
            "Zainstaluj: pip install scipy"
        )
    tri = Delaunay(points[:, :2])
    triangles = tri.simplices

    if max_edge_length is not None:
        v0 = points[triangles[:, 0], :2]
        v1 = points[triangles[:, 1], :2]
        v2 = points[triangles[:, 2], :2]
        e01 = np.linalg.norm(v0 - v1, axis=1)
        e12 = np.linalg.norm(v1 - v2, axis=1)
        e20 = np.linalg.norm(v2 - v0, axis=1)
        max_edges = np.maximum(np.maximum(e01, e12), e20)
        keep = max_edges <= max_edge_length
        triangles = triangles[keep]

    mesh = o3d.geometry.TriangleMesh()
    mesh.vertices = o3d.utility.Vector3dVector(points)
    mesh.triangles = o3d.utility.Vector3iVector(triangles)
    mesh.vertex_colors = o3d.utility.Vector3dVector(_color_by_height(points))
    mesh.compute_vertex_normals()
    return mesh


def build_poisson_mesh(
    point_cloud,
    o3d,
    voxel_size: float = 0.0,
    density_quantile: float = 0.05,
):
    """Poisson surface reconstruction. Kolory Viridis wg Z. Fallback do Delaunay.

    density_quantile: usuwa wierzchołki o najniższej gęstości wsparcia
    (ekstrapolowane obszary poza chmurą wejściową). 0.05 = usuń dolne 5%.
    """
    effective_voxel = voxel_size if voxel_size > 0 else 0.5
    pc = point_cloud.voxel_down_sample(voxel_size=effective_voxel)
    pc.estimate_normals(
        search_param=o3d.geometry.KDTreeSearchParamHybrid(radius=2.0, max_nn=30)
    )
    pc.orient_normals_consistent_tangent_plane(k=15)
    try:
        mesh, densities = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(
            pc, depth=9
        )
    except Exception as exc:
        print(f"Poisson reconstruction nie powiodla sie: {exc}. Uzywam Delaunay.")
        return build_delaunay_mesh(np.asarray(pc.points), o3d)

    densities = np.asarray(densities)
    if 0.0 < density_quantile < 1.0 and len(densities) > 0:
        threshold = float(np.quantile(densities, density_quantile))
        mesh.remove_vertices_by_mask(densities < threshold)

    mesh.remove_non_manifold_edges()
    mesh.compute_vertex_normals()
    pts = np.asarray(mesh.vertices)
    mesh.vertex_colors = o3d.utility.Vector3dVector(_color_by_height(pts))
    return mesh


def register_mesh_toggle(vis, o3d, point_cloud, delaunay_mesh, poisson_mesh) -> None:
    """Przełącza siatki klawiszem M przez animation_callback (bezpieczne dla GIL na Windows)."""
    import ctypes

    _VK_M = 0x4D  # Windows virtual-key code dla 'M'
    _labels = ["Chmura punktow", "Siatka Delaunay", "Siatka Poisson"]
    _geoms = [point_cloud, delaunay_mesh, poisson_mesh]
    state = {"mode": 0, "active": point_cloud, "m_down": False}

    def _animation_callback(vis):
        pressed = bool(ctypes.windll.user32.GetAsyncKeyState(_VK_M) & 0x8000)
        if pressed and not state["m_down"]:
            vis.remove_geometry(state["active"], reset_bounding_box=False)
            state["mode"] = (state["mode"] + 1) % 3
            state["active"] = _geoms[state["mode"]]
            vis.add_geometry(state["active"], reset_bounding_box=False)
            vis.update_renderer()
            print(f"Tryb siatki: {_labels[state['mode']]}")
        state["m_down"] = pressed
        return False

    vis.register_animation_callback(_animation_callback)
