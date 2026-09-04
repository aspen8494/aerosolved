"""results.py -- parse a run's post-processing output into a dashboard spec.

A dashboard spec is a JSON-serialisable dict describing exactly what the
frontend needs to render, without the frontend ever importing numpy or
touching the case tree.  See the module header block in git history.

The dashboard builder first tries a per-shape extractor registered for the
case name; if none exists or it fails, it falls back to a generic scanner
that looks at whatever `*.dat` / `*.xy` / `*.txt` files exist under the
postProcessing tree.
"""
from __future__ import annotations

import os
import re
import logging
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)


def _safe_load(path: str) -> Optional[np.ndarray]:
    """Load a numpy array; return None on any error.  Never raises."""
    try:
        arr = np.loadtxt(path)
        return None if arr.size == 0 else arr
    except Exception:
        return None


def _cols(arr) -> int:
    """column count, safe for 1-D arrays."""
    return int(arr.shape[1]) if arr.ndim > 1 else 0


def _sectional_diameter_bins(y_min: float, y_max: float, n: int,
                            rhol: float = 1000.0,
                            kind: str = "logarithmic") -> np.ndarray:
    """Bin-midpoint diameters for a fixedSectional grid.

    For `logarithmic` grids the `y` values are log-spaced (number density per
    log-diameter); bin midpoints are the geometric means of adjacent grid
    points.  For `uniform` grids they are the arithmetic means.
    """
    if kind == "logarithmic":
        logy = np.linspace(np.log(y_min), np.log(y_max), n + 1, endpoint=True)
        x = np.exp((logy[:-1] + logy[1:]) / 2.0)
    else:
        y = np.linspace(y_min, y_max, n + 1)
        x = (y[:-1] + y[1:]) / 2.0
    return (x / rhol * 6.0 / np.pi) ** (1.0 / 3.0)


# ---------------------------------------------------------------------------
# Per-shape extractors
# ---------------------------------------------------------------------------

def _extract_rain(case_path: str, pp_path: str) -> dict:
    """The verified `rain` smoke case: numberFlux + massFlux over time."""
    spec = {
        "case": os.path.basename(case_path),
        "postproc_path": pp_path,
        "plots": [],
        "scalars": [],
        "files": [],
        "warnings": [],
    }

    # Diameter-grid defaults from the `rain` plot.py.
    y_min, y_max, N, rhol = 1e-24, 1e-10, 10, 1000.0
    ap_path = os.path.join(case_path, "constant", "aerosolProperties")
    if os.path.isfile(ap_path):
        text = open(ap_path).read()
        for m in (re.search(r"yMin\s*([\d.eE+-]+)", text),
                    re.search(r"yMax\s*([\d.eE+-]+)", text),
                    re.search(r"\bN\s*([\d.eE+-]+)", text)):
            if m:
                try:
                    val = float(m.group(1))
                except Exception:
                    continue
                key = m.group(0).split()[0].lower()
                if key == "ymin":
                    y_min = val
                elif key == "ymax":
                    y_max = val
                elif key == "n":
                    N = int(val)
    diameters = _sectional_diameter_bins(y_min, y_max, N, rhol)

    # Number-flux time series
    n_path = os.path.join(pp_path, "numberFlux", "0")
    top_n = _safe_load(os.path.join(n_path, "patch.top.dat"))
    bot_n = _safe_load(os.path.join(n_path, "patch.bottom.dat"))
    if top_n is None and bot_n is None:
        spec["warnings"].append("no numberFlux data found in %s" % n_path)
    else:
        plot_n = {
            "title": "Number flux vs particle diameter",
            "x_axis": "d [m]", "y_axis": "particle flux [#/s]",
            "xscale": "log", "yscale": "log", "series": []}
        ref = top_n if top_n is not None else bot_n
        if ref is not None and ref.ndim > 1:
            times = ref[:, 0]
            n_slices = len(times)
            for frac, marker in ((0.33, "o"), (0.66, "d"), (1.0, "s")):
                idx = int(n_slices * frac) - 1 if frac < 1.0 else n_slices - 1
                tval = float(times[idx])
                if top_n is not None:
                    plot_n["series"].append({
                        "label": "influx  t=%.1f s" % tval,
                        "diameter": list(diameters[:_cols(top_n) - 1] if _cols(top_n) else []),
                        "value": list(np.abs(top_n[idx, 1:]))})
                if bot_n is not None:
                    plot_n["series"].append({
                        "label": "outflux t=%.1f s" % tval,
                        "diameter": list(diameters[:_cols(bot_n) - 1] if _cols(bot_n) else []),
                        "value": list(np.abs(bot_n[idx, 1:]))})
            spec["plots"].append(plot_n)

    # Mass-flux budget
    m_path = os.path.join(pp_path, "massFlux", "0")
    top_m = _safe_load(os.path.join(m_path, "patch.top.dat"))
    bot_m = _safe_load(os.path.join(m_path, "patch.bottom.dat"))
    if top_m is not None or bot_m is not None:
        species = ["vapor", "air", "droplet", "sum"]
        plot_m = {
            "title": "Mass flux budget",
            "x_axis": "species / patch",
            "y_axis": "mass flux [ug/s]",
            "series": []}
        for name, arr in (("top", top_m), ("bottom", bot_m)):
            if arr is None:
                continue
            arr = np.atleast_2d(arr)
            row = arr[-1]
            vals = [float(row[1]), float(row[2]), float(row[3]),
                    float(np.sum(row[1:])) * 1e9]
            plot_m["series"].append({"group": name, "label": name,
                                        "value": vals, "category": species})
            spec["scalars"].append({
                "name": "total_mass_%s_last" % name,
                "value": float(np.sum(arr[-1, 1:]) * 1e9),
                "unit": "ug/s"})
        spec["plots"].append(plot_m)

    # Record the raw data files we consumed
    for sub, name in (("numberFlux", "patch.top.dat"),
                        ("numberFlux", "patch.bottom.dat"),
                        ("massFlux", "patch.top.dat"),
                        ("massFlux", "patch.bottom.dat")):
        p = os.path.join(pp_path, sub, "0", name)
        arr = _safe_load(p)
        if arr is not None:
            spec["files"].append({"path": p, "shape": list(arr.shape),
                                        "rows": int(arr.shape[0]) if arr.ndim > 0 else 0,
                                        "cols": _cols(arr)})
    return spec


def _extract_generic(case_name: str, pp_path: str) -> dict:
    """Falls back to a generic scan of any data files under postProcessing/."""
    spec = {
        "case": case_name,
        "postproc_path": pp_path,
        "plots": [],
        "scalars": [],
        "files": [],
        "warnings": [],
    }
    if not os.path.isdir(pp_path):
        spec["warnings"].append("postProcessing directory not found at %s" % pp_path)
        return spec

    for root, _, fnames in os.walk(pp_path):
        for fn in fnames:
            if not (fn.endswith(".dat") or fn.endswith(".xy")
                    or fn.endswith(".txt")):
                continue
            full = os.path.join(root, fn)
            arr = _safe_load(full)
            if arr is None:
                continue
            spec["files"].append({"path": full, "shape": list(arr.shape),
                                        "rows": int(arr.shape[0]) if arr.ndim > 0 else 0,
                                        "cols": _cols(arr)})

    if not spec["files"]:
        spec["warnings"].append("no usable postProcessing data files under %s"
                                % pp_path)
        return spec

    # Render up to 3 time-series plots from whatever we found
    for f in spec["files"][:3]:
        arr = _safe_load(f["path"])
        if arr is None or arr.ndim < 2 or arr.shape[1] < 2:
            continue
        rel = os.path.relpath(f["path"], pp_path)
        x = arr[:, 0]
        series = [{"label": "col %d" % c, "x": list(x), "y": list(arr[:, c])}
                for c in range(1, arr.shape[1])]
        spec["plots"].append({
            "title": "Data -- %s" % rel,
            "x_axis": "column 0 (probably t)",
            "y_axis": "column 1..%d" % (arr.shape[1] - 1),
            "series": series})
    return spec


# Shape registry
_EXTRACTORS = {
    "rain":               lambda cp, pp: _extract_rain(cp, pp),
    "saturatedBox":       lambda cp, pp: _extract_rain(cp, pp),
    "cavity":             lambda cp, pp: _extract_rain(cp, pp),
    "aspiration":         lambda cp, pp: _extract_generic(cp, pp),
    "bentPipe":           lambda cp, pp: _extract_generic(cp, pp),
    "exposureCell":       lambda cp, pp: _extract_generic(cp, pp),
}


def latest_postproc(run_dir: str) -> Optional[str]:
    """Locate a postProcessing/ directory under a run dir, newest first."""
    base = os.path.join(run_dir, "postProcessing")
    if os.path.isdir(base) and os.listdir(base):
        return base
    # Some cases write postProcessing/TimeNNN/ ; find the latest Time dir
    pp = []
    for root, dirs, _ in os.walk(run_dir):
        for d in list(dirs):
            if d.startswith("postProcessing") or d == "postProcessing":
                pp.append(os.path.join(root, d))
    pp = sorted(pp, key=lambda p: os.path.getmtime(p), reverse=True)
    return pp[0] if pp else None


def build_dashboard(case_name: str, pp_path: str) -> dict:
    """Build a dashboard spec for ``case_name`` using ``pp_path``.

    Tries the per-shape extractor first; falls back to a generic scanner on
    any error.  Never raises.
    """
    # Accept either the run dir (containing postProcessing/) or the
    pp_dir = pp_path
    nested = os.path.join(pp_path, "postProcessing")
    if os.path.isdir(nested) and not os.path.isdir(os.path.join(pp_path, "numberFlux")):
        pp_dir = nested
    extractor = _EXTRACTORS.get(case_name)
    try:
        spec = (extractor(case_name, pp_dir) if extractor
                else _extract_generic(case_name, pp_dir))
        return spec
    except Exception as e:
        log.warning("extractor for %r failed: %r", case_name, e)
        spec = _extract_generic(pp_dir, None) if False else _extract_generic(case_name, pp_dir)
        spec["warnings"].append("per-shape extractor failed: %r -- using generic scanner" % e)
        return spec


def spec_json(spec: dict, out_path: str) -> str:
    """Serialise ``spec`` to JSON at ``out_path``; return the path."""
    import json
    with open(out_path, "w") as f:
        json.dump(spec, f, indent=2, default=lambda o: list(o) if isinstance(o, np.ndarray) else str(o))
    return out_path
