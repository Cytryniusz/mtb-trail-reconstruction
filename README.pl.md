# MTB Trail Reconstruction

[![CI](https://github.com/Cytryniusz/mtb-trail-reconstruction/actions/workflows/ci.yml/badge.svg)](https://github.com/Cytryniusz/mtb-trail-reconstruction/actions/workflows/ci.yml)

Kompletny pipeline przetwarzania danych geoprzestrzennych (LiDAR + GPS + ortofoto) do fotorealistycznej trojwymiarowej wizualizacji trasy MTB w silniku Unity.

> Repozytorium kodu do pracy inzynierskiej (2026). Skrocona wersja po angielsku: [`README.md`](README.md).

<p align="center">
  <img src="examples/sample_results/ortho_preview.png" width="32%" alt="Podglad ortofotomapy" />
  <img src="examples/sample_results/splatmap_preview.png" width="32%" alt="Podglad splatmapy" />
  <img src="examples/sample_results/elevation_profile.png" width="32%" alt="Profil wysokosciowy" />
</p>

## Cel pracy

System umozliwia pozyskanie, integracje i przetworzenie wielosensorowych danych geoprzestrzennych — w szczegolnosci:

- chmury punktow **LiDAR** (LAS/LAZ),
- numerycznych modeli terenu (**DEM/DTM**),
- **ortofotomap** (GeoTIFF),
- sladow **GPS** (GPX),

a nastepnie wygenerowanie z nich fotorealistycznej sceny trasy MTB gotowej do importu w Unity (URP). Powstala scena moze sluzyc zarowno do analizy geometrii trasy, jak i jako baza pod symulacje interaktywne czy aplikacje VR.

## Co dokladnie robi pipeline

Dla pojedynczej trasy MTB przetwarza:

- jeden lub wiecej plikow `.laz` (np. z GUGiK dla obszaru Polski),
- slad `.gpx` z urzadzenia sportowego (Garmin, Wahoo, Strava itp.),
- kafle ortofoto `.tif` pokrywajace obszar trasy,

i produkuje:

- oczyszczony, sklasyfikowany mesh terenu w czterech poziomach szczegolowosci (LOD0–LOD3) gotowy do Unity (`.obj` + `.ply`),
- teksture ortofoto o rozmiarze potegi 2, zgeoreferencjowana do mesh-a,
- oteksturowany mesh LOD0 (`.obj` + `.mtl`) z planarnym mapowaniem UV z ortofoto, gotowy do importu w Unity,
- 4-kanalowa splatmape (ground / path / undergrowth / rock) dla Unity Terrain Layers,
- profil wysokosciowy trasy kolorowany wg klas nachylenia,
- raporty w JSON-ie (statystyki LOD-ow, metadane georeferencji) — gotowy material do rozdzialu eksperymentow w pracy.

## Diagram pipeline-u

```
   surowy .laz             surowy .gpx              surowy .tif (xN)
       |                       |                          |
   [01] cleanup              [03] reprojekcja          [06] mozaika + crop
   SOR + ROR                 WGS84 -> EPSG:2180           + resize 2^N
       |                       |                          |
   [02] ekstrakcja           processed/*.gpkg          results/ortho/ortho.png
   ground (klasa 2)             |
       |                       |
       +-----------+-----------+
                   |
              [04] integracja GPS x LiDAR
              (mesh Delaunay 2.5D, przyciecie do bufora)
                   |
              [05] cleanup mesh-a + kaskada LOD
              (Taubin smoothing, Quadric decimation)
                   |
                   v
              results/lod/mesh_LOD{0..3}_unity.{ply,obj}
                                  |
              [08] nakladanie tekstury (planarne UV: mesh LOD0 x ortho.png)
                                  |
              results/textured/mesh_LOD0_unity_textured.obj (+ .mtl)
                                  +
              [07] splatmap RGBA  -> results/splatmap/splatmap.png
                                  +
                              Unity URP scene
```

Szczegolowy opis kazdego etapu w [`docs/pipeline.md`](docs/pipeline.md).

## Szybki start

```bash
# 1. Sklonuj
git clone https://github.com/<twoj-uzytkownik>/mtb-trail-reconstruction.git
cd mtb-trail-reconstruction

# 2. Srodowisko (conda zalecane - PDAL wymaga binarki z conda-forge)
conda env create -f environment.yml
conda activate mtb-trail

# 3. Wrzuc swoje dane
#    Skad pobrac: docs/data_sources.md
cp /sciezka/do/twojego.laz data/lidar/
cp /sciezka/do/twojego.gpx data/gps_trace/
cp /sciezka/do/ortho.tif   data/ortho/

# 4. Pelny pipeline jednym poleceniem
python scripts/run_all.py \
    --laz data/lidar/twoj.laz \
    --gpx data/gps_trace/twoj.gpx \
    --ortho-tifs data/ortho/twoj_ortho.tif

# Albo krok po kroku (scripts/0X_*.py)
python scripts/01_clean_lidar.py --input data/lidar/twoj.laz
python scripts/02_extract_ground.py --input processed/twoj_filtered.laz
...
```

## Struktura repozytorium

```
mtb-trail-reconstruction/
|-- src/mtb_terrain/         Paczka pythonowa (importowalna)
|   |-- lidar/               Cleanup, klasyfikacja SMRF, ekstrakcja ground
|   |-- gps/                 Parsowanie GPX + reprojekcja do EPSG:2180
|   |-- mesh/                Delaunay 2.5D + Poisson, cleanup, kaskada LOD
|   |-- ortho/               Mozaika ortofoto + crop + resize pod Unity
|   |-- texture/             Planarna projekcja UV ortofoto na mesh LOD0
|   |-- splatmap/            Generacja 4-kanalowej splatmapy terenu
|   `-- viz/                 Profile wysokosci, kolorowanie wg nachylenia
|-- scripts/                 Cienkie wrappery CLI (01_..08_ + run_all.py)
|-- configs/default.yaml     Referencyjna konfiguracja (CRS, filtry, LOD)
|-- data/                    (gitignored) surowe dane wejsciowe
|-- processed/               (gitignored) pliki posrednie
|-- results/                 (gitignored) wyniki (mesh, tekstury, raporty)
|-- examples/sample_results/ Male PNG-i pokazane w README
|-- docs/                    Dokumentacja pipeline-u, zrodel danych
`-- MIGRATION.md             Jak opublikowac repo na GitHub
```

## Wykorzystane technologie

| Dziedzina        | Narzedzie                                | Po co                                                  |
| ---------------- | ---------------------------------------- | ------------------------------------------------------ |
| Chmury punktow   | **PDAL** + **laspy**                     | Filtracja, klasyfikacja, IO dla LAS/LAZ                |
| Geometria 3D     | **Open3D**                               | Operacje na mesh-ach, Taubin smoothing, decymacja QECD |
| Geoprzestrzennie | **GeoPandas**, **Shapely**, **rasterio** | CRS, GPKG, mozaika rastrow                             |
| GPS              | **gpxpy**                                | Parser GPX                                             |
| Obrazy           | **Pillow** + **NumPy**                   | Resize tekstur, rasteryzacja splatmapy                 |
| Numeryka         | **NumPy**, **SciPy**                     | Delaunay, Savitzky-Golay, filtry Gaussa                |
| Silnik 3D        | **Unity 2022.3 LTS** (URP)               | Renderer + scena                                       |

## Dokumentacja

- [`docs/pipeline.md`](docs/pipeline.md) — szczegolowy opis kazdego etapu pipeline-u
- [`docs/data_sources.md`](docs/data_sources.md) — skad pobrac dane wejsciowe dla Polski
- [`MIGRATION.md`](MIGRATION.md) — historia struktury repo, instrukcja git/GitHub
- [`README.md`](README.md) — skrocona wersja angielska

## Status

Aktywne prace, czesc pracy inzynierskiej bronionej w 2026 roku. Repo zawiera kod systemu — sam tekst pracy znajduje sie w osobnym, prywatnym repozytorium.

## Licencja

[MIT](LICENSE).
