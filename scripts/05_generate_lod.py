"""Cienki wrapper CLI. Logika jest w src/mtb_terrain/.
Dodaje src/ do sys.path zeby skrypty dzialaly bez `pip install`.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# Krok 5: cleanup mesh-a + kaskada LOD0/1/2/3 dla Unity.
from mtb_terrain.mesh.pipeline import main

if __name__ == "__main__":
    main()
