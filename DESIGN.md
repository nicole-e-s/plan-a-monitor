# Plan A Monitor — Design Doc

*Owned by the AIFP comms/monitoring operator · Updated: 2026-07-02 · Status: monitor live; publication launch expected within days.*
*Decision rationale lives here; day-to-day usage lives in `USER-MANUAL.md`.*

## 1. Purpose & goals

Early-warning system for the public reaction to **"AI 2040: Plan A"** — built to avoid the "AI 2027 problem," where negative discourse wasn't caught early enough to shape a response. Alerts go to Slack only when noteworthy; everything relevant feeds a live dashboard.

**Priorities:** ① influential individual reactions → ② volume spikes → ③ building negative narratives.
**Non-goals:** full analytics suite; pinging on every mention; sub-minute latency (≤5 min is fine).

## 2. Threat model → detector

| Shape | Detector |
|---|---|
| Single influential voice | Watchlist match — alerts at any size |
| Single viral post | Attention bars (views/likes/reposts/followers) |
| Volume spike | Rate vs. rolling baseline |
| Negativity surge | ≥N negative mentions in a window |
| Slow-building narrative | Rolling multi-day narrative totals (3-day sum ≥ 3× the per-window bar, 12 h cooldown) |

## 3. Sources

| Source | Status / notes |
|---|---|
| **X/Twitter** (pay-per-use API, ~$0.005/read) | ✅ Primary. Broad keyword query **+** direct `from:` polling of every watchlist account (catches subtweets). 10-min read window keeps cost low; prepaid credits with a monthly spend cap. X ended new Basic/Pro signups, so pay-per-use is the only option. |
| **Substack / blogs** (RSS) | ✅ One feed per watchlist person. |
| **Hacker News** (Algolia) | ✅ Stories + comments, exact-phrase. |
| **Google News** (RSS) | ✅ Article pickup/syndication; outlet tiers gate alerts. |
| **Reddit** (public search RSS) | ✅ *Partial by necessity*: Reddit closed self-serve API signup (2026). RSS = public **posts only** (no comments), **no engagement counts** (→ can never solo-alert; feeds trends only), rate-limited/best-effort from cloud IPs. Official Data API application submitted; creds auto-enable the full source if approved. |
| Non-English news | Post-launch. |

## 4. Pipeline & models

`fetch → keyword prefilter → dedupe → AI classify → score → aggregate → alert + dashboard`

Cost-asymmetric by design: the AI only sees posts that are *new* and passed a free keyword filter. Classification is tiered — **Haiku 4.5** for the bulk pass, escalating negative/mixed/critique items to **Sonnet 5** (a failed escalation keeps the first-pass labels). Article text is fetched so news/link judgments aren't headline-only. AI spend is a rounding error vs. X API usage — optimized for fidelity.

## 5. Alerting

**Governing rule:** ping the comms responders when we should *respond*; ping `@channel` when we must *devise a response to something negative*. (Responder IDs live in the `SLACK_RESPOND_NOTIFY` secret.)

| Who posted | Negative | Neutral | Positive |
|---|---|---|---|
| Watchlist / big-attention | 🔴 CRITICAL `@channel` | ⚪ ping responders | 🟢 ping responders |
| General crowd (aggregate) | 🟠 WARNING ping responders | ▫️ plaintext | 🟩 plaintext |

Mechanics: colored sidebars per tier with plain-text fallback; alerts batched per 5-min window per tier; silent when empty; per-post dedup with re-alert only on big escalation; daily no-ping digest at 23:00 UTC; `[features X]` matching counts articles that *mention* a watchlist person (news "authors" are outlets).

## 6. Watchlist (private)

**Names, handles, weights, and feed URLs are secrets** (`WATCHLIST_YAML`; local mirror `watchlist.local.yaml`, gitignored) — never in the repo, its history, its logs (feed errors log an index), or dashboard data (watchlist tags stripped from `timeseries.json`). Coverage per person: direct X polling + their blog/Substack feed + featured-by-name article matching. Currently 17 people (5 critical / 12 high).

## 7. Thresholds

- Bars gate **alerts only, never counting** — small posts always feed the dashboard/surge/narrative logic, so miscalibration is low-stakes and tunable live.
- Current per-post bars (set by comms 2026-07-02, ANY one): **≥100k impressions · ≥500 likes · ≥50 reposts · ≥150k followers**. Watchlist bypasses all bars. Substantive critiques get a discounted bar (~10+ interactions) instead of a free pass.
- Tuned by editing `config.cloud.yaml` in the GitHub web UI (step-by-step in `USER-MANUAL.md`). A slider dashboard and Slack commands were considered and rejected — both need a backend + auth for marginal benefit.

## 8. Operations & security

- **Runs on GitHub Actions**, every 5 min, from `nicole-e-s/plan-a-monitor`; state committed back each run. Cron is best-effort — a skipped run is recovered next run (1 h lookback + dedup). Manual "Run workflow" button as launch-day backup.
- **Secrets:** `SLACK_WEBHOOK_URL`, `ANTHROPIC_API_KEY`, `X_BEARER_TOKEN`, `WATCHLIST_YAML`, `SLACK_ERROR_NOTIFY`, `SLACK_RESPOND_NOTIFY`; pending `REDDIT_CLIENT_ID`/`SECRET`. X/Reddit auto-enable when their secrets exist.
- **Self-monitoring:** any source error, AI fallback, or missing secret pings the operator — the tool never degrades silently.
- **Repo stays public** (decision): privating kills free Pages while the dashboard page would stay public anyway below Enterprise, and caps Actions minutes (silent-cron-stop risk). Everything sensitive was moved to secrets instead, and git history was squashed to a single commit to purge the watchlist from all prior versions.

## 9. Decision log (what & why)

- **Count everything, ping selectively** — dashboards/trends need completeness; humans need signal.
- **X pay-per-use** — Basic/Pro closed to new signups; ~$0.005/read with a spend cap beats a $5k subscription anyway.
- **Watchlist-as-secret + history squash** over privating the repo — hides the actual sensitive data without losing free Pages/Actions.
- **Reddit via public RSS** — self-serve API is closed; third-party scrapers rejected (ToS/reputational risk for a comms org); official application submitted as a free option.
- **Config-file tuning in the GitHub web UI** — versioned, zero infra, comms-editable; UI/slider rejected (backend + auth for one number).
- **No auto-modulating thresholds** — "too many alerts" during a crisis is the signal, not noise; only prominence bars are tunable, crisis floors (watchlist/surge/narrative) stay fixed.
- **"Plan A" dropped as a search term** — matched "plan a trip/variety show"; the title is covered by "AI 2040"/"ai-2040.com"/"AI2040". "Scott Alexander" deliberately excluded (cross-hit volume).
- **Retweets excluded at the query level** — X binds AND tighter than OR; the un-parenthesized query let retweet swarms through carrying the original's repost counts (launch-day incident, fixed 2026-07-02).

## 10. Open items

- [ ] Reddit official Data API application — submitted; add creds if approved
- [ ] Confirm/expand watchlist with comms (edits go into the secret)
- [ ] Verify the one channel-map feed flagged "verify" in the private copy
- [ ] Calibrate thresholds on real launch traffic
- [ ] Post-launch: non-English coverage; optional targeted subreddit RSS feeds
