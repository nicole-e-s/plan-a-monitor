# Plan A Monitor — design notes

Last updated July 2, 2026. The monitor is live; the report launches in a few days. Day-to-day instructions live in USER-MANUAL.md. This file covers how the system works and why it's built the way it is.

## Why this exists

When AI 2027 came out, negative discourse ("their timelines didn't pan out") built up before the team caught it, and the response came late. This monitor exists so that doesn't happen with AI 2040: watch the reaction wherever it happens, keep Slack quiet unless something matters, and keep a dashboard running for anyone who wants to check the temperature.

Priorities, in order: reactions from influential people, then volume spikes, then negative narratives building over time. Non-goals: it isn't an analytics product, it doesn't ping on every mention, and a five-minute delay is acceptable.

## What we're trying to catch

| Shape | How we catch it |
|---|---|
| One influential voice | Watchlist match. Alerts at any size |
| One viral post | Attention bars: views, likes, reposts, follower count |
| A volume spike | Mention rate against a rolling baseline |
| A negativity surge | Several negative mentions inside one window |
| A narrative building over days | Rolling 3-day totals per narrative, warning at 3x the per-window bar, with a 12-hour cooldown |

## Sources

| Source | Notes |
|---|---|
| X/Twitter | The main one. A broad keyword query, plus direct polling of every watchlist account (from: queries), which catches subtweets that never name the project. Pay-per-use API at about half a cent per post read, with a spending cap on the account; X closed the old Basic/Pro plans to new signups, so pay-per-use is the only option anyway. A 10-minute read window keeps us from paying for the same tweets twice. |
| Substack / blogs | RSS, one feed per watchlist writer. |
| Hacker News | Algolia search, exact phrase, stories and comments. |
| Google News | RSS. Outlet tiers decide what's alert-worthy on its own; wide syndication also counts. |
| Reddit | Partial, and not by choice: Reddit closed self-serve API signup in 2026. We read their public search RSS instead, which covers public posts only (no comment threads), has no vote counts, and gets rate-limited from cloud IPs. So Reddit feeds the trend lines but can't fire its own alerts. We've applied for real API access; if it's granted, adding the credentials as secrets turns the full source on with no code change. |
| Non-English news | Not yet. Post-launch. |

## How it works

fetch → keyword prefilter → dedupe → AI classification → scoring → aggregation → alerts + dashboard

The expensive step, AI classification, only runs on posts that are new and passed a free keyword filter, so scanning broadly stays cheap. Classification runs on Haiku 4.5 in bulk; anything negative, mixed, or critique-shaped gets a second pass on Sonnet 5. If that second pass fails we keep the first-pass labels rather than falling back to keyword matching. For articles and link posts we fetch the article text, so the judgment isn't based on a headline alone. At our volume the AI cost is trivial next to the X API bill, so the models are picked for accuracy, not price.

## Alerting

The rule of thumb: ping the responders when we should respond, ping @channel when we need to build a response to something negative. Who the responders are lives in the SLACK_RESPOND_NOTIFY secret.

| Who posted | Negative | Neutral | Positive |
|---|---|---|---|
| Watchlist or big-attention | CRITICAL, @channel | white, ping responders | green, ping responders |
| Everyone else, in aggregate | WARNING, ping responders | grey, no ping | light green, no ping |

Alerts are batched per 5-minute window per tier, colored by tier, and silent when there's nothing to say. A post that already alerted won't alert again unless its engagement grows several-fold. One no-ping digest goes out daily at 23:00 UTC. Articles that mention a watchlist person by name count as watchlist hits (tagged "[features X]"), since news items are attributed to outlets rather than people.

## The watchlist

Names, handles, weights, and feed URLs are secrets (WATCHLIST_YAML, mirrored locally in the gitignored watchlist.local.yaml). They appear nowhere in the repo, its history, its logs (feed errors log an index, not a name), or the dashboard data (watchlist tags are stripped before timeseries.json is written). Each person is covered three ways: their X account is polled directly, their blog or Substack feed is read, and articles that mention them by name get flagged. Currently 17 people, 5 at critical weight.

## Thresholds

The bars only gate alerts; counting is never gated. A miscalibrated bar costs a ping too many or too few, not data. Current bars, set by comms on July 2: a post alerts on any one of 100k+ views, 500+ likes, 50+ reposts, or an author with 150k+ followers. Watchlist people skip the bars entirely. Substantive critiques get a discounted bar (roughly ten or more interactions) instead of the free pass they used to have. Tuning happens in config.cloud.yaml through the GitHub web editor; the manual has the steps.

## Running it

- GitHub Actions cron, every 5 minutes, from nicole-e-s/plan-a-monitor. State files are committed back after each run. GitHub's cron can lag or skip; a missed run is recovered by the next one (1-hour lookback plus dedup). There's a manual "Run workflow" button as backup.
- Secrets: SLACK_WEBHOOK_URL, ANTHROPIC_API_KEY, X_BEARER_TOKEN, WATCHLIST_YAML, SLACK_ERROR_NOTIFY, SLACK_RESPOND_NOTIFY, and eventually REDDIT_CLIENT_ID/SECRET. X and Reddit turn on automatically once their secrets exist.
- The tool watches itself: any source error, AI fallback, or missing secret pings the operator. It should never degrade silently.
- The repo is public on purpose. Making it private would lose free GitHub Pages (and the dashboard page would stay publicly reachable anyway on anything below Enterprise) and would cap Actions minutes, which risks the cron silently stopping mid-launch. Everything sensitive went into secrets instead, and the git history was squashed to a single commit to purge old copies of the watchlist.

## Decisions along the way

- Count everything, ping on little. Trends need complete data; a channel people mute is useless.
- X pay-per-use rather than a subscription. X closed Basic/Pro to new signups in February 2026, and pay-per-use is cheaper for read-only monitoring anyway. Spend is capped at the account level.
- Watchlist in a secret plus a history squash, rather than a private repo. See "Running it" for why private wasn't worth it.
- Reddit through public RSS. Third-party Reddit scrapers exist but break Reddit's terms, and "monitoring org caught using banned scrapers" is a story we'd rather not create. The official application is in.
- Threshold tuning through the GitHub web editor. A slider dashboard and a Slack command were both considered; each needs a backend and auth just to change a number.
- No auto-adjusting thresholds. In a real crisis, "too many alerts" is the information; a monitor that quiets itself under load mutes itself at the worst possible moment. Only the prominence bars are tunable. The watchlist, surge, and narrative triggers stay fixed.
- "Plan A" was dropped as a search term on July 2 after matching things like "plan a variety show". The title is still covered by "AI 2040", "AI2040", and ai-2040.com. Scott Alexander isn't a search term either; his name pulls in far too much unrelated discussion.
- Day-one incident, written down because someone will hit it again: retweet swarms flooded the alerts. X's query grammar binds a space (AND) tighter than OR, so our -is:retweet filter was only applying to the last search term, and retweets carry the original tweet's repost count, so they cleared the bars easily. Fixed by putting parentheses around the OR group.

## Still open

- Reddit Data API application (submitted; add credentials if granted)
- Confirm the watchlist is complete (edits go in the secret)
- Verify the one feed flagged "verify" in the private copy of this doc
- Recalibrate thresholds once real launch traffic exists
- Later: non-English news, maybe targeted subreddit RSS feeds
