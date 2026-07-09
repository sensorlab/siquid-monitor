<!-- GENERATED from internal/data.md — do not edit; edit the internal source. -->

## Recorded dataset (overview)

The dashboard replays a recorded run of a **long-distance entanglement-distribution
experiment** (an entanglement-based QKD test link). Two independent time taggers record
photon arrival times; these are clock-aligned, photon coincidences are found, and from
them **visibility** and **QBER** are computed. Only processed CSV/JSON metrics are used
here — no raw timetags.

Across ~29,000 measurements (2026-06-19 to 07-08), most of the record is the **commissioning
phase** — Adrian's team getting photons through the dark fibers at all and tuning the sender/
receiver hardware, not a working link performing poorly. In the past week or so, **the team
achieved a breakthrough**: from **2026-06-29 onward** (continuing, with more violations, through
07-08) a subset of measurements crossed the entanglement threshold and produced a genuine **CHSH
Bell violation** (S > 2) over the long-distance link. It's a realistic R&D-stage dataset — a long
commissioning stretch followed by the first working results — rather than a clean always-on
showcase. A second, metropolitan link (UVTP-MDP, 2025-11) is also available and is consistently a
**strong positive result** (median QBER under 8%, sustained Bell violation) — see below.
