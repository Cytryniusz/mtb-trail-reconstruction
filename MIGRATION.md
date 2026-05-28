# MIGRATION.md — jak wdrozyc i opublikowac to repo

Krotki przewodnik po przejsciu ze starego, plaskiego folderu `praca_dyplomowa/` do nowego repozytorium `mtb-trail-reconstruction/` i opublikowaniu go na GitHub.

## 1. Rozpakowanie i miejsce docelowe

```powershell
# PowerShell, w Eksploratorze juz pobrales mtb-trail-reconstruction.zip
cd C:\Users\szymo\Code\
# Rozpakuj pobrany .zip do tego folderu (np. prawym przyciskiem -> Wyodrebnij wszystkie...)
# Po rozpakowaniu powinienes miec:
#   C:\Users\szymo\Code\mtb-trail-reconstruction\
#   C:\Users\szymo\Code\praca_dyplomowa\        (stara wersja — zachowaj jako backup)
```

## 2. Przeniesienie surowych danych ze starego folderu

Nowe repo nie zawiera danych (zbyt duze). Przekopiuj swoje wejscia ze starego folderu:

```powershell
$SRC = "C:\Users\szymo\Code\praca_dyplomowa"
$DST = "C:\Users\szymo\Code\mtb-trail-reconstruction"

# LiDAR
Copy-Item "$SRC\data\lidar\*.laz" -Destination "$DST\data\lidar\" -Force

# Ortofoto
Copy-Item "$SRC\data\ortho\*.tif" -Destination "$DST\data\ortho\" -Force

# GPX
Copy-Item "$SRC\data\gps_trace\*.gpx" -Destination "$DST\data\gps_trace\" -Force

# Opcjonalnie — juz przetworzone pliki posrednie (zeby nie zaczynac od zera)
Copy-Item "$SRC\processed\*" -Destination "$DST\processed\" -Recurse -Force
Copy-Item "$SRC\results\*"   -Destination "$DST\results\"   -Recurse -Force
```

Wszystkie te katalogi sa w `.gitignore` — nie trafia do publicznego repo.

## 3. Srodowisko Python

Stary projekt opieral sie na luznych instalacjach. Nowe repo ma deterministyczny `environment.yml`:

```powershell
cd C:\Users\szymo\Code\mtb-trail-reconstruction
conda env create -f environment.yml
conda activate mtb-trail
```

Jezeli juz masz srodowisko `mtb-trail` (lub podobne), zaktualizuj je:

```powershell
conda env update -f environment.yml --prune
```

Sprawdz, czy pipeline startuje (bez wykonywania nic ciezkiego):

```powershell
python scripts\01_clean_lidar.py --help
python scripts\05_generate_lod.py --help
```

## 4. Pierwszy commit i push na GitHub

### Opcja A — z `gh` CLI (najszybsze)

Wymaga zainstalowanego [GitHub CLI](https://cli.github.com/) (`winget install GitHub.cli`).

```powershell
cd C:\Users\szymo\Code\mtb-trail-reconstruction
git init -b main
git add .
git status   # zweryfikuj ze data/processed/results NIE sa stagowane
git commit -m "Initial commit: pipeline rekonstrukcji trasy MTB"

# Utworz publiczne repo na GitHub i wypchnij
gh auth login              # jednorazowo
gh repo create mtb-trail-reconstruction --public --source=. --remote=origin --push
```

### Opcja B — recznie przez przegladarke

```powershell
cd C:\Users\szymo\Code\mtb-trail-reconstruction
git init -b main
git add .
git status
git commit -m "Initial commit: pipeline rekonstrukcji trasy MTB"
```

Na github.com:
1. Kliknij **New repository**.
2. Repository name: `mtb-trail-reconstruction`.
3. **Public**, bez README/LICENSE/.gitignore (juz je masz).
4. Create repository.

Skopiuj polecenia ze strony GitHuba i wykonaj:

```powershell
git remote add origin https://github.com/<twoj-login>/mtb-trail-reconstruction.git
git branch -M main
git push -u origin main
```

## 5. Wzbogac repo o "wizytowkowe" detale

Po pierwszym pushu wejdz na strone repo i ustaw:

1. **About** (prawy panel, ikona kola zebatego):
   - **Description**: `End-to-end pipeline: LiDAR + GPS + aerial imagery -> Unity-ready 3D MTB trail scene`
   - **Website**: link do swojej strony albo zostaw puste
   - **Topics**: `lidar`, `gis`, `geospatial`, `mtb`, `unity`, `3d-reconstruction`, `pdal`, `open3d`, `python`, `urp`, `thesis`
2. **Settings -> General -> Features**:
   - Issues: ON (dobre na trackowanie wlasnych TODO),
   - Discussions: opcjonalnie ON,
   - Wiki: OFF (dokumentacja jest w `docs/`).
3. **Settings -> Pages** (opcjonalnie): mozesz wystawic `docs/` przez GitHub Pages.
4. **Releases** (opcjonalnie): zrob tag `v0.1.0` z opisem "Pierwsza wersja kodu pracy inzynierskiej".

## 6. Co dalej

- Dodaj GitHub Actions CI (`ruff check` + `python -m py_compile` na PRach) — wykryje syntax errors zanim trafia do main.
- Gdy dolozysz scene Unity, podproject `unity/` ma juz placeholder README; sam projekt Unity trafi do `unity/Assets/`.
- Tag `v1.0.0` po obronie pracy — niech bedzie kanonicznym snapshotem.

## 7. Co zmienilo sie wzgledem starego folderu — w skrocie

| Stare | Nowe | Powod |
|---|---|---|
| `lidar_cleanup.py` w korzeniu | `src/mtb_terrain/lidar/cleanup.py` + `scripts/01_clean_lidar.py` | Rozdzielenie biblioteki od entry-pointu |
| `gps_lidar_int_delaunay.py` | `src/mtb_terrain/mesh/delaunay.py` + `scripts/04_build_mesh.py` | Czytelna numeracja kroku, importy z paczki |
| `slope_coloring.py`, `elevation_profile.py`, `terrain_mesh.py` w korzeniu | `src/mtb_terrain/{viz,mesh}/...` | Modul `viz/` dla wizualizacji, `mesh/` dla geometrii |
| `lidar/` (duplikat `data/lidar/`) | usuniete | Smieci po refaktorze |
| Twarde sciezki `C:\Users\szymo\...` | sciezki wzgledne (`_REPO_ROOT / "processed" / ...`) | Pipeline dziala u kazdego |
| Brak `.gitignore`, `LICENSE`, `README` | wszystko jest | Zeby repo dalo sie sklonowac i opublikowac |
| `__pycache__/`, `.pytest_cache/`, `.vscode/`, `.superpowers/` | gitignored | Czysty diff |
| `gpkg_import.py` (4 linie scratchu) | usuniete | Nieuzywane |

## 8. Jak wrocic do starego ukladu (gdyby cos)

Stary folder `C:\Users\szymo\Code\praca_dyplomowa\` zostal **nietkniety** — to twoj backup. Mozesz w razie potrzeby usunac nowy katalog i pracowac dalej na starym, bez utraty pracy.
