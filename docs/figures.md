<!-- GENERATED from internal/figures.md — do not edit; edit the internal source. -->

## Figure design principles

Guiding principle: **replay recorded data as truthfully as possible** — the dashboard shows
logged values and never recomputes or fabricates physics. Concretely: gaps in time are never
bridged; noisy metrics show a faint raw trace plus a descriptive rolling median (not a fit);
a ±1σ counting-statistics band is drawn; and reference thresholds (Bell/CHSH, separability,
QKD security) are shown so distance from "useful" is explicit.

The dashboard has five panels: **Headline** (visibility + QBER), **Source / link health**
(coincidence + singles rates), **Stability & drift** (polarization-control voltages + sync
health), **Diagnostics** (correlated vs error coincidence *rates* + per-label delays), and
**Security** (CHSH |S|, a **theoretical** asymptotic secret-key rate, and an **exact finite-key**
rate). Metrics are shown only where available — e.g. CHSH is blank before it was validly recorded,
the theoretical key rate is blank wherever a positive secure rate isn't achievable, and the
finite-key rate is blank across this record (the accumulated statistics never reach the level a
provably-secure key would require).
