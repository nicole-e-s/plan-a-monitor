# Plan A Monitor — user manual

For the comms team. Nothing in here requires a technical background. For how the system is designed and why, see DESIGN.md.

## What this is

A monitor that watches public reaction to "AI 2040: Plan A" around the clock: X/Twitter, Hacker News, Google News, Substack blogs, and (partially) Reddit. It posts to #plan-a-launch-watch in Slack when something needs attention and stays quiet otherwise. There's also a live dashboard:

https://nicole-e-s.github.io/plan-a-monitor/dashboard.html

It updates every 5 minutes or so.

## Reading the alerts

Each alert has a colored bar on its left side. The color tells you how urgent it is.

| Alert | What happened | What to do |
|---|---|---|
| 🔴 CRITICAL (red, pings @channel) | Someone influential, something viral, or a major outlet went negative | Read it now and start thinking about a response |
| 🟠 WARNING (orange, pings the responders) | A negative pattern is forming: a surge of negative posts, or one of the narratives we track is picking up | Skim it and keep an eye on the trend |
| 🟢 Green (pings the responders) | Someone influential said something positive | Chance to amplify |
| ⚪ White (pings the responders) | Someone influential said something neutral | FYI, might be worth engaging |
| ▫️ Grey (no ping) | Routine, including the daily summary at 4pm Pacific | Read whenever |
| ⚠️ (pings the operator) | The tool itself had a problem | Nothing, the operator handles these |

A few things worth knowing:

- Alerts are batched. One message covers everything from the last 5 minutes, so you'll never get ten pings in a row.
- Silence means nothing crossed the bar. That's normal, especially pre-launch.
- Every item links to the original post or article.
- A "[features Name]" tag means an article mentions that person. "[Name]" without "features" means they wrote it.

## What's big enough to trigger an alert

People on the watchlist always alert, even for tiny posts. For everyone else, a post needs at least one of these:

- 100k+ views
- 500+ likes
- 50+ reposts
- an author with 150k+ followers

Posts below the bar aren't ignored. They still count in the dashboard and in the pattern detection. They just don't ping anyone on their own.

## Changing settings yourself

All the tunable settings live in one file, config.cloud.yaml, and you can edit it from the browser:

1. Go to github.com/nicole-e-s/plan-a-monitor (logged in, with access)
2. Click config.cloud.yaml
3. Click the pencil icon at the top right of the file ("Edit this file")
4. Change what you need. Every section has a comment explaining what it does
5. Click the green "Commit changes..." button, keep "Commit directly to the main branch" selected, and commit

The change takes effect within about 5 minutes. Every edit is recorded under your name and can be reverted, so you can't permanently break anything.

Settings that are safe to change:

| Setting | What it does |
|---|---|
| thresholds | The "big enough to alert" numbers above. Higher numbers mean fewer pings |
| search_terms | The phrases we search for. Avoid generic ones; "Plan A" had to be removed because it matched things like "plan a trip" |
| narratives | The critical storylines we track ("doomer", "unscientific", and so on). Each has a label, some keywords, and a mention count that triggers a warning. To add one, copy an existing block |
| daily_digest, hour_utc | When the daily summary posts. 23 means 4pm Pacific during summer |

For anything else in the file, ask the operator first.

## The watchlist

The list of people we watch is deliberately not in this repository, because the repository is public. To add or remove someone, message the operator; it takes a couple of minutes on their end. Please don't put names into config.cloud.yaml.

## FAQ

**Quiet all day. Is it broken?** Probably not; quiet is the intended behavior. Check the dashboard timestamp (it updates every ~5 minutes), and the daily 4pm summary should always arrive. If the tool itself breaks, it pings the operator automatically.

**Why didn't post X trigger an alert?** Most likely it was under the bars. Check the dashboard; it should still be counted there. If you think the bars are set wrong, they're one browser edit away (see above).

**How good is the Reddit coverage?** Partial. We can see public posts but not comment threads, and we get no vote counts, so Reddit informs the trends but rarely alerts on its own. We've applied for full API access.

**Can I make it check right now?** Yes. In the repo, go to the Actions tab, click "Plan A monitor", then "Run workflow". Useful in a big moment if you don't want to wait five minutes.
