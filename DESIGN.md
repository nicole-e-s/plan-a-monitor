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
| Single influential voice | Dean Ball dunks on Plan A | Watchlist match (alerts regardless of size) |
| Single viral post | A no-name tweet blows up | Attention bar (impressions / engagement) |
| Volume spike | Sudden flood of mentions | Rate vs. rolling baseline |
| Negativity surge | Many negative posts at once | Count of negatives in a window |
| Slow-building narrative | "It's unscientific" accretes over days | Multi-day narrative tracking *(to build)* |

## 4. Sources

| Source | Role | Status |
|---|---|---|
| **X/Twitter (API Pro)** | Primary. Broad keyword search **+** per-watchlist `from:` queries. | Pro approved; **TODO** pick owning account → add `X_BEARER_TOKEN` |
| **Substack / blogs (RSS)** | Longform reactions from watchlist people | Feeds mapped (§6); **TODO** build the RSS fetcher |
| **Hacker News (Algolia)** | Article discussion + comment sentiment | ✅ live |
| **Google News (RSS)** | Detects article pickup / syndication | ✅ live (English/US) |
| **Reddit** | Community reaction | Code wired; **TODO** create "script" app → add creds |
| **Non-English news** | Foreign-language coverage | Post-launch |

## 5. Pipeline

`fetch → keyword prefilter → dedupe → classify (tiered AI) → score → aggregate → alert + record`

Deliberately **cost-asymmetric**: the expensive step (AI) only runs on posts that are *new* **and** *passed a cheap keyword filter*, so scanning broadly stays cheap.

- **Classify — tiered models:** **Haiku 4.5** for the bulk relevance + sentiment pass; **escalate to Sonnet 5** only for high-stakes items (negative/mixed sentiment or substantive critiques) for higher fidelity where it matters. Article text is fetched for links/news so judgments aren't headline-only.

## 6. Watchlist & channel map

Watchlist authors alert **regardless of engagement** — their importance is the signal. **17 people** (5 critical, 12 high). Handles + feeds researched below.

| Name | Weight | X handle | Substack/blog feed | Note |
|---|---|---|---|---|
| David Sacks | critical | @davidsacks | `sacks.substack.com/feed` | feed dormant → rely on X |
| Sriram Krishnan | critical | @sriramk | `sriramk.substack.com/feed` | ⚠ NOT `sriramkrishnan.substack.com` (different person) |
| JD Vance | critical | @JDVance (@VP) | — | no newsletter |
| Dean Ball | critical | @deanwball | `hyperdimensional.co/feed` | live |
| Michael Kratsios | critical | @mkratsios47 | — | handle newly found |
| Matt Yglesias | high | @mattyglesias | `slowboring.com/feed` | live |
| Ezra Klein | high | @ezraklein | `feeds.simplecast.com/kEKXbjuJ` | NYT podcast (no personal newsletter) |
| Ross Douthat | high | @DouthatNYT | `douthat.substack.com/feed` | dormant → rely on X + Google News |
| Dwarkesh Patel | high | @dwarkesh_sp | `dwarkesh.com/feed` | live (Substack handle is @dwarkesh) |
| Kevin Roose | high | @kevinroose | `kevinroose.substack.com/feed` | *verify* — per research he left NYT; Google News still covers NYT |
| Zvi Mowshowitz | high | @TheZvi | `thezvi.wordpress.com/feed/` | Substack mirror also exists |
| Jack Clark | high | @jackclarkSF | `importai.substack.com/feed` | live |
| Nate Silver | high | @NateSilver538 | `natesilver.net/feed` | live |
| Helen Toner | high | @hlntnr | `helentoner.substack.com/feed` | live |
| Tyler Cowen | high | @tylercowen | `marginalrevolution.com/feed` | live |
| Shakeel Hashim | high | @ShakeelHashim | `transformernews.ai/feed` | live |
| Leopold Aschenbrenner | high | @leopoldasch | — | essay site has no feed → rely on X; handle newly found |

**TODO:** confirm completeness with comms (more names to add); wire the feeds when the Substack source is built.

## 7. Alert tiers & Slack routing

**Governing rule:** *ping Lauren + Nicole whenever we should **respond**; ping `@channel` when we must **devise a response to something negative**.* Sidebar color encodes the cell for skimming.

| Who posted | Negative | Neutral | Positive |
|---|---|---|---|
| Watchlist person | 🔴 CRITICAL · `@channel` · red | ⚪ ping Lauren+Nicole · white | 🟢 ping Lauren+Nicole · green |
| Big-attention non-watchlist | 🔴 CRITICAL · `@channel` · red | ⚪ ping Lauren+Nicole · white | 🟢 ping Lauren+Nicole · green |
| General crowd (aggregate) | 🟠 WARNING · ping Lauren+Nicole · orange | ▫️ plaintext · grey | 🟩 plaintext · light-green |
| Daily summary | — | ▫️ plaintext · grey · once/day | — |

**Mechanics:**
- Member IDs (display names don't notify): **Lauren `<@U08QWKU8CJY>`**, **Nicole `<@U08PH68S2AU>`**; `@channel` = `<!channel>`.
- **Aesthetics:** colored attachment sidebars / Block Kit per tier, with a **plain-text fallback** so formatting can never drop an alert.
- **Batching:** alerts grouped **per 5-min window per tier** (one message listing what crossed the bar); **silent if the window is empty**.
- **Daily digest:** one GENERAL (no-ping) summary per day at **4:00 PM PDT (23:00 UTC)**.

*Status: routing/colors/batching/digest still to build; core alert engine exists.*

## 8. Thresholds & tuning

- **"Worth a ping" bar (non-watchlist):** ~**10,000 impressions**, or engagement equivalent (~50–100 interactions) as a fallback since `impression_count` can read 0. Starting number, **calibrated on live traffic**.
- **Watchlist people bypass the bar.**
- **The bar gates *alerts*, never *counting*** — every relevant post still flows to the dashboard/sentiment/surge/narrative logic, so a swarm of small negatives still trips a WARNING. This is why the exact number is low-stakes.
- **No auto-modulation** (fixed number). **Controlled via GitHub's browser editor** on a heavily-commented config, so a non-technical comms person can adjust it without a terminal.

## 9. Health / self-monitoring

The tool must never **degrade silently**. A run-level error collector pings the operator (**Dillon `<@U0BED0HLDLL>`**, config `error_notify`) whenever a source errors, the AI falls back to keyword-only, or the API key is missing. *(Implemented.)*

## 10. Dashboard

Static GitHub Pages reading `timeseries.json`: volume + sentiment over time + recent notable mentions. Ambient "check anytime" tool; display-only (a static page can't control anything).

## 11. Models & cost

- **Tiered:** Haiku 4.5 (bulk) + Sonnet 5 (escalation). *(Implemented; fixed a previously-invalid model id that had been silently degrading the tool to keyword-only.)*
- **Cost:** AI spend is a rounding error next to the X API Pro subscription — optimize for **fidelity**, not pennies.

## 12. Operations

- **Repo:** runs from the handler's repo (`nicole-e-s/plan-a-monitor`); Dillon has push access.
- **Schedule:** GitHub Actions cron, **every 5 min**; state committed back each run.
- **Secrets** (GitHub → Settings → Secrets and variables → Actions — *never in code/YAML*): `SLACK_WEBHOOK_URL` ✅, `ANTHROPIC_API_KEY` ✅, `X_BEARER_TOKEN` TODO, `REDDIT_CLIENT_ID`/`SECRET` TODO. X/Reddit auto-enable when their secrets exist.
- **Cron reliability:** GitHub cron is best-effort (can lag/skip). The design is resilient — the 1-hour lookback + dedupe means a skipped run is recovered on the next one (latency, not data loss). Launch day: keep the manual "Run workflow" button (or an external trigger) as backup.
- **Repo privacy (decision pending):** going private hides the code + config (strategy), but note two costs — (a) **Pages needs GitHub Pro+**, and the published dashboard **stays public** anyway (only Enterprise makes the page access-controlled); (b) **private-repo Actions minutes are capped** (Free 2k / Pro 3k / Team 50k per month; $0.006/min overage), and a 5-min cron uses ~9k–17k min/month → expect overage (~$40–90/mo, trivial vs X) **or a silent stop when the quota runs out**. Public repos get unlimited free Actions. *Recommended if going private:* enable Actions billing so the cron never silently stops, and either accept a public dashboard URL (Pro) or split the dashboard into a separate tiny public repo holding only `dashboard.html` + `timeseries.json`.

## 13. Security & privacy

Secrets live only as encrypted GitHub Actions secrets, never in code/config. If the repo stays public, only aggregate counts + links to already-public posts are exposed. The threshold-control surface is just a config file behind repo write-access — no public endpoint to tamper with.

## 14. Open items / TODO

- [x] Fix model id → tiered Haiku/Sonnet
- [x] Health/self-monitoring ping on silent failures
- [x] Add Leopold + fill Kratsios/Leopold handles
- [ ] Build tiered Slack routing (colors, pings, per-window batching, silent-if-empty)
- [ ] Build Substack RSS source + wire the channel-map feeds
- [ ] Daily digest at 4 PM PDT (23:00 UTC)
- [ ] X: choose account → subscribe Pro → add `X_BEARER_TOKEN`
- [ ] Reddit: create "script" app → add creds
- [ ] Decide repo privacy (see §12)
- [ ] Multi-day narrative tracking
- [ ] Fix watchlist-matching on articles (news author = outlet, not person)
- [ ] Confirm/expand watchlist with comms; tune thresholds in the quiet window

## 15. Phasing

- **Launch:** X (Pro), HN, News, Substack; tiered models; tiered/batched Slack alerts; dashboard; config-tuned thresholds; health pings.
- **Fast-follow:** Reddit; more watchlist people; threshold calibration from real data.
- **Post-launch:** non-English; multi-day narrative refinements; repo-privacy cleanup.
