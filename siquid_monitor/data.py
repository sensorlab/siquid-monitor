"""Data loading for the playback-mock — source-agnostic.

A `Dataset` bundles the measurement spine (visibility/QBER/coincidences/singles/sync)
with the EPC-voltage series, on a shared time axis (no row-merge; see docs/data.md).
Today only the LJ-Drnovo repo CSVs exist; to add UVTP-MDP, write another `load_*`
that returns a `Dataset` with the same columns — the figures don't change.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

# label -> (alice_channel, bob_channel): the acquisition channel-pair map for this dataset
PAIRS = [
    ("HH", 4, 1),
    ("HV", 4, 2),
    ("VH", 2, 1),
    ("VV", 2, 2),
    ("DD", 1, 4),
    ("DA", 1, 3),
    ("AD", 3, 4),
    ("AA", 3, 3),
]
LABELS = [p[0] for p in PAIRS]
CORRELATED = ["HH", "VV", "DD", "AA"]  # outcomes a Phi+ state should give
ERRORS = ["HV", "VH", "DA", "AD"]
VOLTAGE_JOIN_TOL_S = 1.0  # nearest-timestamp tolerance for tagging has_voltage (gate-1)
LOCAL_TZ = "Europe/Ljubljana"  # where the measurements were taken; used for DISPLAY only

# Partner acquisition repo is a pristine clone kept under external/ (see root README);
# its Data/ holds the CSVs we replay. Overridable via $SIQUID_DATA_DIR (used by the Docker
# image, which mounts the data at /data). Centralized here so a rename is a one-line change.
DEFAULT_DATA_DIR = (
    Path(os.environ["SIQUID_DATA_DIR"])
    if os.environ.get("SIQUID_DATA_DIR")
    else Path(__file__).resolve().parent.parent / "external" / "long-distance-entanglement" / "Data"
)


def to_local(epoch):
    """Epoch seconds (UTC) -> tz-naive Europe/Ljubljana wall-clock (for display).
    Works on a scalar or a Series. Raw timestamps stay UTC; only what we show is local."""
    ts = pd.to_datetime(epoch, unit="s", utc=True)
    if isinstance(ts, pd.Series):
        return ts.dt.tz_convert(LOCAL_TZ).dt.tz_localize(None)
    return ts.tz_convert(LOCAL_TZ).tz_localize(None)


@dataclass
class Dataset:
    """Spine = alice_results-derived; voltages = qber_iterlog-derived. Shared time axis."""

    name: str
    measurements: pd.DataFrame  # + t, coinc_rate, C_correlated, C_error, has_voltage
    voltages: pd.DataFrame  # + t, V0..V7

    @property
    def time_range(self) -> tuple[pd.Timestamp, pd.Timestamp]:
        t = self.measurements["t"]
        return t.min(), t.max()


def load_repo_dataset(data_dir: str | Path = DEFAULT_DATA_DIR) -> Dataset:
    """Load the LJ-Drnovo CSVs from the partner clone (external/…/Data) into a Dataset."""
    data_dir = Path(data_dir)
    ar = pd.read_csv(data_dir / "alice_results.csv").sort_values("timestamp").reset_index(drop=True)
    it = pd.read_csv(data_dir / "qber_iterlog.csv").sort_values("timestamp").reset_index(drop=True)

    # display time = Europe/Ljubljana local (measurement site). floor to ms: epoch-second
    # floats carry nonzero nanoseconds -> Plotly warns per point (floods logs / burns CPU).
    ar["t"] = to_local(ar["timestamp"]).dt.floor("ms")
    it["t"] = to_local(it["timestamp"]).dt.floor("ms")
    ar["coinc_rate"] = ar["total_coincidences"] / ar["overlap_duration_sec"]
    ar["C_correlated"] = ar[[f"C_{lab}" for lab in CORRELATED]].sum(axis=1)
    ar["C_error"] = ar[[f"C_{lab}" for lab in ERRORS]].sum(axis=1)

    volt = pd.DataFrame(
        it["voltages"].apply(ast.literal_eval).tolist(),
        columns=[f"V{i}" for i in range(8)],
        index=it.index,
    )
    it = pd.concat([it, volt], axis=1)

    # tag which measurements coincide with an optimizer voltage row (gate-1 nearest join, <=1 s)
    nn = pd.merge_asof(
        ar[["timestamp"]],
        it[["timestamp"]].rename(columns={"timestamp": "_it"}),
        left_on="timestamp",
        right_on="_it",
        direction="nearest",
    )
    ar["has_voltage"] = (ar["timestamp"] - nn["_it"]).abs() <= VOLTAGE_JOIN_TOL_S

    return Dataset(
        name="LJ-Drnovo (recorded 2026-06-19 to 24, times in Europe/Ljubljana)",
        measurements=ar,
        voltages=it,
    )


def channel_singles(m: pd.DataFrame) -> pd.DataFrame:
    """Per-physical-channel singles rate (cps). Labels sharing a channel share singles,
    so we pick one representative label per distinct Alice/Bob channel."""
    out = {}
    for side, evcol in [("Alice", "alice_events_"), ("Bob", "bob_events_")]:
        seen: dict[int, str] = {}
        for name, a, b in PAIRS:
            seen.setdefault(a if side == "Alice" else b, f"{evcol}{name}")
        for ch, colname in sorted(seen.items()):
            out[f"{side} ch{ch}"] = m[colname] / m["overlap_duration_sec"]
    return pd.DataFrame(out, index=m.index)
