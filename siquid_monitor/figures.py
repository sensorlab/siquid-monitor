"""Plotly figure builders for the playback-mock. Design rationale: ../docs/figures.md.

Honest-replay rules implemented here:
- Scattergl everywhere; lines broken across time gaps > GAP_S (no fabricated interpolation).
- Noisy metrics: faint raw + bold *gap-aware* rolling median (a descriptive smoother, not a fit).
- Correct reference lines (CHSH 1/sqrt(2), separability 1/3, QKD ~11% QBER, 50% random).
- Optimizer-active periods shaded (a search, not passive monitoring).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .data import CORRELATED, LABELS, channel_singles, to_local

GAP_S = 120.0  # break any line across time gaps longer than this (s)
MEDIAN_W = 51  # rolling-median window (samples)
PALETTE = {"total": "#2c5fb3", "H/V": "#2f8f3e", "D/A": "#c79100"}


def break_gaps(t, y):
    """Insert NaN where consecutive samples are >GAP_S apart, so the line is not
    drawn across periods with no data. Vectorized (no Python per-point loop)."""
    tv = pd.to_datetime(pd.Series(np.asarray(t))).to_numpy()  # datetime64[ns]
    yv = np.asarray(y, dtype=float)
    if tv.size < 2:
        return tv, yv
    dt_s = np.diff(tv).astype("timedelta64[s]").astype(float)
    brk = np.flatnonzero(dt_s > GAP_S) + 1  # insert NaN before these
    if brk.size == 0:
        return tv, yv
    return np.insert(tv, brk, tv[brk]), np.insert(yv, brk, np.nan)


def rmed(t, y, w=MEDIAN_W):
    """Gap-aware centered rolling median: computed within each contiguous segment
    (split on gaps > GAP_S) so the window never blends across a gap."""
    t = pd.Series(pd.to_datetime(np.asarray(t)))
    y = pd.Series(np.asarray(y, float))
    seg = (t.diff().dt.total_seconds() > GAP_S).cumsum()
    return y.groupby(seg).transform(lambda s: s.rolling(w, center=True, min_periods=5).median())


def gl(
    fig,
    t,
    y,
    name,
    row,
    col,
    *,
    width=0.9,
    color=None,
    dash=None,
    shape="linear",
    secondary_y=None,
    showlegend=True,
    legendgroup=None,
    opacity=1.0,
):
    """Add one gap-broken Scattergl line."""
    tg, yg = break_gaps(t, y)
    kw = {} if secondary_y is None else {"secondary_y": secondary_y}
    fig.add_trace(
        go.Scattergl(
            x=tg,
            y=yg,
            name=name,
            mode="lines",
            showlegend=showlegend,
            legendgroup=legendgroup,
            opacity=opacity,
            connectgaps=False,
            line={"width": width, "color": color, "dash": dash, "shape": shape},
        ),
        row=row,
        col=col,
        **kw,
    )


def raw_plus_median(fig, t, y, name, color, row, col, w=MEDIAN_W, secondary_y=None, median=None):
    """Faint gap-broken raw line + bold gap-aware rolling-median, sharing one legend entry.
    Pass `median` to reuse a precomputed series (avoids recomputing rmed every playback tick)."""
    gl(
        fig,
        t,
        y,
        name,
        row,
        col,
        width=0.5,
        color=color,
        opacity=0.25,
        showlegend=False,
        legendgroup=name,
        secondary_y=secondary_y,
    )
    med = rmed(t, y, w) if median is None else median
    gl(
        fig,
        t,
        med,
        name,
        row,
        col,
        width=1.8,
        color=color,
        legendgroup=name,
        secondary_y=secondary_y,
    )


# Columns we draw a median for; precompute once so playback ticks don't recompute the groupby.
PLOT_MEDIAN_COLS = [
    "visibility",
    "vis_HV",
    "vis_DA",
    "QBER_total",
    "QBER_HV",
    "QBER_DA",
    "coinc_rate",
    "corr_rate",
    "err_rate",
    "chsh_s",
    "key_rate_theo",
]


def precompute_medians(m, cols=PLOT_MEDIAN_COLS):
    """Add `<col>__med` gap-aware-median columns to `m` in place (call once after load)."""
    for c in cols:
        if c in m.columns:
            m[c + "__med"] = rmed(m["t"], m[c]).to_numpy()
    return m


def _med(m, c, y):
    """Precomputed median column if present, else compute on the fly."""
    mc = c + "__med"
    return m[mc] if mc in m.columns else rmed(m["t"], y)


def poisson_sigma_vis_total(m):
    """Per-measurement Poisson 1σ on the TOTAL visibility, from counting statistics only.

    Per basis V=(c−e)/(c+e) with independent Poisson counts → σ_V = 2·√(c·e)/N^1.5 (N=c+e).
    Total V = mean(V_HV, V_DA) ⇒ σ_total = ½·√(σ_HV² + σ_DA²). (QBER band uses σ_Q = σ_V/2,
    since vis = 1−2·QBER.) Returns a float Series on m.index; NaN where a basis has no counts
    (so the band honestly breaks rather than drawing a zero-width line)."""

    def basis_sigma(c, e):
        c = np.asarray(c, float)
        e = np.asarray(e, float)
        N = c + e
        with np.errstate(divide="ignore", invalid="ignore"):
            s = 2.0 * np.sqrt(c * e) / np.power(N, 1.5)
        s = np.where(N > 0, s, np.nan)
        return s

    s_hv = basis_sigma(m["C_HH"] + m["C_VV"], m["C_HV"] + m["C_VH"])
    s_da = basis_sigma(m["C_DD"] + m["C_AA"], m["C_DA"] + m["C_AD"])
    return pd.Series(0.5 * np.sqrt(s_hv**2 + s_da**2), index=m.index)


def precompute_poisson(m):
    """Add `vis_sigma_total` (per-measurement Poisson 1σ) and its gap-aware-smoothed
    `vis_sigma_total__sm` to `m` in place (call once after load, like precompute_medians)."""
    s = poisson_sigma_vis_total(m)
    m["vis_sigma_total"] = s.to_numpy()
    m["vis_sigma_total__sm"] = rmed(m["t"], s).to_numpy()
    return m


def _sigma(m):
    """Smoothed per-measurement Poisson σ on total visibility (precomputed if present)."""
    if "vis_sigma_total__sm" in m.columns:
        return m["vis_sigma_total__sm"]
    return rmed(m["t"], poisson_sigma_vis_total(m))


def add_band(fig, t, center, half, name, fillcolor, row, col, showlegend=True):
    """Shaded ±half band around `center`, gap-aware. NaN in any input breaks the fill
    (no band where the gap-breaker cut the line or where σ is undefined). Two zero-width
    Scattergl traces (upper, then lower with fill='tonexty') so it sits behind the lines."""
    center = np.asarray(center, float)
    half = np.asarray(half, float)
    tg, lo = break_gaps(t, center - half)
    _, hi = break_gaps(t, center + half)
    fig.add_trace(
        go.Scattergl(
            x=tg,
            y=hi,
            mode="lines",
            line={"width": 0},
            hoverinfo="skip",
            showlegend=False,
            legendgroup=name,
        ),
        row=row,
        col=col,
    )
    fig.add_trace(
        go.Scattergl(
            x=tg,
            y=lo,
            mode="lines",
            line={"width": 0},
            fill="tonexty",
            fillcolor=fillcolor,
            hoverinfo="skip",
            name=name,
            legendgroup=name,
            showlegend=showlegend,
        ),
        row=row,
        col=col,
    )


def optimizer_spans(m, merge_gap_s=300):
    """Contiguous time spans where the optimizer was actively changing voltages
    (has_voltage==True), merging spans separated by < merge_gap_s."""
    on = m["has_voltage"].values
    t = m["timestamp"].values
    spans = []
    start = last = None
    for i in range(len(m)):
        if on[i]:
            if start is None:
                start = t[i]
            last = t[i]
        elif start is not None:
            spans.append([start, last])
            start = None
    if start is not None:
        spans.append([start, last])
    merged = []
    for s in spans:
        if merged and s[0] - merged[-1][1] < merge_gap_s:
            merged[-1][1] = s[1]
        else:
            merged.append([s[0], s[1]])
    return merged


def add_optimizer_shading(fig, m, merge_gap_s=300):
    """Shade periods where the optimizer was actively searching (not passive monitoring)."""
    for s, e in optimizer_spans(m, merge_gap_s):
        # spans come from `timestamp` (UTC epoch); convert to local to match the x-axis (`t`).
        # floor to s + .to_pydatetime() keeps the figure JSON-clean (raw pandas Timestamp isn't
        # serializable) and avoids the nonzero-nanoseconds warning the epoch floats would trigger.
        fig.add_vrect(
            x0=to_local(s).floor("s").to_pydatetime(),
            x1=to_local(e).floor("s").to_pydatetime(),
            fillcolor="#999999",
            opacity=0.10,
            line_width=0,
            layer="below",
            row="all",
            col=1,
        )


def fig_headline(m, band=True):
    """Visibility + QBER. The PoC panel. `band`=draw the ±1σ Poisson (counting-statistics)
    envelope around the total-series trend (see poisson_sigma_vis_total)."""
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.09,
        subplot_titles=(
            "Coincidence-correlation visibility (corr-err)/(corr+err), per basis "
            "- faint=raw, bold=gap-aware median, shaded=+-1s Poisson (total)",
            "QBER (per basis)  [note vis = 1 - 2*QBER]",
        ),
    )
    # Poisson ±1σ envelope behind the lines (total visibility; QBER uses σ/2 since vis=1-2*QBER).
    # Drawn first so the raw/median lines and thresholds render on top.
    if band:
        sig = _sigma(m)
        add_band(
            fig,
            m.t,
            _med(m, "visibility", m["visibility"]),
            sig,
            "+-1s Poisson (counting stats)",
            "rgba(44,95,179,0.16)",
            row=1,
            col=1,
        )
        add_band(
            fig,
            m.t,
            _med(m, "QBER_total", m["QBER_total"]),
            sig / 2.0,
            "+-1s Poisson",
            "rgba(44,95,179,0.16)",
            row=2,
            col=1,
            showlegend=False,
        )
    for col, name in [("visibility", "total"), ("vis_HV", "H/V"), ("vis_DA", "D/A")]:
        raw_plus_median(fig, m.t, m[col], name, PALETTE[name], row=1, col=1, median=_med(m, col, m[col]))
    fig.add_hline(
        y=2**-0.5,
        line_dash="dash",
        line_color="black",
        row=1,
        col=1,
        annotation_text="CHSH/Bell  V=1/sqrt(2)~0.707",
        annotation_font_size=9,
        annotation_position="top left",
    )
    fig.add_hline(
        y=1 / 3,
        line_dash="dot",
        line_color="gray",
        row=1,
        col=1,
        annotation_text="separability  V=1/3",
        annotation_font_size=9,
        annotation_position="bottom left",
    )
    for col, name in [("QBER_total", "total"), ("QBER_HV", "H/V"), ("QBER_DA", "D/A")]:
        raw_plus_median(fig, m.t, m[col], name, PALETTE[name], row=2, col=1, median=_med(m, col, m[col]))
    fig.add_hline(
        y=0.5,
        line_dash="dash",
        line_color="black",
        row=2,
        col=1,
        annotation_text="random 50%",
        annotation_font_size=9,
        annotation_position="top left",
    )
    fig.add_hline(
        y=0.11,
        line_dash="dot",
        line_color="#2f8f3e",
        row=2,
        col=1,
        annotation_text="QKD one-way security ~11%",
        annotation_font_size=9,
        annotation_position="bottom left",
    )
    add_optimizer_shading(fig, m)
    fig.update_yaxes(title_text="visibility", row=1, col=1, range=[-1, 1])
    fig.update_yaxes(title_text="QBER", row=2, col=1, range=[0, 1])
    fig.update_xaxes(title_text="time (Europe/Ljubljana)", row=2, col=1)
    fig.update_layout(
        height=640,
        template="plotly_white",
        margin={"t": 120, "b": 110},
        title={
            "text": "Headline QKD - replay of recorded LJ-Drnovo data (2026-06-19 to 24, Europe/Ljubljana; not live)",
            "y": 0.98,
            "yanchor": "top",
        },
        legend={"orientation": "h", "y": -0.16},
    )
    # red caveat sits between the main title and the subplot title (top margin gives it room)
    fig.add_annotation(
        xref="paper",
        yref="paper",
        x=0,
        y=1.10,
        showarrow=False,
        font={"size": 10, "color": "#a33"},
        align="left",
        text="Recorded & delay-biased; NOT accidental-subtracted. "
        "Grey shading = optimizer actively changing EPC voltages (a search, not passive).",
    )
    return fig


def fig_source(m):
    """Total coincidence rate + per-channel singles rate."""
    S = channel_singles(m)
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.07,
        subplot_titles=("Total coincidence rate (cps)", "Per-channel singles rate (cps, log)"),
    )
    raw_plus_median(
        fig,
        m.t,
        m.coinc_rate,
        "coinc rate",
        "#7d3cb5",
        row=1,
        col=1,
        median=_med(m, "coinc_rate", m.coinc_rate),
    )
    for c in S.columns:
        gl(fig, m.t, S[c], c, row=2, col=1, width=0.8)
    add_optimizer_shading(fig, m)
    fig.update_yaxes(title_text="cps", row=1, col=1)
    fig.update_yaxes(title_text="cps", type="log", row=2, col=1)
    fig.update_xaxes(title_text="time (Europe/Ljubljana)", row=2, col=1)
    fig.update_layout(
        height=580,
        template="plotly_white",
        margin={"t": 70, "b": 130},
        title="Source / link health (grey = optimizer active)",
        legend={"orientation": "h", "y": -0.26, "yanchor": "top"},
    )
    return fig


def fig_stability(m, v):
    """EPC voltage drift (stepped, gapped) + sync health."""
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.07,
        subplot_titles=(
            "EPC voltage drift (V) - stepped; gaps = optimizer off",
            "Sync: clock skew (ppm) & shared markers",
        ),
        specs=[[{}], [{"secondary_y": True}]],
    )
    for i in range(8):
        gl(
            fig,
            v.t,
            v[f"V{i}"],
            f"{'Alice' if i < 4 else 'Bob'} DAC{i % 4}",
            row=1,
            col=1,
            width=0.9,
            shape="hv",
        )
    gl(
        fig,
        m.t,
        m.sync_skew_ppm_mean,
        "skew ppm",
        row=2,
        col=1,
        width=1,
        color="#c0392b",
        secondary_y=False,
    )
    gl(
        fig,
        m.t,
        m.sync_common_markers,
        "shared markers",
        row=2,
        col=1,
        width=0.6,
        color="#888",
        secondary_y=True,
    )
    fig.update_yaxes(title_text="V", row=1, col=1)
    fig.update_yaxes(title_text="ppm", row=2, col=1, secondary_y=False)
    fig.update_yaxes(title_text="markers", row=2, col=1, secondary_y=True)
    fig.update_xaxes(title_text="time (Europe/Ljubljana)", row=2, col=1)
    fig.update_layout(
        height=640,
        template="plotly_white",
        title="Stability & drift",
        margin={"t": 70, "b": 160},
        legend={"orientation": "h", "y": -0.22, "yanchor": "top"},
    )
    return fig


def fig_diagnostics(m):
    """Correlated-sum vs error-sum coincidences + per-label delays."""
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.07,
        subplot_titles=(
            "Coincidence rate: correlated vs error (cps) - normalised, exposure changed 10->20 s",
            "Per-label best delay (ps) - real ~+10 ns step at 06-29 (equipment change)",
        ),
    )
    raw_plus_median(
        fig,
        m.t,
        m.corr_rate,
        "correlated (HH+VV+DD+AA)",
        "#2f8f3e",
        row=1,
        col=1,
        median=_med(m, "corr_rate", m.corr_rate),
    )
    raw_plus_median(
        fig,
        m.t,
        m.err_rate,
        "error (HV+VH+DA+AD)",
        "#c0392b",
        row=1,
        col=1,
        median=_med(m, "err_rate", m.err_rate),
    )
    for name in LABELS:
        gl(
            fig,
            m.t,
            m[f"delay_{name}_ps"],
            name,
            row=2,
            col=1,
            width=0.6,
            dash="solid" if name in CORRELATED else "dot",
        )
    add_optimizer_shading(fig, m)
    fig.update_yaxes(title_text="cps", row=1, col=1)
    fig.update_yaxes(title_text="delay (ps)", row=2, col=1)
    fig.update_xaxes(title_text="time (Europe/Ljubljana)", row=2, col=1)
    fig.update_layout(
        height=660,
        template="plotly_white",
        margin={"t": 70, "b": 160},
        title="Diagnostics (rates; grey = optimizer active; not accidental-subtracted)",
        legend={"orientation": "h", "y": -0.22, "yanchor": "top"},
    )
    return fig


def fig_security(m):
    """CHSH S-value + THEORETICAL key rate. Both show N/A (gaps) where unavailable:
    CHSH only from 2026-06-29 (valid), key rate only where a positive secure rate is possible."""
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.09,
        subplot_titles=(
            "CHSH |S| - Bell violation if > 2 (valid from 2026-06-29; N/A before)",
            "THEORETICAL secret-key rate (asymptotic BBM92; NOT a measured key; N/A when QBER too high)",
        ),
    )
    raw_plus_median(fig, m.t, m.chsh_s, "CHSH |S|", "#7d3cb5", row=1, col=1, median=_med(m, "chsh_s", m.chsh_s))
    fig.add_hline(
        y=2.0,
        line_dash="dash",
        line_color="black",
        row=1,
        col=1,
        annotation_text="classical bound S=2",
        annotation_font_size=9,
        annotation_position="bottom left",
    )
    fig.add_hline(
        y=2 * 2**0.5,
        line_dash="dot",
        line_color="gray",
        row=1,
        col=1,
        annotation_text="Tsirelson 2sqrt2~2.83",
        annotation_font_size=9,
        annotation_position="top left",
    )
    # key rate is positive on only a handful of rows -> markers, not a line (a line is invisible)
    fig.add_trace(
        go.Scattergl(
            x=pd.to_datetime(np.asarray(m.t)),
            y=np.asarray(m.key_rate_theo, dtype=float),
            mode="markers",
            name="key rate (theoretical)",
            marker={"size": 7, "color": "#2f8f3e", "line": {"width": 0.5, "color": "#1b5e20"}},
            hovertemplate="%{y:.2f} bit/s<extra></extra>",
        ),
        row=2,
        col=1,
    )
    add_optimizer_shading(fig, m)
    fig.update_yaxes(title_text="|S|", row=1, col=1, range=[0, 3])
    fig.update_yaxes(title_text="bits/s (theoretical)", row=2, col=1)
    fig.update_xaxes(title_text="time (Europe/Ljubljana)", row=2, col=1)
    fig.update_layout(
        height=620,
        template="plotly_white",
        margin={"t": 120, "b": 100},
        title="Security metrics - replay of recorded LJ-Drnovo data (not live)",
        legend={"orientation": "h", "y": -0.16},
    )
    fig.add_annotation(
        xref="paper",
        yref="paper",
        x=0,
        y=1.10,
        showarrow=False,
        font={"size": 10, "color": "#a33"},
        align="left",
        text="CHSH valid only from 2026-06-29; key rate is a THEORETICAL asymptotic estimate "
        "(1-(1+f)*h2(QBER)) x coincidence rate, not a measured/distilled key.",
    )
    return fig
