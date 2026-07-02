# Plan A Monitor

Media monitor for AIFP's **"AI 2040: Plan A"** — watches X, Hacker News, Google News, Substack, and Reddit; alerts Slack when something is noteworthy; feeds a [live dashboard](https://nicole-e-s.github.io/plan-a-monitor/dashboard.html).

- **[USER-MANUAL.md](USER-MANUAL.md)** — for the comms team: reading alerts, tuning thresholds, FAQ.
- **[DESIGN.md](DESIGN.md)** — architecture, detectors, and the decision log.

Runs on GitHub Actions every 5 minutes (`.github/workflows/monitor.yml` → `planamonitor.py` + `config.cloud.yaml`). All credentials — and the watchlist — live in Actions secrets, never in this repo.
