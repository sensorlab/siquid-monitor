<!-- GENERATED from internal/data.md — do not edit; edit the internal source. -->

## Recorded dataset (overview)

The dashboard replays a recorded run of a **long-distance entanglement-distribution
experiment** (an entanglement-based QKD test link). Two independent time taggers record
photon arrival times; these are clock-aligned, photon coincidences are found, and from
them **visibility** and **QBER** are computed. Only processed CSV/JSON metrics are used
here — no raw timetags.

Across ~29,000 measurements (2026-06-19 to 07-08) the link was **mostly below threshold but
worked intermittently**: near-random for most of the window, then on **2026-06-29 to 07-01** (and
more briefly afterwards) a subset of measurements reached the entanglement threshold and a genuine
**CHSH Bell violation** (S > 2). It's a realistic monitoring dataset — long stretches of noise with
occasional working periods — rather than a clean always-on showcase. A second, metropolitan link
(UVTP-MDP, 2025-11) is also available and is consistently a **strong positive result** (median QBER
under 8%, sustained Bell violation) — see below.
