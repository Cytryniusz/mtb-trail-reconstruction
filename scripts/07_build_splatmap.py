"""Cienki wrapper CLI. Logika jest w src/mtb_terrain/.
Dodaje src/ do sys.path zeby skrypty dzialaly bez `pip install`.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# Krok 7: generacja 4-kanalowej splatmapy (RGBA = ground/path/undergrowth/rock) dla Unity Terrain.
from mtb_terrain.splatmap.pipeline import main

if __name__ == "__main__":
    main()
