# Plan A Monitor

Watches public reaction to AIFP's "AI 2040: Plan A" across X, Hacker News, Google News, Substack, and Reddit. Alerts Slack when something needs attention and feeds a [live dashboard](https://nicole-e-s.github.io/plan-a-monitor/dashboard.html).

- [USER-MANUAL.md](USER-MANUAL.md) — for the comms team: reading alerts, tuning thresholds, FAQ
- [DESIGN.md](DESIGN.md) — how it works and why it's built this way

Runs on GitHub Actions every 5 minutes (`planamonitor.py` + `config.cloud.yaml`). Credentials and the watchlist live in Actions secrets, not in this repo.
