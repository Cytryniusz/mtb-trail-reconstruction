# data/

Katalog na surowe dane wejsciowe. **Cala zawartosc jest gitignorowana** (poza tym README).

## Oczekiwana struktura

```
data/
|-- lidar/        *.laz / *.las       (LiDAR z GUGiK lub innego zrodla)
|-- ortho/        *.tif / *.tiff      (ortofotomapa GeoTIFF)
`-- gps_trace/    *.gpx               (slad GPS z urzadzenia / Stravy)
```

Skad pobrac: [`../docs/data_sources.md`](../docs/data_sources.md).

## Wymagania dotyczace danych

| Typ | Format | CRS | Uwagi |
|---|---|---|---|
| LiDAR | LAZ/LAS | EPSG:2180 (PL-1992) | Klasyfikowany (ASPRS), min. 4 pts/m^2 |
| Ortofoto | GeoTIFF | EPSG:2180 | RGB, 8-bit, rozdzielczosc <= 0.25 m/px |
| GPS | GPX 1.1 | EPSG:4326 (WGS84) | Z wysokoscia barometryczna jesli to mozliwe |

Jesli twoje dane sa w innym CRS, pipeline przeprowadzi reprojekcje (`geopandas`/`rasterio`), ale lepiej zaczac od spojnego ukladu.
