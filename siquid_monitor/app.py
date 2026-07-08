"""Playback-mock dashboard (PoC) — see POC_SPEC.md.

Single-process Dash app that replays the recorded LJ-Drnovo metrics through a virtual
clock. KPI tiles + the Headline QKD figure + playback controls (play/pause, speed, reset).

Callback design (avoids the Interval racing the controls):
- play/pause toggles the Interval's `disabled` (real pause; ticks stop).
- speed is read as State, so changing it never triggers/interrupts the render.
- one render callback advances the clock on tick / zeroes it on reset, and stops cleanly
  at the end via PreventUpdate.

Run:  .venv/bin/python -m siquid_monitor.app   ->  http://127.0.0.1:8050
"""

from __future__ import annotations

import os

import pandas as pd
from dash import Dash, Input, Output, State, ctx, dcc, html
from dash.exceptions import PreventUpdate

from . import figures as F
from .data import channel_singles, load_mdpuvtp_dataset, load_repo_dataset, to_local

TICK_MS = 1000  # > worst-case render (~0.8 s at full data) so ticks never back up
TICK_S = TICK_MS / 1000.0
SPEEDS = [100, 500, 2000, 10000, 50000, 100000]  # x real-time (LJ-Drnovo dataset is ~19 days)
DEFAULT_SPEED = 2000
DEFAULT_DATASET = "LJ-Drnovo"


class _Loaded:
    """One preloaded, precomputed dataset (figures/KPIs are read straight from these fields)."""

    def __init__(self, ds):
        self.ds = ds
        self.m = ds.measurements
        self.v = ds.voltages
        # cps; min_count=1 -> NaN (not 0) when a dataset logs no per-channel singles at all
        # (e.g. UVTP-MDP, which has no overlap_duration_sec), so the KPI honestly shows N/A.
        self.m["singles_total"] = channel_singles(self.m).sum(axis=1, min_count=1)
        F.precompute_medians(self.m)
        F.precompute_poisson(self.m)
        self.t0 = float(self.m.timestamp.min())
        self.t1 = float(self.m.timestamp.max())
        self.span = self.t1 - self.t0
        # floor to seconds: epoch-float -> Timestamp carries nonzero nanoseconds, which Plotly warns
        # about when serialising the fixed x-axis range (the `t` column is likewise floored, to ms).
        self.t0_dt = to_local(self.t0).floor("s")
        self.t1_dt = to_local(self.t1).floor("s")


# --- load + precompute once, for every selectable dataset --------------------
DATASETS = {
    "LJ-Drnovo": _Loaded(load_repo_dataset()),  # DEFAULT_DATA_DIR = external/…/Data
    "UVTP-MDP": _Loaded(load_mdpuvtp_dataset()),
}


def _fmt_cps(x: float) -> str:
    if x >= 1e6:
        return f"{x / 1e6:.2f} Mcps"
    if x >= 1e3:
        return f"{x / 1e3:.1f} kcps"
    return f"{x:.0f} cps"


def _kpi(label: str, value: str, color: str = "#222") -> html.Div:
    return html.Div(
        [
            html.Div(label, style={"fontSize": "12px", "color": "#666", "textTransform": "uppercase"}),
            html.Div(value, style={"fontSize": "26px", "fontWeight": "600", "color": color}),
        ],
        style={
            "padding": "10px 16px",
            "background": "#f6f7f9",
            "borderRadius": "8px",
            "minWidth": "150px",
            "textAlign": "center",
        },
    )


def _build_panel(tab: str, m, v, now: float):
    """Build the figure for the active tab from the visible slice `m`. Only the active
    panel is built per tick, so render cost stays ~constant regardless of how many panels exist."""
    if tab == "source":
        return F.fig_source(m)
    if tab == "stability":
        return F.fig_stability(m, v[v.timestamp <= now])  # reveal voltages left-to-right too
    if tab == "diagnostics":
        return F.fig_diagnostics(m)
    if tab == "security":
        return F.fig_security(m)
    return F.fig_headline(m)


def _render_panel(ds_key: str, elapsed: float, tab: str):
    """Build the active panel at the current clock. `uirevision` keeps any user zoom across
    playback re-renders (but resets on a dataset switch, since the time domain changes)."""
    d = DATASETS[ds_key]
    now = d.t0 + elapsed
    vis = d.m[d.m.timestamp <= now]
    if vis.empty:
        vis = d.m.iloc[:1]
    fig = _build_panel(tab, vis, d.v, now)
    fig.update_xaxes(range=[d.t0_dt, d.t1_dt])  # fixed full span -> playback reveals left-to-right
    fig.update_layout(uirevision=ds_key)
    pct = 100.0 * elapsed / d.span if d.span else 100.0
    return fig, f"{pct:5.1f}% of {d.span / 3600:.1f} h"


def _zoom_range(relayout):
    """Parse a Plotly relayoutData dict into (lo, hi) local Timestamps, or None (autorange / full)."""
    if not relayout or "xaxis.autorange" in relayout:
        return None
    r0, r1 = relayout.get("xaxis.range[0]"), relayout.get("xaxis.range[1]")
    if r0 is None and isinstance(relayout.get("xaxis.range"), (list, tuple)):
        r0, r1 = relayout["xaxis.range"]
    if r0 is None or r1 is None:
        return None
    try:
        return pd.to_datetime(r0), pd.to_datetime(r1)
    except (ValueError, TypeError):
        return None


def _median(w, col):
    if col not in w.columns:
        return float("nan")
    v = w[col].dropna()
    return float(v.median()) if len(v) else float("nan")


def _build_kpis(ds_key: str, elapsed: float, relayout, from_zoom: bool):
    """KPI tiles = the MEDIAN of each metric over the visible window (not the latest single row).
    Window = the user's zoom if they zoomed, else all data revealed so far (up to the virtual clock)."""
    d = DATASETS[ds_key]
    now = d.t0 + elapsed
    now_local = to_local(now).floor("s")
    zoom = _zoom_range(relayout) if from_zoom else None
    lo, hi, zoomed = (zoom[0], zoom[1], True) if zoom else (d.t0_dt, now_local, False)
    w = d.m[(d.m["t"] >= lo) & (d.m["t"] <= hi) & (d.m["timestamp"] <= now)]
    n = len(w)

    vis, qber, qber_net = _median(w, "visibility"), _median(w, "QBER_total"), _median(w, "QBER_net_total")
    coinc, singles = _median(w, "coinc_rate"), _median(w, "singles_total")
    s, kr, krf = _median(w, "chsh_s"), _median(w, "key_rate_theo"), _median(w, "key_rate_finite")

    vcolor = "#2f8f3e" if pd.notna(vis) and vis >= 1 / 3 else "#c0392b"  # entangled vs separable
    qn_color = "#2f8f3e" if pd.notna(qber_net) and qber_net < 0.11 else "#222"  # below ~11% QKD bound
    s_color = "#2f8f3e" if pd.notna(s) and s > 2 else "#222"  # Bell-violating
    pct = lambda q: f"{q * 100:.1f}%" if pd.notna(q) else "N/A"  # noqa: E731

    caption = html.Div(
        f"metrics below = median over {'zoomed window' if zoomed else 'all revealed data'}: "
        f"{lo:%m-%d %H:%M} → {hi:%m-%d %H:%M}  ·  N = {n} measurements"
        f"{'' if zoomed else '  (zoom the plot to focus a window)'}",
        style={"flexBasis": "100%", "fontSize": "12px", "color": "#555", "marginBottom": "2px"},
    )
    tiles = [
        _kpi("visibility", f"{vis:+.3f}" if pd.notna(vis) else "N/A", vcolor),
        _kpi("QBER", pct(qber)),
        _kpi("QBER (accid.-sub.)", pct(qber_net), qn_color),
        _kpi("coincidence rate", _fmt_cps(coinc) if pd.notna(coinc) else "N/A"),
        _kpi("total singles", _fmt_cps(singles) if pd.notna(singles) else "N/A"),
        _kpi("CHSH |S|", f"{s:.2f}" if pd.notna(s) else "N/A", s_color),
        _kpi("key rate (theo.)", f"{kr:.1f} bit/s" if pd.notna(kr) else "N/A"),
        _kpi("finite-key (exact)", f"{krf:.3f} bit/s" if pd.notna(krf) else "N/A"),
        _kpi("virtual time (Ljubljana)", now_local.strftime("%m-%d %H:%M:%S")),
    ]
    return [caption, *tiles]


# --- app ---------------------------------------------------------------------
app = Dash(__name__, title="SiQUID monitor (replay)")
server = app.server

app.layout = html.Div(
    style={
        "fontFamily": "system-ui, sans-serif",
        "maxWidth": "1200px",
        "margin": "0 auto",
        "padding": "16px",
    },
    children=[
        html.H2("SiQUID QKD monitor — replay of recorded data (not live)"),
        html.Div(
            id="source-info",
            style={"color": "#a33", "fontSize": "13px", "marginBottom": "10px"},
        ),
        html.Div(
            style={
                "display": "flex",
                "gap": "12px",
                "alignItems": "center",
                "flexWrap": "wrap",
                "marginBottom": "12px",
            },
            children=[
                html.Label("Dataset:"),
                dcc.Dropdown(
                    id="dataset-select",
                    value=DEFAULT_DATASET,
                    clearable=False,
                    options=[{"label": k, "value": k} for k in DATASETS],
                    style={"width": "160px"},
                ),
                html.Button(
                    "⏸ Pause",
                    id="play-pause",
                    n_clicks=0,
                    style={"fontSize": "15px", "padding": "6px 14px"},
                ),
                html.Button(
                    "⟲ Reset",
                    id="reset",
                    n_clicks=0,
                    style={"fontSize": "15px", "padding": "6px 14px"},
                ),
                html.Button(
                    "⏭ Show all",
                    id="skip-end",
                    n_clicks=0,
                    title="Jump to the end — show the whole recording at once",
                    style={"fontSize": "15px", "padding": "6px 14px"},
                ),
                html.Label("Speed:", style={"marginLeft": "8px"}),
                dcc.Dropdown(
                    id="speed",
                    value=DEFAULT_SPEED,
                    clearable=False,
                    options=[{"label": f"{s}×", "value": s} for s in SPEEDS],
                    style={"width": "110px"},
                ),
                html.Div(id="progress", style={"color": "#555", "marginLeft": "8px"}),
            ],
        ),
        html.Div(
            id="kpis",
            style={"display": "flex", "gap": "12px", "flexWrap": "wrap", "marginBottom": "12px"},
        ),
        dcc.Tabs(
            id="tab",
            value="headline",
            children=[
                dcc.Tab(label="Headline QKD", value="headline"),
                dcc.Tab(label="Source / link health", value="source"),
                dcc.Tab(label="Stability & drift", value="stability"),
                dcc.Tab(label="Diagnostics", value="diagnostics"),
                dcc.Tab(label="Security (CHSH + key rate)", value="security"),
            ],
        ),
        dcc.Graph(id="panel"),
        dcc.Store(id="clock", data={"elapsed": 0.0}),
        dcc.Interval(id="tick", interval=TICK_MS, n_intervals=0, disabled=False),
    ],
)


@app.callback(
    Output("tick", "disabled"),
    Output("play-pause", "children"),
    Input("play-pause", "n_clicks"),
    State("tick", "disabled"),
    prevent_initial_call=True,
)
def toggle_play(_n, disabled):
    disabled = not disabled
    return disabled, ("▶ Play" if disabled else "⏸ Pause")


@app.callback(
    Output("source-info", "children"),
    Input("dataset-select", "value"),
)
def update_source_info(ds_key):
    d = DATASETS[ds_key]
    return (
        f"Source: {d.ds.name}.  Values are recorded and delay-biased; raw traces are NOT "
        "accidental-subtracted (an indicative accidental-subtracted overlay/metric is shown where "
        "available). KPIs are medians over the visible window — zoom the plot to focus a timeframe."
    )


@app.callback(
    Output("clock", "data"),
    Output("panel", "figure"),
    Output("progress", "children"),
    Input("tick", "n_intervals"),
    Input("reset", "n_clicks"),
    Input("skip-end", "n_clicks"),
    Input("tab", "value"),
    Input("dataset-select", "value"),
    State("clock", "data"),
    State("speed", "value"),
)
def advance(_n, _rs, _se, tab, ds_key, clock, speed):
    clock = clock or {}
    elapsed = float(clock.get("elapsed", 0.0))
    d = DATASETS[ds_key]
    trig = ctx.triggered_id
    if trig == "reset" or trig == "dataset-select" or clock.get("dataset") != ds_key:
        elapsed = 0.0  # switching dataset changes the time domain -> start it fresh
    elif trig == "skip-end":  # jump straight to the full recording
        elapsed = d.span
    elif trig == "tick":
        if elapsed >= d.span:  # already at the end -> stop re-rendering
            raise PreventUpdate
        elapsed = min(elapsed + TICK_S * float(speed), d.span)
    # a tab switch (trig == "tab") just re-renders the new panel at the current clock
    fig, progress = _render_panel(ds_key, elapsed, tab)
    return {"elapsed": elapsed, "dataset": ds_key}, fig, progress


@app.callback(
    Output("kpis", "children"),
    Input("panel", "relayoutData"),  # user zoom/pan -> metrics over that window
    Input("clock", "data"),  # playback tick/reset -> metrics over all revealed data
    Input("dataset-select", "value"),
    prevent_initial_call=False,
)
def update_kpis(relayout, clock, ds_key):
    elapsed = float((clock or {}).get("elapsed", 0.0))
    # Only honour the zoom when the zoom itself is what changed; a clock tick or dataset switch
    # falls back to the full revealed window (and the panel re-render resets the view).
    from_zoom = ctx.triggered_id == "panel"
    return _build_kpis(ds_key, elapsed, relayout, from_zoom)


def main():
    """Entry point (console script `siquid-monitor`). Host/port overridable via $HOST/$PORT
    (the Docker image sets HOST=0.0.0.0)."""
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8050"))
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
