# Plan A Monitor — Design Doc

*Owner: Dillon Nguyen · Updated: 2026-07-01 · Status: pre-launch (launch ~July 2, 2026)*

## 1. Purpose

A media-monitoring tool that watches the public reaction to AIFP's publication **"AI 2040: Plan A"**, alerts the comms team in Slack when something is worth their attention, and feeds a live dashboard of volume + sentiment.

It exists to solve the **"AI 2027 problem"**: last time, negative discourse ("their timelines didn't pan out") wasn't caught early enough to shape the response. This is an early-warning system so comms can respond before a narrative hardens.

## 2. Goals & non-goals

**Goals (priority order):**
1. **Catch influential individual reactions** — a watchlist person, or anyone whose post earns real attention.
2. **Catch volume spikes.**
3. **Catch building negative narratives** — including slow ones that grow over several days.

**Non-goals:** not a full analytics suite (the dashboard is an ambient temperature check); not trying to *ping* on every mention (small ones still *count*); not real-time-to-the-second (≤5-min lag is fine).

## 3. Threat model

| Shape | Example | How we catch it |
|---|---|---|
| Single influential voice | A watched critic dunks on Plan A | Watchlist match (alerts regardless of size) |
| Single viral post | A no-name tweet blows up | Attention bar (impressions / engagement) |
| Volume spike | Sudden flood of mentions | Rate vs. rolling baseline |
| Negativity surge | Many negative posts at once | Count of negatives in a window |
| Slow-building narrative | "It's unscientific" accretes over days | Rolling multi-day narrative totals (§7) |

## 4. Sources

| Source | Role | Status |
|---|---|---|
| **X/Twitter (API, pay-per-use)** | Primary. Broad keyword search **+** direct `from:` polling of every watchlist account (catches subtweets that never name the project). | ✅ live — app `aifp_scraper`, `X_BEARER_TOKEN` set, $500 credits, $1,000/mo spend cap. ~$0.005/read; 10-min read window keeps cost low (X ended new Basic/Pro signups — pay-per-use is the only option). |
| **Substack / blogs (RSS)** | Longform reactions from watchlist people | ✅ live — 14 per-person feeds wired (§6) |
| **Hacker News (Algolia)** | Article discussion + comment sentiment | ✅ live |
| **Google News (RSS)** | Detects article pickup / syndication | ✅ live (English/US) |
| **Reddit (public search RSS)** | Community reaction — **partial coverage, see extent note below** | ✅ live, best-effort. Official Data API application pending. |
| **Non-English news** | Foreign-language coverage | Post-launch |

**Reddit access — exact extent.** Reddit closed self-serve API registration in 2026 ("Responsible Builder Policy"), so current coverage comes from Reddit's **public search RSS**, which is materially weaker than real API access. Specifically:

1. **Posts only** — submissions turn up in search RSS; **comments do not**. A hostile comment thread under someone else's post is invisible to us until/unless it becomes its own post or shows up elsewhere.
2. **No engagement data** — RSS exposes no upvote/comment counts, so Reddit items feed volume, sentiment, and narrative detection but can **never fire a solo "high-attention post" alert**.
3. **Best-effort reliability** — Reddit aggressively rate-limits RSS (HTTP 429), especially from datacenter IPs like GitHub Actions'. Some runs will fetch nothing; errors are logged in the run output, not alerted.
4. One combined OR query per run (not per-term) to minimize throttling.

If the pending official Data API application is approved, adding `REDDIT_CLIENT_ID`/`REDDIT_CLIENT_SECRET` secrets auto-enables the full source (posts **and** comments, with engagement) alongside the RSS fallback — no code change.

## 5. Pipeline

`fetch → keyword prefilter → dedupe → classify (tiered AI) → score → aggregate → alert + record`

Deliberately **cost-asymmetric**: the expensive step (AI) only runs on posts that are *new* **and** *passed a cheap keyword filter*, so scanning broadly stays cheap.

- **Classify — tiered models:** **Haiku 4.5** for the bulk relevance + sentiment pass; **escalate to Sonnet 5** only for high-stakes items (negative/mixed sentiment or substantive critiques) for higher fidelity where it matters. Article text is fetched for links/news so judgments aren't headline-only.

## 6. Watchlist & channel map

Watchlist authors alert **regardless of engagement** — their importance is the signal. Currently **17 people** (5 critical, 12 high).

**The watchlist itself is private.** Names, X handles, weights, and blog-feed URLs live **only** in the GitHub Actions secret `WATCHLIST_YAML` (mirrored in the gitignored `watchlist.local.yaml` for local runs, and in the comms team's private copy of this doc) — never in the public repo or its git history. Watchlist edits go into the secret, not this repo.

Coverage per person is threefold: their **X accounts are polled directly** (`from:` queries every run, so a subtweet that never names the project is still caught), their **Substack/blog feeds** are read (14 feeds), and any **article or post that features them by name** counts as a watchlist hit (labeled `[features X]` — news "authors" are outlets, so a watched columnist's op-ed or a piece quoting a watched official would otherwise never match).

*(Channel-map table — who, weight, X handle, feed URL, per-person caveats — lives in the private copy of this doc and in the `WATCHLIST_YAML` secret.)*

**TODO:** confirm completeness with comms (more names to add) — additions go into the secret.

## 7. Alert tiers & Slack routing

**Governing rule:** *ping Lauren + Nicole whenever we should **respond**; ping `@channel` when we must **devise a response to something negative**.* Sidebar color encodes the cell for skimming.

| Who posted | Negative | Neutral | Positive |
|---|---|---|---|
| Watchlist person | 🔴 CRITICAL · `@channel` · red | ⚪ ping Lauren+Nicole · white | 🟢 ping Lauren+Nicole · green |
| Big-attention non-watchlist | 🔴 CRITICAL · `@channel` · red | ⚪ ping Lauren+Nicole · white | 🟢 ping Lauren+Nicole · green |
| General crowd (aggregate) | 🟠 WARNING · ping Lauren+Nicole · orange | ▫️ plaintext · grey | 🟩 plaintext · light-green |
| Daily summary | — | ▫️ plaintext · grey · once/day | — |

**Mechanics:**
- Pings use Slack **member IDs** (plain display names don't notify); the responder IDs are private and come from the `SLACK_RESPOND_NOTIFY` secret. `@channel` = `<!channel>`.
- **Aesthetics:** colored attachment sidebars / Block Kit per tier, with a **plain-text fallback** so formatting can never drop an alert.
- **Batching:** alerts grouped **per 5-min window per tier** (one message listing what crossed the bar); **silent if the window is empty**.
- **Daily digest:** one GENERAL (no-ping) summary per day at **4:00 PM PDT (23:00 UTC)** — last-24h volume, sentiment split, top themes, notable posts.
- **Slow-build narratives:** WARNING also fires when a narrative's rolling total over the last **3 days** reaches its build bar (default **3× its per-window `alert_count`**), with a 12-hour cooldown so it doesn't re-warn every 5 minutes. This catches the drip of criticism that never spikes in any single window.

*Status: all implemented — routing, colors, pings, batching, daily digest, slow-build detection, featured-in-article matching.*

## 8. Thresholds & tuning

- **"Worth a ping" bar (non-watchlist):** ~**10,000 impressions**, or engagement equivalent (~50–100 interactions) as a fallback since `impression_count` can read 0. Starting number, **calibrated on live traffic**.
- **Watchlist people bypass the bar.**
- **The bar gates *alerts*, never *counting*** — every relevant post still flows to the dashboard/sentiment/surge/narrative logic, so a swarm of small negatives still trips a WARNING. This is why the exact number is low-stakes.
- **No auto-modulation** (fixed number). **Controlled via GitHub's browser editor** on a heavily-commented config, so a non-technical comms person can adjust it without a terminal.

## 9. Health / self-monitoring

The tool must never **degrade silently**. A run-level error collector pings the operator (member ID in the `SLACK_ERROR_NOTIFY` secret) whenever a source errors, the AI falls back to keyword-only, the watchlist secret is missing, or an API key is absent. *(Implemented.)*

## 10. Dashboard

Static GitHub Pages reading `timeseries.json`: volume + sentiment over time + recent notable mentions. Ambient "check anytime" tool; display-only (a static page can't control anything).

## 11. Models & cost

- **Tiered:** Haiku 4.5 (bulk) + Sonnet 5 (escalation). *(Implemented; fixed a previously-invalid model id that had been silently degrading the tool to keyword-only.)*
- **Cost:** AI spend is a rounding error next to X API usage (pay-per-use, ~$0.005/read) — optimize for **fidelity**, not pennies.

## 12. Operations

- **Repo:** runs from the handler's repo (`nicole-e-s/plan-a-monitor`); Dillon has push access.
- **Schedule:** GitHub Actions cron, **every 5 min**; state committed back each run.
- **Secrets** (GitHub → Settings → Secrets and variables → Actions — *never in code/YAML*): `SLACK_WEBHOOK_URL` ✅, `ANTHROPIC_API_KEY` ✅, `X_BEARER_TOKEN` ✅ ($500 credits, $1,000/mo cap), `WATCHLIST_YAML` (the private watchlist + feeds — the monitor health-pings the operator if it's missing), `SLACK_ERROR_NOTIFY` + `SLACK_RESPOND_NOTIFY` (private staff member IDs for pings), `REDDIT_CLIENT_ID`/`SECRET` pending Reddit's application review (RSS coverage needs none). X/Reddit auto-enable when their secrets exist.
- **Cron reliability:** GitHub cron is best-effort (can lag/skip). The design is resilient — the 1-hour lookback + dedupe means a skipped run is recovered on the next one (latency, not data loss). Launch day: keep the manual "Run workflow" button (or an external trigger) as backup.
- **Repo privacy — DECIDED: stay public through launch.** Rationale: (a) ownership transfer takes time we don't have; (b) **Pages needs GitHub Pro+ on a private repo, and the published dashboard stays public anyway** (only Enterprise gates the page) — so privating loses the free dashboard without actually hiding it; (c) **private-repo Actions minutes are capped** (Free 2k/mo vs our ~9k–17k at a 5-min cadence) — overage billing or, worse, **a silent cron stop mid-launch**; public repos get unlimited free Actions; (d) what's exposed is modest: code, generic config, aggregate counts, links to already-public posts. The one real sensitivity — the watchlist — is handled directly: it lives **only in the `WATCHLIST_YAML` secret**, the git history was **squashed to a single commit** so no prior version of it survives in the repo, dashboard data strips watchlist tags, and run logs never print watched names. Revisit repo privacy after launch week if anything else becomes sensitive.

## 13. Security & privacy

Secrets live only as encrypted GitHub Actions secrets, never in code/config. **The watchlist (names, handles, feeds) is treated as a secret** (`WATCHLIST_YAML`): it appears nowhere in the repo, its history, its Actions logs (feed errors log an index, not a name), or the public dashboard data (`[Name]`/`[features Name]` tags are stripped before `timeseries.json` is written). Beyond that, only aggregate counts + links to already-public posts are exposed. The threshold-control surface is just a config file behind repo write-access — no public endpoint to tamper with.

## 14. Open items / TODO

- [x] Fix model id → tiered Haiku/Sonnet
- [x] Health/self-monitoring ping on silent failures
- [x] Watchlist expanded to 17; all X handles filled
- [x] Tiered Slack routing (colors, pings, per-window batching, silent-if-empty)
- [x] Substack RSS source — 14 channel-map feeds wired
- [x] Daily digest at 4 PM PDT (23:00 UTC)
- [x] X: `aifp_scraper` app, pay-per-use credits + spend cap, `X_BEARER_TOKEN` set
- [x] Reddit interim coverage via public search RSS (see extent note, §4)
- [x] Multi-day narrative build tracking (rolling 3-day totals + cooldown)
- [x] Watchlist matching on articles (`[features X]`) + direct X polling of watchlist accounts
- [x] Repo privacy decided: stay public through launch (§12)
- [x] Watchlist made private: moved to `WATCHLIST_YAML` secret, git history squashed, logs + dashboard scrubbed
- [ ] Submit Reddit official Data API application; add creds if approved
- [ ] Confirm/expand watchlist with comms; tune thresholds in the quiet window
- [ ] Verify the one channel-map feed flagged "verify" in the private copy (writer reportedly changed outlets mid-2026)

## 15. Phasing

- **Launch (now live):** X pay-per-use (broad + watchlist polling), HN, Google News, Substack, Reddit-RSS; tiered models; tiered/batched Slack alerts; daily digest; slow-build narrative detection; dashboard; config-tuned thresholds; health pings.
- **Fast-follow:** official Reddit API if approved; more watchlist people; threshold calibration from real launch data.
- **Post-launch:** non-English coverage; watchlist-in-secret if comms wants it hidden; repo-privacy revisit.
