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
from .data import channel_singles, load_repo_dataset, to_local

TICK_MS = 1000  # > worst-case render (~0.8 s at full data) so ticks never back up
TICK_S = TICK_MS / 1000.0
SPEEDS = [100, 500, 2000, 10000, 50000, 100000]  # x real-time (dataset is ~13 days)
DEFAULT_SPEED = 2000

# --- load + precompute once --------------------------------------------------
DS = load_repo_dataset()  # DEFAULT_DATA_DIR = external/…/Data
M = DS.measurements
V = DS.voltages
M["singles_total"] = channel_singles(M).sum(axis=1)  # cps, precomputed (cheap per-tick KPI)
F.precompute_medians(M)  # precompute medians -> near-constant per-tick render
F.precompute_poisson(M)  # precompute Poisson 1σ band for the headline
T0 = float(M.timestamp.min())
T1 = float(M.timestamp.max())
SPAN = T1 - T0
T0_DT = to_local(T0)  # Europe/Ljubljana, matches the measurements' `t` column
T1_DT = to_local(T1)


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


def _build_panel(tab: str, m, now: float):
    """Build the figure for the active tab from the visible slice `m`. Only the active
    panel is built per tick, so render cost stays ~constant regardless of how many panels exist."""
    if tab == "source":
        return F.fig_source(m)
    if tab == "stability":
        return F.fig_stability(m, V[V.timestamp <= now])  # reveal voltages left-to-right too
    if tab == "diagnostics":
        return F.fig_diagnostics(m)
    if tab == "security":
        return F.fig_security(m)
    return F.fig_headline(m)


def _render(elapsed: float, tab: str = "headline"):
    now = T0 + elapsed
    vis = M[M.timestamp <= now]
    if vis.empty:
        vis = M.iloc[:1]
    last = vis.iloc[-1]
    vcolor = "#2f8f3e" if last.visibility >= 1 / 3 else "#c0392b"  # entangled vs separable
    # CHSH + theoretical key rate show "N/A" when the value is masked/undefined for this row.
    s = last.get("chsh_s", float("nan"))
    s_txt = f"{s:.2f}" if pd.notna(s) else "N/A"
    s_color = "#2f8f3e" if pd.notna(s) and s > 2 else "#222"  # green when Bell-violating
    kr = last.get("key_rate_theo", float("nan"))
    kr_txt = f"{kr:.1f} bit/s" if pd.notna(kr) else "N/A"
    kpis = [
        _kpi("visibility", f"{last.visibility:+.3f}", vcolor),
        _kpi("QBER", f"{last.QBER_total * 100:.1f}%"),
        _kpi("coincidence rate", _fmt_cps(last.coinc_rate)),
        _kpi("total singles", _fmt_cps(last.singles_total)),
        _kpi("CHSH |S|", s_txt, s_color),
        _kpi("key rate (theo.)", kr_txt),
        _kpi("virtual time (Ljubljana)", to_local(now).strftime("%m-%d %H:%M:%S")),
    ]
    fig = _build_panel(tab, vis, now)
    fig.update_xaxes(range=[T0_DT, T1_DT])  # fixed full span -> playback reveals left-to-right
    pct = 100.0 * elapsed / SPAN if SPAN else 100.0
    return fig, kpis, f"{pct:5.1f}% of {SPAN / 3600:.1f} h"


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
            f"Source: {DS.name}.  Values are recorded, delay-biased, not accidental-subtracted.",
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
    Output("clock", "data"),
    Output("panel", "figure"),
    Output("kpis", "children"),
    Output("progress", "children"),
    Input("tick", "n_intervals"),
    Input("reset", "n_clicks"),
    Input("skip-end", "n_clicks"),
    Input("tab", "value"),
    State("clock", "data"),
    State("speed", "value"),
)
def advance(_n, _rs, _se, tab, clock, speed):
    elapsed = float((clock or {}).get("elapsed", 0.0))
    trig = ctx.triggered_id
    if trig == "reset":
        elapsed = 0.0
    elif trig == "skip-end":  # jump straight to the full recording
        elapsed = SPAN
    elif trig == "tick":
        if elapsed >= SPAN:  # already at the end -> stop re-rendering
            raise PreventUpdate
        elapsed = min(elapsed + TICK_S * float(speed), SPAN)
    # a tab switch (trig == "tab") just re-renders the new panel at the current clock
    fig, kpis, progress = _render(elapsed, tab)
    return {"elapsed": elapsed}, fig, kpis, progress


def main():
    """Entry point (console script `siquid-monitor`). Host/port overridable via $HOST/$PORT
    (the Docker image sets HOST=0.0.0.0)."""
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8050"))
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
