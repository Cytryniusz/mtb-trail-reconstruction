# Patch: GitHub Actions CI

Ten patch dodaje do repo `mtb-trail-reconstruction` automatyczny CI na GitHub:
- **ruff check** — lint + sortowanie importow (pierwsza linia obrony)
- **python -m compileall** — sprawdzenie skladni wszystkich modulow
- Uruchamia sie przy kazdym `push` do `main` i kazdym `pull_request`

## Co jest w tym patchu

| Plik | Typ zmiany | Powod |
|---|---|---|
| `.github/workflows/ci.yml` | **NOWY** | Workflow GitHub Actions |
| `.gitignore` | zmieniony | + `.ruff_cache/` |
| `pyproject.toml` | zmieniony | + `per-file-ignores` dla `scripts/*.py` (E402) |
| `README.md`, `README.pl.md` | zmienione | + badge CI w naglowku |
| `src/mtb_terrain/lidar/process.py` | **bugfix** | Literowka `_threshold` -> `smrf_threshold` w sygnaturze `save_ground_laz` |
| Pozostale `*.py` (kilkanascie) | zmienione | Auto-fixy ruffa: sortowanie importow, usuniecie unused imports, f-strings bez placeholderow, `strict=False` w `zip()`, `raise ... from exc`, `contextlib.suppress` w miejsce `try/except/pass`, zmienna `l` -> `label` |

Wszystkie zmiany przeszly `ruff check src/ scripts/` (zero ostrzezen) i `python -m compileall` (zero bledow skladni).

## Jak nalozyc patch

### Opcja A: nadpisac w istniejacym repo (najprostsze)

1. Rozpakuj `mtb-ci-patch.zip` do **tego samego** folderu co repo, tak zeby pliki sie nadpisaly. Po rozpakowaniu z folderu z patchem (zachowuje wewnetrzna strukture katalogow) skopiuj zawartosc do `C:\Users\szymo\Code\mtb-trail-reconstruction\`:

   ```powershell
   # PowerShell: rozpakuj zip do tymczasowego folderu
   Expand-Archive -Path mtb-ci-patch.zip -DestinationPath $env:TEMP\mtb-ci-patch -Force

   # Skopiuj wszystko (-Recurse -Force = nadpisuj)
   Copy-Item -Path "$env:TEMP\mtb-ci-patch\mtb-ci-patch\*" `
             -Destination "C:\Users\szymo\Code\mtb-trail-reconstruction\" `
             -Recurse -Force
   ```

2. Podmien `YOUR_USERNAME` w badge:

   ```powershell
   cd C:\Users\szymo\Code\mtb-trail-reconstruction
   (Get-Content README.md) -replace 'YOUR_USERNAME','<twoj-github-login>' | Set-Content README.md
   (Get-Content README.pl.md) -replace 'YOUR_USERNAME','<twoj-github-login>' | Set-Content README.pl.md
   ```

3. Sprawdz lokalnie ze ruff przechodzi (opcjonalnie, wymaga `pip install ruff`):

   ```powershell
   conda activate mtb-trail   # albo dowolne srodowisko Python 3.11+
   pip install ruff
   ruff check src/ scripts/
   python -m compileall -q src/ scripts/
   ```

   Oba polecenia powinny zakonczyc sie kodem 0, bez wypisanych bledow.

4. Commit + push:

   ```powershell
   git add .
   git status              # sprawdz ze widzisz nowy plik .github/workflows/ci.yml
   git commit -m "Add GitHub Actions CI: ruff check + py_compile on PRs and main"
   git push
   ```

5. Wejdz na strone repo na GitHub -> zakladka **Actions**. Powinienes zobaczyc bieg "CI" startujacy automatycznie. Zielone po ~30 sekundach.

### Opcja B: gdy juz wypchnales pierwsza wersje na GitHub i chcesz tylko CI

Mozesz tez wziac z patcha tylko `.github/workflows/ci.yml` i `pyproject.toml`, ale wtedy CI moze przepasc czerwono na obecnym kodzie (bo nie wpadly bugfixy). Zalecam Opcje A — nadpisz wszystko, zacommituj jednym commitem.

## Co dalej

Po pierwszym zielonym CI mozesz dorzucic:

- **Branch protection** na `main`: Settings -> Branches -> Add rule -> require status checks (CI) before merging. Zabezpiecza przed pushowaniem zlamanego kodu wprost do main.
- **Drugie zadanie CI**: faktyczny `pytest` jak juz beda testy.
- **Cache conda**: gdyby CI mial uruchamiac sam pipeline (test integracyjny), warto zacachowac srodowisko.

Powodzenia.
