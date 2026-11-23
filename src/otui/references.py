"""Reference objectives for open TIMES models.

GAMS TIMES with CPLEX is TIMES's reference implementation and solver. These objectives let otui
compare a TIMES.jl run with GAMS TIMES on a computer without GAMS (the parity panel in the Run
tab): a model matches an entry when its run file and the DD files the run file names are the same
files, byte for byte (line endings aside).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

# the DD files a run file names ($include / $batinclude lines, or any other mention)
DD_NAME = re.compile(r"[\w.\-]+\.dd\b", re.IGNORECASE)

# fingerprint -> the reference; objectives of GAMS TIMES 4.9.7 + CPLEX
REFERENCES: dict[str, dict[str, Any]] = {
    # TIMES_Demo demo12 (etsap-TIMES/TIMES_Demo @ 962db86, CC BY 4.0): TIMES.jl's reference
    "57c88f22819ff0dabc8acb760289b2475ff62b6088ec305af151097d26680c3f": {
        "model": "TIMES_Demo demo12",
        "objective": 23960685.737007827,
        "source": "TIMES.jl's reference run (GAMS TIMES 4.9.7 + CPLEX)",
    },
    # OTCGEN gc_ref (esma-ie/OTCGEN @ 08af95f, dd/gc_ref, CC BY-SA 4.0)
    "230c3016003563566bddbacdafc74a22a2d720ab3c6a30ca2397ffe75e557977": {
        "model": "OTCGEN gc_ref",
        "objective": 756404387.8201401,
        "source": "TIMES.jl's reference run (GAMS TIMES 4.9.7 + CPLEX)",
    },
    # OTNYC s1_nyc_ref (esma-ie/OTNYC @ bc1d276, dd/s1_nyc_ref, CC BY-SA 4.0)
    "a930548ed0b0a48aaa2a1bd705e5123b2629ed846fb91f2c7c42d9aa328c7015": {
        "model": "OTNYC s1_nyc_ref",
        "objective": 9657725.872571386,
        "source": "TIMES.jl's reference run (GAMS TIMES 4.9.7 + CPLEX)",
    },
    # OTUS9r reference (esma-ie/OTUS9r @ 598e179, dd/reference, CC BY-SA 4.0)
    "39d582dbd4b04b9525cc8a3bd899cc4690f84b03d0245b77cadf3142ebec5d36": {
        "model": "OTUS9r reference",
        "objective": 192206507156.18253,
        "source": "TIMES.jl's reference run (GAMS TIMES 4.9.7 + CPLEX)",
    },
}


def _digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def fingerprint(run: Path) -> str:
    """The run file and the DD files beside it that it names, hashed by content."""
    text = run.read_text(encoding="utf-8", errors="replace")
    here = {p.name.lower(): p for p in run.parent.iterdir() if p.is_file()}
    names = sorted({n.lower() for n in DD_NAME.findall(text)} & here.keys())
    h = hashlib.sha256(_digest(run).encode())
    for n in names:
        h.update(f"\n{n}:{_digest(here[n])}".encode())
    return h.hexdigest()


def reference_for(run: Path) -> dict[str, Any] | None:
    try:
        return REFERENCES.get(fingerprint(run))
    except OSError:
        return None
