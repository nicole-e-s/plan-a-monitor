# Plan A Monitor — User Manual

*For the comms team. No technical background needed. (How the system was designed and why: see `DESIGN.md`.)*

## What this is

A tool that watches public reaction to **"AI 2040: Plan A"** around the clock — X/Twitter, Hacker News, Google News, Substack/blogs, and (partially) Reddit — and posts to the **#plan-a-launch-watch** Slack channel **only when something needs eyes**. There's also a live dashboard you can check anytime:

**Dashboard:** https://nicole-e-s.github.io/plan-a-monitor/dashboard.html (updates every ~5 minutes)

## Reading the alerts

Every alert has a colored sidebar. The color tells you how to react:

| You see | What it means | What to do |
|---|---|---|
| 🔴 **CRITICAL** (red, pings @channel) | An influential person, a viral post, or a major outlet went **negative** | Read it now — this is "devise a response" territory |
| 🟠 **WARNING** (orange, pings responders) | A negative **pattern**: surge of negative posts, a critical narrative gaining traction, or a slow build over days | Skim it, watch the trend |
| 🟢 **Positive — influential** (green, pings responders) | An influential person said something **good** | Amplification opportunity |
| ⚪ **Heads-up — key figure** (white, pings responders) | An influential person said something **neutral** | FYI — may deserve engagement |
| ▫️ Grey, no ping | Routine: the once-a-day summary (4 PM PT) | Read at leisure |
| ⚠️ Warning triangle, pings the operator | The tool itself hit a problem | Nothing — the operator handles it |

Good to know:
- One message bundles everything from the last ~5 minutes — you never get one ping per post.
- **Silence is normal.** No message means nothing crossed the bar.
- Every item links to the original post/article.
- A `[features Name]` tag means the article *mentions* that person; `[Name]` means they *wrote/posted* it.

## What's "big enough" to ping?

- A **watchlist person** always alerts, no matter how small the post.
- Anyone else needs **any one** of: **100k+ views · 500+ likes · 50+ reposts · author with 150k+ followers**.
- Smaller posts are **not lost** — they all feed the dashboard, sentiment trends, and pattern detection. They just don't ping on their own.

## Changing the dials yourself (browser only, ~1 minute)

All the tunable settings live in one file: **`config.cloud.yaml`**. To edit it:

1. Go to **github.com/nicole-e-s/plan-a-monitor** (you need to be logged in with access).
2. Click **`config.cloud.yaml`** in the file list.
3. Click the **pencil icon** (✏️ "Edit this file") at the top-right of the file.
4. Change the numbers/words you need — every section has a plain-English comment explaining it.
5. Click the green **"Commit changes..."** button → keep "Commit directly to the main branch" → **Commit changes**.

Your change takes effect within ~5 minutes. Every edit is saved with your name and can be undone, so you can't break anything permanently.

**Safe to change:**

| Setting | What it does |
|---|---|
| `thresholds:` → likes / reposts / followers / impressions | The "big enough to ping" bars above. Higher numbers = fewer pings. |
| `search_terms:` | What we search for. ⚠️ Avoid generic phrases — "Plan A" had to be removed because it matched "plan a trip". |
| `narratives:` | The critical storylines we track (e.g. "doomer", "unscientific"). Each is a label + keywords + how many mentions trigger a warning. Add a new one by copying an existing block. |
| `daily_digest:` → `hour_utc` | When the daily summary posts (23 = 4 PM PT summer time). |

Anything else in the file (or anything code-related): ask the operator.

## The watchlist

The list of people we watch is deliberately **not** in this repository (the repo is public). To add or remove someone, **message the operator** — it takes them ~2 minutes. Please don't put names into `config.cloud.yaml`.

## FAQ

- **It's been quiet all day — is it broken?** Probably not: quiet is the design. Signs of life: the dashboard timestamp updates every ~5 minutes, the daily 4 PM PT summary arrives, and the tool automatically pings the operator if something actually breaks.
- **Why didn't post X alert?** Most likely it was below the bars — check the dashboard; it will still be counted there. If it feels wrong, tell the operator; the bars are one browser-edit away.
- **How complete is Reddit coverage?** Partial: public *posts* only (not comment threads), with no vote counts, and it can miss runs due to Reddit's rate limits. Reddit informs the trends; it rarely alerts on its own. Full API access has been applied for.
- **Can I force it to check right now?** Yes: repo → **Actions** tab → "Plan A monitor" → **Run workflow**. Useful on big moments if you don't want to wait 5 minutes.
