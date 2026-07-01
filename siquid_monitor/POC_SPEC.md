# PoC spec — playback-mock dashboard (gate 3)

Minimal, bounded build. Goal: prove the **whole loop** (load → playback clock → live figure + KPIs)
end-to-end before adding the other panels. Design rationale: `../docs/figures.md`.

## Scope (PoC only)
- **In:** KPI tiles + the **Headline QKD** figure + playback controls, single process, in-memory.
- **Out (later):** the other 3 panels (source/stability/diagnostics — builders already exist),
  Poisson bands, down-sampling, source-switching UI, multi-page layout.

## Files (`siquid_monitor/`)
- `data.py` — `load_repo_dataset()` → `Dataset(measurements, voltages)` (done).
- `figures.py` — `fig_headline(m)` + helpers (done; reused as-is).
- `app.py` — **new**: the Dash app (this spec).
- `../pyproject.toml` — dependencies (base + `dev`/`notebook` extras).

## Architecture
Single `Dash` app. State in a `dcc.Store` (no globals), advanced by a `dcc.Interval`:
- `dcc.Store(id="clock")` holds `{elapsed_s, playing}` (recorded-seconds elapsed since `t0`).
- `dcc.Interval(id="tick", interval=500 ms)`.
- One callback on `tick` (+ control inputs) → updates `clock`, then outputs: the Headline `Graph`,
  the KPI tiles, and a virtual-clock label.
- Each tick: if playing, `elapsed += 0.5 * speed`, clamped to `[0, t1-t0]` (stop at end; Reset zeroes it).
- Visible slice = `m[m.timestamp <= t0 + elapsed]`; figure = `F.fig_headline(slice)`; KPIs = slice's last row.

## Controls
- **Play/Pause** button.
- **Speed**: dropdown {100×, 500×, 2000× (default), 10000×}. (2000× ⇒ 117.6 h replays in ~3.5 min.)
- **Reset** button (elapsed → 0).
- **Virtual-clock** readout (UTC) + a progress indicator (% of span).

## KPI tiles (latest visible row)
latest **visibility**, **QBER_total**, **coincidence rate** (cps), **total singles rate** (Alice+Bob cps),
**virtual time** (UTC). Tiles state "recorded / delay-biased" once (not per tile).

## Honesty (inherited from `figures.py`, no extra work)
Correct thresholds (CHSH 1/√2, separability 1/3, QKD ~11%), gaps broken, gap-aware median,
optimizer-active shading, UTC + "replay, not live" + caveat annotation.

## Run
```
.venv/bin/pip install -e ".[dev]"                        # once (deps in pyproject.toml)
.venv/bin/python -m siquid_monitor.app                     # serves http://127.0.0.1:8050
```

## Acceptance criteria
- App boots and serves the Headline panel with KPI tiles.
- Play advances the virtual clock; figure + KPIs update live; **stops cleanly at end**.
- Pause halts; Reset returns to start; speed change takes effect.
- Headline shows the correct reference lines, broken gaps, and optimizer shading (i.e. it's the
  honest figure, not a re-implementation).

## Notes
- Performance: ~10k points × 12 traces via `Scattergl`; if a tick feels heavy, throttle `Interval`
  or down-sample (TODO D, min/max-preserving) — not expected at PoC scale.
- The notebook `Player` class is the reference for the clock math; the app reimplements it over `dcc.Store`.
