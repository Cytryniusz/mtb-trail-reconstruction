# Zrodla danych

Repo nie zawiera surowych danych (zbyt duze pliki — patrz `.gitignore`). Ponizej instrukcje skad pobrac komplet wejsciowy dla wybranej trasy w Polsce.

## 1. LiDAR (LAS/LAZ) — GUGiK

**Najlepsze zrodlo:** [Geoportal — Dane do pobrania](https://www.geoportal.gov.pl/dane/dane-do-pobrania) lub bezposrednio [Skorowidze ISOK / NMT 1m](https://opendata.geoportal.gov.pl/).

Krok po kroku:
1. Wejdz na **dane.gov.pl** -> "LiDAR" lub na **geoportal.gov.pl** -> warstwa "Dane LiDAR".
2. Znajdz arkusz pokrywajacy obszar trasy (system map kartograficznych M-34-89-C-c-2-3 itp.).
3. Pobierz plik `.laz` — standardowo to chmura **klasyfikowana** w ukladzie **EPSG:2180** z rozdzielczoscia 12–20 punktow/m^2.
4. Skopiuj do `data/lidar/`.

Format pliku to nazwa techniczna typu `78142_1411124_M-34-89-C-c-2-3-2.laz`.

## 2. Ortofotomapa (GeoTIFF) — GUGiK

**Zrodlo:** [Geoportal — Ortofotomapa](https://www.geoportal.gov.pl/uslugi/ortofotomapa).

Krok po kroku:
1. Geoportal -> warstwa "Ortofotomapa" -> wybor "Pelne pokrycie" + "Dane do pobrania".
2. Pobierz kafle TIF pokrywajace ten sam obszar co LiDAR (zwykle 1–2 kafle wystarcza).
3. Skopiuj do `data/ortho/`.

Rozdzielczosc zwykle **25 cm/px** lub **5 cm/px** (najnowsza). Pliki w EPSG:2180.

## 3. Slad GPS (GPX)

**Najprostsze opcje:**
- **Strava** — eksport sladu z aktywnosci: Aktywnosc -> "..." -> "Eksport GPX".
- **Garmin Connect** — Aktywnosc -> ikona kola zebatego -> "Eksportuj jako GPX".
- **Komoot / Wahoo / Bryton** — analogicznie.

Wymagania jakosci:
- min. 1 punkt na 1–3 sekundy (typowe ustawienie),
- dane wysokosci `<ele>` jesli to mozliwe (urzadzenia z barometrem sa duzo lepsze niz tylko GPS),
- format **GPX 1.1** (standard).

Skopiuj do `data/gps_trace/`.

## 4. Alternatywne zrodla

- **OpenTopography** (https://opentopography.org/) — globalne pokrycie LiDAR, czasem przydatne poza Polska.
- **USGS 3DEP** (USA), **Geoscience Australia ELVIS** (AU) — odpowiedniki narodowe.
- **OpenStreetMap** — sciezki MTB sa zwykle dobrze namapowane; mozna pobrac dla calego regionu i porownac z wlasnym sladem GPS.

## 5. Mala probka do testow

Jesli chcesz wyprobowac pipeline bez pelnego pobierania danych:
- `examples/sample_results/` zawiera juz wygenerowane wyniki dla referencji,
- mozesz wziac maly fragment LAZ-a (np. 100 m × 100 m) przez `pdal translate input.laz crop.laz --bounds "([xmin,xmax],[ymin,ymax])"` i uzyc go jako test.

## Polityka prywatnosci sladow

GPX-y zawieraja precyzyjne wspolrzedne — uwazaj przy publikacji wlasnych przejazdow. Domyslnie `data/` jest w `.gitignore`, ale jesli chcesz dolaczyc przyklady do publicznego repo:
- usun adres domu (przytnij pierwsze/ostatnie 100 m),
- rozwaz losowy offset czasu (znacznik startu nie powinien byc dokladny).
