# unity/

Planowany podprojekt Unity URP konsumujacy wyniki pipeline-u (mesh-e LOD, ortofoto, splatmape).

**Status:** placeholder — sceny Unity beda dodane w pozniejszej fazie pracy.

## Spodziewana zawartosc

```
unity/
|-- Assets/
|   |-- Terrain/         mesh_LOD0..3_unity.obj + materialy
|   |-- Textures/        ortho.png + splatmap.png
|   `-- Scripts/         skrypty kamery, profilu wysokosci, trybu VR
|-- ProjectSettings/
`-- Packages/
```

Pipeline renderujacy: **URP** (Universal Render Pipeline).
Wersja Unity: **2022.3 LTS**.

## Import z pipeline-u

1. Wygeneruj LOD-y: `python scripts/05_generate_lod.py`
2. Wygeneruj ortofoto: `python scripts/06_process_ortho.py`
3. Wygeneruj splatmape: `python scripts/07_build_splatmap.py`
4. W Unity: drag&drop `results/lod/mesh_LOD0_unity.obj` -> Hierarchy.
5. Texture Import Settings dla `ortho.png`: sRGB ON, Wrap=Clamp, Filter=Bilinear.
6. Splatmape: import jako Texture2D + dodanie 4 Terrain Layers do komponentu `Terrain`.

Szczegoly w `docs/pipeline.md`.
