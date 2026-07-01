# siquid_monitor — SiQUID playback dashboard (PoC)

Replays recorded QKD metrics as an **honest, non-live** dashboard. It does not recompute physics;
it shows logged values with the caveats baked in (delay-biased, not accidental-subtracted,
non-entangling link). See `../docs/figures.md` (design), `../docs/data.md` (data), `POC_SPEC.md` (this PoC).

Data source: the partner clone's CSVs under `../external/long-distance-entanglement/Data`
(centralized in `data.DEFAULT_DATA_DIR`).

## Run
```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"             # deps in ../pyproject.toml
.venv/bin/python -m siquid_monitor.app          # -> http://127.0.0.1:8050
```
See the root `README.md` for the Docker workflow.

## Layout
- `data.py` — `load_repo_dataset()` → `Dataset(measurements, voltages)`; source-agnostic
  (add a `load_*` for UVTP-MDP later; figures unchanged).
- `figures.py` — Plotly builders (`fig_headline/source/stability/diagnostics`) + helpers
  (`break_gaps`, gap-aware `rmed`, `gl`, `raw_plus_median`, `add_optimizer_shading`).
- `app.py` — Dash app: KPI tiles + Headline figure + playback (`dcc.Interval` clock,
  play/pause, speed {100–10000×, default 2000×}, reset).

## Status
KPI tiles + playback + **all four panels** as tabs (Headline / Source / Stability / Diagnostics);
only the active tab is rebuilt per tick. Headline carries a ±1σ Poisson (counting-statistics) band.
Remaining (tracked in the internal roadmap): accidental-subtracted view + key-rate data, down-sampling
(only if needed), and tuning `GAP_S` / median window / speed with stakeholders.
