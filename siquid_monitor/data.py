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

import numpy as np
import pandas as pd
from scipy import optimize

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

# CHSH S was added on 2026-06-24 but is BUGGY until 2026-06-29 (partner-confirmed); mask before this.
CHSH_VALID_FROM_EPOCH = pd.Timestamp("2026-06-29", tz="UTC").timestamp()
# Error-correction inefficiency for the THEORETICAL asymptotic key rate (LDPC f ~ 1.1).
KEY_RATE_EC_INEFFICIENCY = 1.1

# EXACT finite-key security parameters (R. Novak, Entropy 27(10):1032, 2025, eqs 18-23).
EPS_SECURITY = 1e-10  # target secrecy: Delta <= eps
FINITE_KEY_T = 40  # eq (18): eps_ec = 2^-t (error-verification hash length)
# c_bar = 1/2 (ideal min-entropy overlap) => log2(1/c_bar) = 1 in eq (22).


def binary_entropy(q):
    """Shannon binary entropy h2(q) in bits; 0 at the endpoints {0,1}, NaN for NaN/out-of-range.
    (NaN must propagate so NaN-QBER rows don't masquerade as h2=0 -> secure.) Vectorized."""
    q = np.asarray(q, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        h = -q * np.log2(q) - (1 - q) * np.log2(1 - q)
    h = np.where((q == 0) | (q == 1), 0.0, h)  # endpoints -> 0
    return np.where((q >= 0) & (q <= 1), h, np.nan)  # NaN / out-of-range -> NaN (propagates)


def theoretical_key_rate(coinc_rate, qber, f=KEY_RATE_EC_INEFFICIENCY):
    """THEORETICAL asymptotic BBM92 secret-key rate (bits/s), NOT a measured key.

    Secret fraction per sifted bit r = 1 - (1+f)*h2(QBER) (Shor-Preskill / BBM92, one-way EC+PA),
    times the sifted-coincidence rate. NaN where insecure (r<=0, QBER above ~11%) or inputs missing
    — i.e. "N/A unless a positive secure rate is theoretically possible."
    """
    q = np.asarray(qber, float)
    r = 1.0 - (1.0 + f) * binary_entropy(q)
    rate = np.asarray(coinc_rate, float) * r
    ok = np.isfinite(rate) & (r > 0)
    return np.where(ok, rate, np.nan)


def _finite_key_length_scalar(m, delta, eps=EPS_SECURITY, f_e=KEY_RATE_EC_INEFFICIENCY, t=FINITE_KEY_T):
    """EXACT epsilon-secure final key length l for ONE accumulated block (Novak 2025, eqs 20-23).

    No closed form -> numerical optimization. For a block of m sifted same-basis coincidences at
    error rate delta, we sacrifice k bits for parameter estimation (n = m - k remain) and choose a
    smoothing parameter nu in (0, 1/2 - delta); the largest secure key is

        l(k, nu) = n*(1 - h(delta+nu)) - r - t + 2*log2(2*(eps - eps_ec - eps_pe(k, nu)))     [from eq 22]

    with r = f_e*n*h(delta) [eq 36], eps_ec = 2^-t [eq 18], eps_pe = 2*exp(-n*k^2*nu^2/(m(k+1))) [eq 21],
    subject to the security budget eps_ec + eps_pe(k, nu) < eps [eq 23]. We maximize l over (k, nu):
    inner 1-D optimize over nu for each k, outer grid-scan + refine over k. Returns 0.0 if no positive
    length is achievable. c_bar = 1/2 => log2(1/c_bar) = 1.
    """
    if not (np.isfinite(m) and np.isfinite(delta)) or m < 2 or delta <= 0 or delta >= 0.5:
        return 0.0
    hd = float(binary_entropy(delta))
    # Finite-size l/m never exceeds the asymptotic secret fraction; if that is <= 0, no key exists.
    if 1.0 - (1.0 + f_e) * hd <= 0.0:
        return 0.0
    eps_ec = 2.0**-t
    if eps <= eps_ec:
        return 0.0
    nu_hi = 0.5 - delta
    log_arg = np.log(2.0 / (eps - eps_ec))  # > 0; sets the nu below which eps_pe exhausts the budget

    def neg_l_given_k(k):
        n = m - k
        if k < 1.0 or n < 1.0:
            return 0.0  # invalid split -> l = 0
        # eps_pe(k, nu) < eps - eps_ec  <=>  nu > nu_lo; below nu_lo the budget is negative.
        nu_lo2 = m * (k + 1.0) * log_arg / (n * k * k)
        if nu_lo2 >= nu_hi * nu_hi:
            return 0.0  # no admissible nu for this k -> l = 0
        nu_lo = np.sqrt(nu_lo2)
        r = f_e * n * hd

        def neg_l(nu):
            eps_pe = 2.0 * np.exp(-(n * k * k * nu * nu) / (m * (k + 1.0)))
            budget = eps - eps_ec - eps_pe
            if budget <= 0.0:
                return 0.0
            length = n * (1.0 - float(binary_entropy(delta + nu))) - r - t + 2.0 * np.log2(2.0 * budget)
            return -length

        res = optimize.minimize_scalar(neg_l, bounds=(nu_lo * (1 + 1e-9), nu_hi * (1 - 1e-9)), method="bounded")
        return float(res.fun)  # best -l for this k (< 0 iff a positive-length key exists)

    # Outer optimization over the split k. The optimum is a sizeable fraction of m (Novak's worked
    # example: k/m ~ 0.4), so scan k as fractions of m, then refine around the best with a bounded solve.
    ks = np.unique(np.clip((m * np.linspace(0.01, 0.99, 80)).astype(np.int64), 1, int(m) - 1)).astype(float)
    vals = np.array([neg_l_given_k(k) for k in ks])
    j = int(np.argmin(vals))
    best = vals[j]
    k_lo, k_hi = ks[max(j - 1, 0)], ks[min(j + 1, len(ks) - 1)]
    if k_hi > k_lo:
        res = optimize.minimize_scalar(neg_l_given_k, bounds=(k_lo, k_hi), method="bounded")
        best = min(best, float(res.fun))
    length = -best
    return float(np.floor(length)) if length >= 1.0 else 0.0


def finite_key_length(m, qber, eps=EPS_SECURITY, f_e=KEY_RATE_EC_INEFFICIENCY, t=FINITE_KEY_T):
    """Vectorized EXACT finite-key length (see `_finite_key_length_scalar`).

    Returns 0 where no epsilon-secure key is achievable. Rows that cannot possibly yield a key
    (invalid/high QBER, m too small, asymptotic fraction <= 0) are short-circuited to 0 without
    optimization, so this is cheap on data whose QBER is mostly above the security threshold.
    """
    m_arr = np.atleast_1d(np.asarray(m, float))
    q_arr = np.atleast_1d(np.asarray(qber, float))
    m_arr, q_arr = np.broadcast_arrays(m_arr, q_arr)
    out = np.zeros(m_arr.shape, float)
    hq = binary_entropy(q_arr)
    feasible = (
        np.isfinite(m_arr) & np.isfinite(hq) & (q_arr > 0) & (q_arr < 0.5) & (m_arr >= 2) & (1.0 - (1.0 + f_e) * hq > 0)
    )
    for i in np.nonzero(feasible.ravel())[0]:
        out.ravel()[i] = _finite_key_length_scalar(float(m_arr.ravel()[i]), float(q_arr.ravel()[i]), eps, f_e, t)
    return float(out.reshape(())) if np.ndim(m) == 0 and np.ndim(qber) == 0 else out


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
    # .copy() consolidates the freshly-read (wide, block-fragmented) frame so the few single-column
    # inserts below don't each raise pandas' PerformanceWarning.
    ar = pd.read_csv(data_dir / "alice_results.csv").sort_values("timestamp").reset_index(drop=True).copy()
    it = pd.read_csv(data_dir / "qber_iterlog.csv").sort_values("timestamp").reset_index(drop=True).copy()

    # display time = Europe/Ljubljana local (measurement site). floor to ms: epoch-second
    # floats carry nonzero nanoseconds -> Plotly warns per point (floods logs / burns CPU).
    ar["t"] = to_local(ar["timestamp"]).dt.floor("ms")
    it["t"] = to_local(it["timestamp"]).dt.floor("ms")
    overlap = ar["overlap_duration_sec"]
    c_corr = ar[[f"C_{lab}" for lab in CORRELATED]].sum(axis=1)
    c_err = ar[[f"C_{lab}" for lab in ERRORS]].sum(axis=1)

    # CHSH-mode rows (from 2026-06-24) log CHSH but leave visibility/QBER blank, though their same-basis
    # counts ARE present. Recompute those from C_* with the exact logged formula (verified byte-identical
    # on standard rows) so visibility/QBER are continuous. Fill ONLY blanks (assigns to EXISTING columns).
    need = ar["QBER_total"].isna()
    with np.errstate(divide="ignore", invalid="ignore"):
        v_hv = (ar.C_HH + ar.C_VV - ar.C_HV - ar.C_VH) / (ar.C_HH + ar.C_VV + ar.C_HV + ar.C_VH)
        v_da = (ar.C_DD + ar.C_AA - ar.C_DA - ar.C_AD) / (ar.C_DD + ar.C_AA + ar.C_DA + ar.C_AD)
    v_tot = (v_hv + v_da) / 2
    for col, val in [
        ("vis_HV", v_hv),
        ("vis_DA", v_da),
        ("visibility", v_tot),
        ("QBER_HV", (1 - v_hv) / 2),
        ("QBER_DA", (1 - v_da) / 2),
        ("QBER_total", (1 - v_tot) / 2),
    ]:
        ar[col] = ar[col].where(~need, val)

    # CHSH S-value: present only in newer datasets, and valid only from 2026-06-29 (buggy before);
    # treat exact 0 as "not computed". Masked to NaN elsewhere -> panels show N/A. Source-agnostic:
    # if the column is absent, chsh_s is all-NaN and the panel simply shows nothing.
    if "CHSH_S_value" in ar.columns:
        chsh_s = ar["CHSH_S_value"].where((ar["timestamp"] >= CHSH_VALID_FROM_EPOCH) & (ar["CHSH_S_value"] != 0))
    else:
        chsh_s = pd.Series(np.nan, index=ar.index)

    # EXACT finite-key length, cumulative block model (a): at each measurement accumulate ALL sifted
    # same-basis coincidences and the running QBER since the record start, then solve eqs (20)-(23).
    # For this link the running QBER never approaches the finite-key threshold (stays ~0.37), so l is 0
    # throughout and key_rate_finite is all-NaN (honest N/A) -- see figures.md. The solver short-circuits
    # insecure rows, so this stays cheap despite being per-row.
    cum_sifted = (c_corr + c_err).cumsum()
    cum_qber = (c_err.cumsum() / cum_sifted).where(cum_sifted > 0)
    key_length_finite = finite_key_length(cum_sifted.to_numpy(), cum_qber.to_numpy())
    elapsed_s = (ar["timestamp"] - ar["timestamp"].iloc[0]).clip(lower=0).to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        key_rate_finite = np.where((key_length_finite > 0) & (elapsed_s > 0), key_length_finite / elapsed_s, np.nan)

    # Add all derived columns in ONE concat (many single `ar[col]=` inserts fragment a wide frame).
    # coinc_rate = all-16 total (link health); key rate uses the SIFTED same-basis rate (corr+err) — NOT
    # coinc_rate — because total_coincidences now includes the cross-basis (CHSH) pairs, discarded in sifting.
    ar = pd.concat(
        [
            ar,
            pd.DataFrame(
                {
                    "coinc_rate": ar["total_coincidences"] / overlap,
                    "C_correlated": c_corr,
                    "C_error": c_err,
                    "corr_rate": c_corr / overlap,
                    "err_rate": c_err / overlap,
                    "vis_recomputed": need,
                    "chsh_s": chsh_s,
                    "key_rate_theo": theoretical_key_rate(c_corr / overlap + c_err / overlap, ar["QBER_total"]),
                    "cum_sifted": cum_sifted,
                    "cum_qber": cum_qber,
                    "key_length_finite": key_length_finite,
                    "key_rate_finite": key_rate_finite,
                },
                index=ar.index,
            ),
        ],
        axis=1,
    )

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
        name="LJ-Drnovo (recorded 2026-06-19 to 07-02, times in Europe/Ljubljana)",
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
