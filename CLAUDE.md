# CLAUDE.md

Guidance for Claude Code (claude.ai/code) working in the **SiQUID visualization** project.

## What this repo is

A visualization / monitoring proof-of-concept (Deliverable **D5.3**) for a long-distance,
entanglement-based QKD demonstration. It **replays recorded metrics** as a dashboard — it does
not drive hardware and does not recompute physics.

## Read these first (context)

The full working docs are the source of truth in **`internal/`** (gitignored — local only).
Read the relevant ones before substantial work:

- `internal/data.md` — the recorded data: provenance, columns, units, metric formulas, caveats.
- `internal/monitoring.md` — requirements/intent (D5.3), ownership, constraints.
- `internal/figures.md` — dashboard figure-design decisions & rationale.
- `internal/todo.md` — open items + questions for project partners.
- `internal/partner-code.md` — architecture of the partner acquisition code (under `external/`).

`docs/` holds the **public subset**, generated from `internal/` by `tools/build_public_docs.py`
(`<!-- public:start/end -->` blocks). Do **not** hand-edit `docs/` — edit the `internal/` source.

## Project layout

- `siquid_monitor/` — the dashboard (Dash app + Plotly figure builders); data loading in `data.py`.
- `notebooks/viz_explore.ipynb` — sandbox that imports `siquid_monitor`.
- `external/long-distance-entanglement/` — pristine clone of the partner acquisition repo. **Never modify it.**
- `resources/` — reference material (e.g. the D4.4 deliverable `.docx`).
- `internal/` (full docs, gitignored) · `docs/` (generated public subset) · `tools/` (the generator).

## Running

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m siquid_monitor.app     # or: siquid-monitor   -> http://127.0.0.1:8050
```

Recorded data is read from `external/long-distance-entanglement/Data` (override via `$SIQUID_DATA_DIR`).
Docker: `docker compose up --build`.

## Conventions

- Dependencies live in `pyproject.toml` (base + `dev` / `notebook` extras) — there is no `requirements.txt`.
- pre-commit runs nbstripout, ruff-check, ruff-format, and regenerates `docs/` from `internal/`.
- Ruff: line-length 120, target py312 (`[tool.ruff]`).
- Keep **LF** line endings.
- Do **not** edit anything under `external/`; do **not** hand-edit `docs/` (regenerated from `internal/`).
