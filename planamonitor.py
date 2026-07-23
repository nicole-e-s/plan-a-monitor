#!/usr/bin/env python3
"""Plan A Monitor — single-file build (all modules combined)."""
from __future__ import annotations
import argparse, base64, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Optional

# --------------------------------------------------------------------------
# Run-level health tracking: collect problems (source errors, AI fallbacks,
# a missing API key) so the operator gets pinged instead of the tool
# degrading silently.
# --------------------------------------------------------------------------
_ERRORS: list[str] = []


def _note_error(msg: str) -> None:
    print("  ⚠ " + msg)
    _ERRORS.append(msg)


# ===== from sources.py =====




# --------------------------------------------------------------------------
# Common shape every source produces
# --------------------------------------------------------------------------
@dataclass
class Mention:
    platform: str                 # "twitter" | "reddit" | "hackernews"
    id: str                       # unique id within the platform
    url: str
    text: str
    author: str = ""              # handle or username
    author_name: str = ""         # display name (twitter)
    author_followers: Optional[int] = None
    author_verified: bool = False
    created_at: Optional[datetime] = None
    engagement: int = 0           # single comparable number (see _engagement)
    metrics: dict = field(default_factory=dict)   # raw platform metrics

    # filled in later by the relevance/scoring stages:
    confidence: str = "medium"    # high | medium | low (rule-based)
    about: str = ""               # plan_a | aifp_other | unrelated (AI-judged scope)
    about_conf: str = ""          # high | medium | low — confidence in that scope call
    relevant: Optional[bool] = None
    sentiment: str = "unknown"    # positive | neutral | negative | mixed
    stance: str = ""              # supportive | substantive_critique | dismissive | question | news_share | off_topic
    theme: str = ""               # short cluster label
    summary: str = ""             # one-line AI summary
    priority: float = 0.0
    tier: str = "low"             # critical | high | low


def _get_json(url: str, headers: dict | None = None, timeout: int = 15):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        # surface the response body — APIs (esp. X pay-per-use) put the actual
        # reason there ("credits depleted", "not enrolled", ...), and a bare
        # "403 Forbidden" is undiagnosable from a Slack ping
        try:
            detail = re.sub(r"\s+", " ", e.read(500).decode("utf-8", "ignore")).strip()
        except Exception:
            detail = ""
        if detail.lower().startswith(("<!doctype", "<html")):
            # an HTML body on an API endpoint = an edge/CDN interception, not
            # a real API answer; say so instead of dumping markup into Slack
            detail = "HTML challenge page (Cloudflare bot check on this runner IP — transient)"
        if detail:
            e.msg = f"{e.msg} — {detail[:250]}"
        raise


# --------------------------------------------------------------------------
# Hacker News  (free, no auth — via the Algolia search API)
# --------------------------------------------------------------------------
def fetch_hackernews(terms: list[str], since: datetime, limit: int = 100) -> list[Mention]:
    out: list[Mention] = []
    since_ts = int(since.timestamp())
    for term in terms:
        # search both stories and comments, EXACT PHRASE only (advancedSyntax +
        # quotes) so HN's fuzzy matcher doesn't flood us with generic AI chatter.
        q = urllib.parse.quote(f'"{term}"')
        url = (f"https://hn.algolia.com/api/v1/search_by_date?query={q}&advancedSyntax=true"
               f"&tags=(story,comment)&numericFilters=created_at_i>{since_ts}"
               f"&hitsPerPage={limit}")
        # Algolia occasionally drops connections from GitHub runner IPs
        # (Errno 104) — transient, so retry before declaring the term failed.
        data = None
        for attempt in range(3):
            try:
                data = _get_json(url)
                break
            except Exception as e:
                if attempt < 2:
                    time.sleep(3 * (attempt + 1))
                else:
                    _note_error(f"[HN] fetch error for '{term}': {e}")
        if data is None:
            continue
        for h in data.get("hits", []):
            is_comment = h.get("comment_text") is not None
            text = h.get("comment_text") or h.get("title") or h.get("story_text") or ""
            oid = str(h.get("objectID"))
            article_url = h.get("url")     # external link for link-style submissions
            domain = urllib.parse.urlparse(article_url).netloc.replace("www.", "") if article_url else ""
            out.append(Mention(
                platform="hackernews",
                id=oid,
                url=f"https://news.ycombinator.com/item?id={oid}",
                text=text,
                author=h.get("author", ""),
                created_at=datetime.fromtimestamp(h.get("created_at_i", since_ts), timezone.utc),
                engagement=int(h.get("points") or 0) + int(h.get("num_comments") or 0),
                metrics={"points": h.get("points") or 0,
                         "num_comments": h.get("num_comments") or 0,
                         "type": "comment" if is_comment else "story",
                         "article_url": article_url, "domain": domain},
            ))
        time.sleep(0.3)   # be polite
    return out


# --------------------------------------------------------------------------
# Reddit  (free, but needs a script app: client_id + secret -> OAuth token)
# --------------------------------------------------------------------------
def _reddit_token(client_id: str, client_secret: str, user_agent: str) -> str:
    data = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    req = urllib.request.Request(
        "https://www.reddit.com/api/v1/access_token",
        data=data,
        headers={"Authorization": f"Basic {basic}", "User-Agent": user_agent},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)["access_token"]


def fetch_reddit(terms: list[str], since: datetime, cfg: dict, limit: int = 100) -> list[Mention]:
    try:
        token = _reddit_token(cfg["client_id"], cfg["client_secret"], cfg["user_agent"])
    except Exception as e:
        _note_error(f"[Reddit] auth failed ({e}); skipping Reddit this run.")
        return []
    headers = {"Authorization": f"Bearer {token}", "User-Agent": cfg["user_agent"]}
    out: list[Mention] = []
    for term in terms:
        q = urllib.parse.quote(f'"{term}"')
        url = (f"https://oauth.reddit.com/search?q={q}&sort=new&limit={limit}"
               f"&type=link,comment&restrict_sr=false")
        try:
            data = _get_json(url, headers=headers)
        except Exception as e:
            _note_error(f"[Reddit] error for '{term}': {e}")
            continue
        for c in data.get("data", {}).get("children", []):
            d = c.get("data", {})
            created = datetime.fromtimestamp(d.get("created_utc", 0), timezone.utc)
            if created < since:
                continue
            text = d.get("selftext") or d.get("title") or d.get("body") or ""
            perma = d.get("permalink", "")
            out.append(Mention(
                platform="reddit",
                id=str(d.get("name") or d.get("id")),
                url=f"https://www.reddit.com{perma}" if perma else d.get("url", ""),
                text=(d.get("title", "") + "\n" + text).strip(),
                author=d.get("author", ""),
                created_at=created,
                engagement=int(d.get("score") or 0) + int(d.get("num_comments") or 0),
                metrics={"score": d.get("score") or 0,
                         "num_comments": d.get("num_comments") or 0,
                         "subreddit": d.get("subreddit", "")},
            ))
        time.sleep(0.5)
    return out


# --------------------------------------------------------------------------
# X / Twitter  (X API v2 recent search — needs a paid bearer token)
# --------------------------------------------------------------------------
def fetch_twitter(terms: list[str], since: datetime, cfg: dict,
                  watchlist: list[dict] | None = None,
                  blacklist: list[str] | None = None,
                  article_urls: list[str] | None = None) -> list[Mention]:
    token = cfg.get("bearer_token", "")
    if not token or token == "FILL IN":
        print("  [X] no bearer token configured; skipping X this run.")
        return []
    # A descriptive User-Agent matters: urllib's default ("Python-urllib/3.x")
    # is a bot signature that Cloudflare — which fronts X's API and challenges
    # datacenter IPs like GitHub's — weighs heavily.
    headers = {"Authorization": f"Bearer {token}", "User-Agent": "plan-a-monitor/1.0"}
    max_results = int(cfg.get("max_results", 100))
    start_time = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Query 1: broad keyword search. Queries 2+: EVERYTHING the watchlist
    # people tweet (chunked to stay under the query-length cap) — so a
    # subtweet that never names the project is still caught. The short
    # lookback window keeps this cheap under pay-per-use.
    # NOTE: the parentheses matter — X binds AND (space) tighter than OR, so
    # without them "-is:retweet" applies ONLY to the last term and retweet
    # swarms flood in (each carrying the ORIGINAL tweet's repost count).
    # blacklisted bots are excluded in the query itself — no point paying
    # per-read for Grok's reply firehose just to drop it locally
    no_bots = "".join(f" -from:{h.lstrip('@')}" for h in (blacklist or []))
    # url:"ai-2040.com" matches the EXPANDED link in a tweet — catches shares
    # of the site whose visible text never names the project (t.co wrapping
    # hides the URL from plain text search).
    queries = [("(" + " OR ".join(f'"{t}"' for t in terms)
                + ' OR url:"ai-2040.com"' + f") -is:retweet lang:en{no_bots}", "kw")]
    handles = [h.lstrip("@") for w in (watchlist or []) for h in w.get("handles", [])]
    chunk: list[str] = []
    for h in handles:
        if chunk and sum(len(x) + 9 for x in chunk) + len(h) + 9 > 480:
            queries.append(("(" + " OR ".join(f"from:{x}" for x in chunk) + ") -is:retweet", "watch"))
            chunk = []
        chunk.append(h)
    if chunk:
        queries.append(("(" + " OR ".join(f"from:{x}" for x in chunk) + ") -is:retweet", "watch"))

    # Queries N+: tweets LINKING to articles we already know are about us —
    # catches the keyword-less "this is wild <link>" shares and quote-posts
    # that only surface on trend pages. URLs come from our own news mentions.
    link_chunk: list[str] = []
    def _flush_links():
        if link_chunk:
            queries.append(("(" + " OR ".join(f'url:"{u}"' for u in link_chunk)
                            + f") -is:retweet{no_bots}", "link"))
            link_chunk.clear()
    for u in (article_urls or [])[:8]:
        u = re.sub(r"^https?://(www\.)?", "", u.split("?")[0].split("#")[0]).rstrip("/")
        if not u:
            continue
        if link_chunk and sum(len(x) + 9 for x in link_chunk) + len(u) + 9 > 450:
            _flush_links()
        link_chunk.append(u)
    _flush_links()

    out: list[Mention] = []
    got: set[str] = set()          # same tweet can match several query types
    for query, kind in queries:
        params = urllib.parse.urlencode({
            "query": query,
            "max_results": min(max(max_results, 10), 100),
            "tweet.fields": "created_at,public_metrics,author_id",
            "expansions": "author_id",
            "user.fields": "username,name,verified,public_metrics",
            "start_time": start_time,
        })
        url = f"https://api.x.com/2/tweets/search/recent?{params}"
        # Cloudflare challenges are transient (runner-IP reputation) — retry
        # before declaring the query failed.
        data = None
        for attempt in range(3):
            try:
                data = _get_json(url, headers=headers)
                break
            except Exception as e:
                if attempt < 2:
                    time.sleep(4 * (attempt + 1))
                else:
                    _note_error(f"[X] fetch error: {e}")
        if data is None:
            continue
        users = {u["id"]: u for u in data.get("includes", {}).get("users", [])}
        for t in data.get("data", []):
            if t["id"] in got:
                continue
            got.add(t["id"])
            u = users.get(t.get("author_id"), {})
            pm = t.get("public_metrics", {})
            upm = u.get("public_metrics", {})
            handle = u.get("username", "")
            out.append(Mention(
                platform="twitter",
                id=t["id"],
                url=f"https://x.com/{handle}/status/{t['id']}" if handle else f"https://x.com/i/status/{t['id']}",
                text=t.get("text", ""),
                author=f"@{handle}" if handle else "",
                author_name=u.get("name", ""),
                author_followers=upm.get("followers_count"),
                author_verified=bool(u.get("verified")),
                created_at=datetime.strptime(t["created_at"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
                    if t.get("created_at") else None,
                engagement=int(pm.get("like_count", 0)) + 2 * int(pm.get("retweet_count", 0)) + int(pm.get("quote_count", 0)),
                metrics={"likes": pm.get("like_count", 0), "reposts": pm.get("retweet_count", 0),
                         "quotes": pm.get("quote_count", 0), "replies": pm.get("reply_count", 0),
                         "impressions": pm.get("impression_count", 0),
                         "from_watchlist": kind == "watch",
                         "matched_link": kind == "link"},
            ))
        time.sleep(0.2)
    return out


def fetch_all(config: dict, since: datetime) -> list[Mention]:
    terms = [t["query"] for t in config["search_terms"]]
    now = datetime.now(timezone.utc)
    mentions: list[Mention] = []
    src = config["sources"]
    if src.get("hackernews", {}).get("enabled"):
        print("Fetching Hacker News…")
        mentions += fetch_hackernews(terms, since)
    if src.get("reddit", {}).get("enabled"):
        print("Fetching Reddit…")
        mentions += fetch_reddit(terms, since, src["reddit"])
    if src.get("reddit_rss", {}).get("enabled"):
        print("Fetching Reddit (RSS)…")
        rr_since = now - timedelta(hours=config.get("reddit_rss_lookback_hours", 6))
        mentions += fetch_reddit_rss(terms, rr_since)
    if src.get("twitter", {}).get("enabled"):
        print("Fetching X/Twitter…")
        # X is pay-per-use, so keep the window small (little overlap with the
        # 5-min cron) to avoid re-reading — and re-paying for — the same tweets.
        tw_since = now - timedelta(minutes=config.get("twitter_lookback_minutes", 10))
        # tweets that share our recent press coverage carry no keywords —
        # find them by the article LINK (url: operator, expanded-URL match)
        article_urls = []
        if src["twitter"].get("track_article_shares", True):
            cutoff = (now - timedelta(hours=48)).isoformat()
            seen_u = set()
            for e in reversed(_load(os.path.join(".", _MENTIONS), [])):
                if e.get("platform") == "news" and e.get("ts", "") >= cutoff \
                   and e.get("url") and e["url"] not in seen_u:
                    seen_u.add(e["url"])
                    article_urls.append(e["url"])
                if len(article_urls) >= 8:
                    break
        mentions += fetch_twitter(terms, tw_since, src["twitter"], config.get("watchlist", []),
                                  config.get("author_blacklist", []), article_urls)
    if src.get("news", {}).get("enabled"):
        print("Fetching news…")
        # window may be stretched by run() to cover a Google News outage
        news_since = since
        if config.get("news_lookback_hours"):
            news_since = min(since, now - timedelta(hours=config["news_lookback_hours"]))
        mentions += fetch_news(terms, news_since, src["news"])
    if config.get("substack_feeds"):
        print("Fetching Substack/blogs…")
        sub_since = now - timedelta(hours=config.get("substack_lookback_hours", 24))
        mentions += fetch_substack(config["substack_feeds"], sub_since)
    return mentions

# ===== from news.py =====




_STOP = set("a an the of to for and or in on at is are be with we our their this that "
            "new how why what plan ai 2027 2040".split())


def _norm_title(t: str) -> str:
    t = re.sub(r"\s*[-–|]\s*[^-–|]+$", "", t)        # strip trailing " - Outlet"
    t = re.sub(r"[^a-z0-9 ]", " ", t.lower())
    toks = [w for w in t.split() if w not in _STOP and len(w) > 2]
    return " ".join(toks)


def _similar(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    ta, tb = set(a.split()), set(b.split())
    jacc = len(ta & tb) / len(ta | tb) if (ta | tb) else 0
    return max(jacc, SequenceMatcher(None, a, b).ratio())


def _name_matches(name: str, outlet: str, domain: str) -> bool:
    """Match a configured outlet name against the article's outlet/domain using
    WORD BOUNDARIES, so 'TIME' matches 'Time' but not 'Hindustan Times', and a
    compact form ('newyorktimes') matches the domain (nytimes.com -> contains
    'nytimes'? no) — so we also try the spaced name inside the outlet text."""
    n = name.lower().strip()
    if re.search(r"\b" + re.escape(n) + r"\b", outlet.lower()):
        return True
    # domain check: the de-spaced name must EQUAL a whole domain label, so
    # 'time' matches time.com but NOT hindustantimes.com.
    host = re.sub(r"^https?://", "", (domain or "").lower()).split("/")[0]
    labels = {lbl for lbl in host.split(".") if lbl not in ("www", "com", "org", "net", "co", "uk", "io")}
    return re.sub(r"[^a-z0-9]", "", n) in labels


def _tier_of(outlet: str, domain: str, tier1: list[str], tier2: list[str]) -> int:
    if any(_name_matches(t, outlet, domain) for t in tier1):
        return 1
    if any(_name_matches(t, outlet, domain) for t in tier2):
        return 2
    return 3


def _fetch_rss(term: str, limit: int = 50):
    q = urllib.parse.quote(f'"{term}"')
    url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
    req = urllib.request.Request(url, headers={"User-Agent": "plan-a-monitor/0.1"})
    with urllib.request.urlopen(req, timeout=20) as r:
        root = ET.fromstring(r.read())
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        pub = it.findtext("pubDate") or ""
        src_el = it.find("source")
        outlet = (src_el.text if src_el is not None else "").strip()
        domain = (src_el.get("url") if src_el is not None else "") or ""
        try:
            dt = datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc)
        except Exception:
            dt = datetime.now(timezone.utc)
        items.append({"title": title, "link": link, "outlet": outlet,
                      "domain": domain, "date": dt})
        if len(items) >= limit:
            break
    return items


def fetch_news(terms, since, cfg, state_dir=".") -> list[Mention]:
    tier1 = cfg.get("tier1_outlets", [])
    tier2 = cfg.get("tier2_outlets", [])
    pickup_threshold = int(cfg.get("pickup_threshold", 5))
    min_tier = int(cfg.get("min_tier", 2))          # 1 = only top outlets, 2 = top+mid

    # 1) gather raw articles — ONE combined OR query, not one request per term:
    # Google News 503-throttles datacenter IPs when hit 8x every 5 minutes.
    # Retries with backoff because it still 503s intermittently even at 1/run.
    raw, last_err = [], None
    query = " OR ".join(f'"{t}"' for t in terms)
    for attempt in range(3):
        try:
            raw = _fetch_rss(query, limit=100)
            last_err = None
            break
        except Exception as e:
            last_err = e
            time.sleep(5 * (attempt + 1))
    if last_err:
        _note_error(f"[News] fetch error after 3 tries: {last_err}")
    # De-dupe identical links. Accept anything published within the pickup
    # window, NOT just since the last pull: Google News often surfaces an
    # article in search HOURS after its pubDate, and a strict last-hour date
    # filter permanently missed those late-indexed stories (the Axios miss,
    # 2026-07-09). Re-processing is idempotent: clusters absorb repeats and
    # emitted_tier stops re-emission.
    now = datetime.now(timezone.utc)
    date_floor = min(since, now - timedelta(hours=float(cfg.get("pickup_window_hours", 72))))
    seen_links, articles = set(), []
    for a in raw:
        if a["link"] in seen_links or a["date"] < date_floor:
            continue
        seen_links.add(a["link"])
        a["norm"] = _norm_title(a["title"])
        a["tier"] = _tier_of(a["outlet"], a["domain"], tier1, tier2)
        articles.append(a)

    # 2) merge into the PERSISTENT cluster state. Stories syndicate over hours
    # or days, so pickup must accumulate ACROSS pulls — clustering only within
    # one window meant "5 outlets in an hour", which only wire-service bursts
    # ever satisfied; organic pickup restarted from zero every pull.
    path = os.path.join(state_dir, _NEWS_CLUSTERS)
    horizon = now - timedelta(hours=float(cfg.get("pickup_window_hours", 72)))
    clusters = []
    for c in _load(path, []):
        try:
            if datetime.fromisoformat(c["last_seen"]) >= horizon:
                clusters.append(c)
        except Exception:
            continue
    for a in articles:
        placed = None
        for c in clusters:
            if _similar(a["norm"], c["norm"]) >= 0.6:
                placed = c
                break
        if placed is None:
            placed = {"norm": a["norm"], "outlets": {}, "best": None,
                      "first_seen": now.isoformat(), "emitted_tier": 99}
            clusters.append(placed)
        placed["last_seen"] = now.isoformat()
        if a["outlet"]:
            placed["outlets"][a["outlet"]] = a["date"].isoformat()
        if placed["best"] is None or a["tier"] < placed["best"]["tier"]:
            placed["best"] = {"title": a["title"], "link": a["link"],
                              "outlet": a["outlet"], "tier": a["tier"],
                              "date": a["date"].isoformat()}

    # 3) emit one Mention per cluster that qualifies and hasn't already been
    # emitted at this strength. emitted_tier prevents re-emitting on every new
    # small pickup, but a LOUDER event — a tier-1/2 outlet joining after a
    # small-outlet emission — emits again.
    out = []
    for c in clusters:
        best = c.get("best")
        if not best:
            continue
        pickup = len(c["outlets"])
        qualifies = (best["tier"] <= min_tier) or (pickup >= pickup_threshold)
        if not qualifies or best["tier"] >= c.get("emitted_tier", 99):
            continue
        c["emitted_tier"] = best["tier"]
        others = sorted(set(c["outlets"]) - {best["outlet"]})
        out.append(Mention(
            platform="news",
            id=best["link"],
            url=best["link"],
            text=re.sub(r"\s*[-–|]\s*[^-–|]+$", "", best["title"]),
            author=best["outlet"],
            author_name=best["outlet"],
            created_at=datetime.fromisoformat(best["date"]),
            engagement=pickup * 10 + (100 if best["tier"] == 1 else 40 if best["tier"] == 2 else 0),
            metrics={"outlet": best["outlet"], "tier": best["tier"],
                     "pickup_count": pickup, "also_covered": others[:10]},
        ))
    with open(path, "w") as f:
        json.dump(clusters, f)
    return out

# --------------------------------------------------------------------------
# Generic RSS / Atom sources — Substack & personal blogs (per watchlist person)
# and Reddit's PUBLIC search RSS (no app/credentials; the official API is now
# approval-gated). Both parse the same way.
# --------------------------------------------------------------------------
_FEED_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _feed_get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": _FEED_UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _feed_date(s):
    if not s:
        return None
    s = s.strip()
    dt = None
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M %z"):
        try:
            dt = datetime.strptime(s, fmt); break
        except Exception:
            pass
    if dt is None:
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_feed(raw):
    """Parse an RSS 2.0 (<item>) or Atom (<entry>) feed into simple dicts."""
    out = []
    try:
        root = ET.fromstring(raw)
    except Exception:
        return out
    tag = lambda el: el.tag.split("}")[-1]
    for it in root.iter():
        if tag(it) not in ("item", "entry"):
            continue
        d = {"title": "", "link": "", "author": "", "date": None, "summary": ""}
        for ch in it:
            ct = tag(ch)
            if ct == "title":
                d["title"] = (ch.text or "").strip()
            elif ct == "link" and not d["link"]:
                d["link"] = (ch.text or ch.get("href") or "").strip()
            elif ct in ("creator", "author") and not d["author"]:
                name = ""
                for sub in ch:
                    if tag(sub) == "name":
                        name = (sub.text or "").strip()
                d["author"] = name or (ch.text or "").strip()
            elif ct in ("pubDate", "published", "updated") and not d["date"]:
                d["date"] = _feed_date(ch.text)
            elif ct in ("summary", "description", "content") and not d["summary"]:
                d["summary"] = re.sub(r"<[^>]+>", " ", (ch.text or "")).strip()[:500]
        out.append(d)
    return out


def fetch_substack(feeds_map, since, limit=15):
    """One feed per watchlist person; author is forced to the person's name so
    the watchlist matcher fires (their importance is the signal)."""
    out = []
    for i, (name, url) in enumerate((feeds_map or {}).items()):
        try:
            items = _parse_feed(_feed_get(url))
        except Exception as e:
            # index only, never the name/URL — Actions logs on a public repo
            # are publicly viewable and must not reveal who is watched.
            print(f"  [Substack] feed #{i} error: {e}")
            continue
        for it in items[:limit]:
            if it["date"] and it["date"] < since:
                continue
            link = it["link"]
            out.append(Mention(
                platform="substack", id=link or (name + it["title"][:40]),
                url=link, text=(it["title"] + "\n" + it["summary"]).strip(),
                author=name, author_name=name, created_at=it["date"],
                metrics={"outlet": name, "source": "substack"}))
        time.sleep(0.2)
    return out


def fetch_reddit_rss(terms, since, limit=50):
    """Public Reddit search RSS — no credentials. ONE combined OR query keeps us
    under Reddit's aggressive RSS rate limit (per-term hammering hits 429).
    Engagement isn't exposed here, so these feed volume/sentiment/narrative
    signals rather than solo alerts."""
    out = []
    query = " OR ".join(f'"{t}"' for t in terms)
    q = urllib.parse.quote(query)
    url = f"https://www.reddit.com/search.rss?q={q}&sort=new&limit={limit}"
    try:
        items = _parse_feed(_feed_get(url))
    except Exception as e:
        print(f"  [Reddit-RSS] error: {e}")
        return out
    for it in items:
        if it["date"] and it["date"] < since:
            continue
        link = it["link"]
        author = (it["author"] or "").rsplit("/", 1)[-1]
        out.append(Mention(
            platform="reddit", id=link or it["title"][:60],
            url=link, text=(it["title"] + "\n" + it["summary"]).strip(),
            author=author, created_at=it["date"], engagement=0,
            metrics={"source": "reddit_rss"}))
    return out


# ===== from relevance.py =====




# --------------------------------------------------------------------------
# Stage 1: rule-based prefilter
# --------------------------------------------------------------------------
def prefilter(mentions, config):
    """Tag each mention with a confidence and drop obvious non-matches."""
    high_terms = [t["query"].lower() for t in config["search_terms"] if t["precision"] == "high"]
    low_terms = [t["query"].lower() for t in config["search_terms"] if t["precision"] == "low"]
    context = [c.lower() for c in config.get("context_terms", [])]
    blacklist = {h.lower().lstrip("@") for h in config.get("author_blacklist", [])}

    kept = []
    for m in mentions:
        if blacklist and (m.author or "").lower().lstrip("@") in blacklist:
            continue    # bot accounts (e.g. Grok) — never counted, never alerted
        if m.metrics.get("from_watchlist") or m.metrics.get("matched_link"):
            # Watchlist tweets and article-link shares skip the keyword gate —
            # a subtweet never names the project, and a "this is wild <link>"
            # share has no keywords either. The AI still vets scope.
            m.confidence = "medium"
            kept.append(m)
            continue
        if m.platform in ("news", "substack"):
            # News stories already passed the outlet/pickup gate, and substack
            # items come from watchlist feeds. Their TITLES often carry no
            # keyword ("He warned AI could lead to extinction..." — the WaPo
            # miss, 2026-07-09); the AI fetches the article body and judges
            # scope, so never keyword-drop these on the headline.
            m.confidence = "medium"
            kept.append(m)
            continue
        text = m.text.lower()
        if any(t in text for t in high_terms):
            m.confidence = "high"
            kept.append(m)
            continue
        if any(t in text for t in low_terms):
            if any(c in text for c in context):
                m.confidence = "medium"
            else:
                m.confidence = "low"     # ambiguous — let the AI decide later
            kept.append(m)
            continue
        # No search phrase in the visible text. Keep ONLY if it's a link-style
        # post whose article body we can still fetch and vet (e.g. an HN
        # submission that just links to an article); otherwise DROP it — this
        # is the fuzzy-search noise we don't want.
        if m.metrics.get("article_url"):
            m.confidence = "low"
            kept.append(m)
    return kept


# --------------------------------------------------------------------------
# Stage 2a: keyword fallback (used only if the AI classifier is disabled)
# --------------------------------------------------------------------------
_POS = ["great", "excellent", "impressive", "love", "brilliant", "important",
        "thoughtful", "excited", "endorse", "agree", "best", "must-read", "smart"]
_NEG = ["wrong", "bad", "terrible", "nonsense", "doomer", "fearmonger", "grift",
        "cult", "naive", "unrealistic", "debunked", "captured", "shill", "stupid",
        "hype", "overblown", "aged poorly", "didn't happen"]


def keyword_sentiment(m):
    t = m.text.lower()
    pos = sum(w in t for w in _POS)
    neg = sum(w in t for w in _NEG)
    if neg > pos:
        return "negative"
    if pos > neg:
        return "positive"
    return "neutral"


_THEME_BY_SENTIMENT = {"negative": "negative reactions",
                       "positive": "positive reactions",
                       "neutral": "neutral mentions"}


def keyword_fallback(mentions):
    for m in mentions:
        # Without the AI we can't truly verify relevance. We keep high- and
        # medium-confidence items (prefer false positives over misses), but
        # we DROP low-confidence ones: an ambiguous term like "Plan A" or
        # "AI 2027" with no context word nearby is exactly the Google-Alerts
        # noise we're trying to avoid, and there's no AI here to vet it.
        m.relevant = m.confidence in ("high", "medium")
        t = m.text.lower()
        m.about = ("plan_a" if any(k in t for k in ("ai 2040", "ai2040", "ai-2040", "plan a"))
                   else "aifp_other")
        # keyword matching can vouch for scope only when the term was explicit
        m.about_conf = "medium" if m.confidence == "high" else "low"
        m.sentiment = keyword_sentiment(m)
        m.theme = _THEME_BY_SENTIMENT.get(m.sentiment, "neutral mentions")
        m.summary = (m.text[:140] + "…") if len(m.text) > 140 else m.text
    return mentions


# --------------------------------------------------------------------------
# Stage 2b: AI classifier (Anthropic or OpenAI), batched
# --------------------------------------------------------------------------
_SYSTEM = (
    "You are a media-monitoring analyst for the AI Futures Project (AIFP), a "
    "nonprofit known for the viral 'AI 2027' scenario and its new publication "
    "'AI 2040: Plan A' (released early July 2026, ai-2040.com). Authors include "
    "Daniel Kokotajlo, Eli Lifland, Thomas Larsen, Scott Alexander. For each "
    "social-media post, classify its scope:\n"
    "- plan_a: explicitly about the AI 2040: Plan A publication — it names the "
    "title, links ai-2040.com, or unmistakably discusses this specific "
    "publication or its launch.\n"
    "- aifp_other: genuinely about AIFP, its people, or its other work (AI "
    "2027, the authors, their forecasts) but NOT clearly about the new "
    "publication.\n"
    "- unrelated: a coincidental match — the phrase used generically, a "
    "different person with the same name, or a post about something else "
    "entirely (e.g. a watched author praising some other lab's paper).\n"
    "Also report scope_confidence (high | medium | low): how sure you are of "
    "the scope label. Uncertain posts are kept for trend counting but never "
    "page a human, so honest low confidence beats a guess. Rules:\n"
    "- Never label plan_a without an explicit signal in the text or linked "
    "article; do not infer 'likely referring to Plan A' from vibes or from "
    "who wrote it.\n"
    "- When torn between aifp_other and unrelated, choose aifp_other with "
    "scope_confidence low (inclusion is cheap; paging people is not). Use "
    "unrelated only when you are actually confident the post has nothing to "
    "do with AIFP.\n"
    "- Posts marked matched=watchlist_feed come from a watched author's feed "
    "with NO keyword match: these need an explicit AIFP reference to be "
    "anything but unrelated. If you cannot tell what such a post refers to, "
    "use aifp_other with scope_confidence low — never plan_a.\n"
    "- Posts marked matched=article_link share a link to a press article "
    "already identified as AIFP coverage — the link itself is the evidence, "
    "so judge scope and sentiment from how the poster frames it.\n"
    "Then label sentiment and stance toward AIFP/its work, a short theme, and "
    "a one-sentence neutral summary. The summary must never assert a "
    "connection to Plan A that the post does not explicitly make.\n"
    "Sentiment rules — sentiment measures the post's attitude TOWARD AIFP and "
    "its work, not the post's general mood:\n"
    "- Mockery, satire, or dunking on the forecasts is negative even when "
    "playful or funny.\n"
    "- Sharing a link or reporting facts without commentary is neutral, even "
    "if the underlying news is bad.\n"
    "- Worry about AI risk in general is NOT negative toward AIFP; it is only "
    "negative if aimed at AIFP, its people, or its publications.\n"
    "- Praise of the work, endorsement, or 'must-read' framing is positive.\n"
    "If recent human corrections are listed in the prompt, treat them as "
    "precedents and follow them for similar posts."
)

_INSTR = (
    'Return ONLY a JSON array. For each post return an object: '
    '{"i": <index>, "about": "plan_a"|"aifp_other"|"unrelated", '
    '"scope_confidence": "high"|"medium"|"low", '
    '"sentiment": "positive"|"neutral"|"negative"|"mixed", '
    '"stance": "supportive"|"substantive_critique"|"dismissive"|"question"|"news_share"|"off_topic", '
    '"theme": "<3-6 word label>", "summary": "<one sentence, neutral>"}'
)


def _anthropic_call(api_key, model, prompt):
    payload = {
        "model": model,
        # generous cap: adaptive thinking spends output tokens before the JSON
        "max_tokens": 8000,
        "system": _SYSTEM,
        "messages": [{"role": "user", "content": prompt}],
    }
    if not model.startswith("claude-haiku"):
        # Sonnet 5 / Opus 4.8: adaptive thinking helps the sarcasm/satire
        # sentiment calls; Haiku 4.5 doesn't accept it
        payload["thinking"] = {"type": "adaptive"}
    body = json.dumps(payload).encode()
    for attempt in range(3):
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages", data=body,
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.load(r)
            break
        except urllib.error.HTTPError as e:
            # 529 = Anthropic overloaded, 429 = rate limit, 5xx = server blip:
            # all transient. Anything else (bad model id, bad key) is permanent
            # and retrying would just delay the real error.
            if e.code not in (429, 500, 502, 503, 504, 529) or attempt == 2:
                # surface the API's own error message — "HTTP Error 400: Bad
                # Request" alone can't distinguish a bad parameter from an
                # exhausted credit balance or an oversized prompt
                try:
                    detail = json.loads(e.read().decode("utf-8", "ignore"))["error"]["message"]
                except Exception:
                    detail = ""
                msg = f"{e.reason} — {detail[:300]}" if detail else e.reason
                raise urllib.error.HTTPError(e.url, e.code, msg, e.headers, None) from None
            time.sleep(10 * (attempt + 1))
        except OSError:   # connection reset / timeout
            if attempt == 2:
                raise
            time.sleep(10 * (attempt + 1))
    # concatenate the text blocks — content[0] isn't guaranteed to be text
    # (e.g. a thinking block comes first on some models)
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


def _openai_call(api_key, model, prompt):
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": _SYSTEM},
                     {"role": "user", "content": prompt}],
        "temperature": 0,
    }).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {api_key}", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.load(r)
    return data["choices"][0]["message"]["content"]


def _extract_json(text):
    start = text.find("[")
    if start < 0:
        return []
    s = text[start:]
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    # tolerate prose after the array ("...]  Hope that helps!")
    m = re.search(r"\[.*\]", s, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    # truncated or garbled tail: salvage the complete leading objects so a
    # one-character glitch doesn't throw away the whole batch's labels
    last = s.rfind("}")
    if last > 0:
        return json.loads(s[:last + 1] + "]")
    raise json.JSONDecodeError("no parseable JSON array in reply", s, 0)


def fetch_article_text(url, limit=2000):
    """Best-effort plain-text grab of an article so the AI judges on content,
    not just a headline. Crude HTML strip; returns '' on any failure."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 plan-a-monitor"})
        with urllib.request.urlopen(req, timeout=10) as r:
            html = r.read(400_000).decode("utf-8", "ignore")
    except Exception:
        return ""
    html = re.sub(r"(?is)<(script|style|nav|header|footer).*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    return text.strip()[:limit]


def ai_classify(mentions, llm_cfg):
    provider = llm_cfg.get("provider", "anthropic")
    model = llm_cfg["model"]
    escalate_model = llm_cfg.get("escalate_model", "")
    api_key = llm_cfg["api_key"]
    batch = int(llm_cfg.get("batch_size", 15))
    get_article = llm_cfg.get("fetch_article_text", True)

    # For news + HN link-submissions, pull the article body so relevance and
    # sentiment are based on the real content (fixes headline-only ambiguity).
    if get_article:
        for m in mentions:
            url = m.metrics.get("article_url") if m.platform == "hackernews" else (
                m.url if m.platform in ("news", "substack") else None)
            if url:
                body = fetch_article_text(url)
                if body:
                    m.metrics["article_excerpt"] = body[:1200]

    # Human re-tags become precedents the model sees (see apply_retags)
    _ex = _load(os.path.join(".", _RETAG), {}).get("examples", [])
    _fewshot = ""
    if _ex:
        _fewshot = ("\nRecent human corrections — follow these precedents for similar posts:\n"
                    + "\n".join(f'- "{c.get("summary", "")}" -> about={c.get("about") or "?"}, '
                                f'sentiment={c.get("sentiment")}' for c in _ex[-6:]) + "\n")

    def _label(items, use_model, fallback=True):
        """Classify a list of mentions in batches with the given model."""
        for start in range(0, len(items), batch):
            chunk = items[start:start + batch]
            posts = [{"i": i, "platform": m.platform, "author": m.author or m.author_name,
                      # watchlist-feed posts carry no keyword evidence at all;
                      # the prompt holds those to the strictest scope standard.
                      # article_link posts share a known-relevant article — the
                      # link itself is the evidence.
                      "matched": ("watchlist_feed" if (m.metrics.get("from_watchlist")
                                                       or m.platform == "substack")
                                  else "article_link" if m.metrics.get("matched_link")
                                  else "keyword"),
                      "text": m.text[:600],
                      "article_excerpt": m.metrics.get("article_excerpt", "")[:900]}
                     for i, m in enumerate(chunk)]
            prompt = (f"{_INSTR}\n{_fewshot}\nPosts:\n{json.dumps(posts, ensure_ascii=False)}")
            results = None
            for attempt in range(2):
                try:
                    raw = (_anthropic_call if provider == "anthropic" else _openai_call)(api_key, use_model, prompt)
                    results = {r["i"]: r for r in _extract_json(raw)}
                    break
                except Exception as e:
                    # a malformed-JSON reply is a bad sample, not a broken API —
                    # one fresh attempt usually parses; then fall back
                    if attempt == 0:
                        continue
                    if fallback:
                        _note_error(f"[AI] batch failed on model '{use_model}' ({e}); "
                                    f"keyword fallback for {len(chunk)} item(s).")
                        keyword_fallback(chunk)
                    else:
                        # escalation pass: on failure KEEP the first-pass labels —
                        # never downgrade an already-classified item to keywords
                        _note_error(f"[AI] escalation failed on '{use_model}' ({e}); "
                                    f"keeping first-pass labels for {len(chunk)} item(s).")
            if results is None:
                continue
            for i, m in enumerate(chunk):
                r = results.get(i)
                if not r:
                    m.relevant, m.sentiment = True, keyword_sentiment(m)
                    m.about = m.about or "aifp_other"
                    m.summary = m.text[:140]
                    continue
                about = r.get("about")
                if about not in ("plan_a", "aifp_other", "unrelated"):
                    # legacy/degenerate answer — fall back to the old boolean
                    about = "aifp_other" if r.get("about_aifp", True) else "unrelated"
                m.about = about
                conf = r.get("scope_confidence")
                m.about_conf = conf if conf in ("high", "medium", "low") else "medium"
                m.relevant = about != "unrelated"
                m.sentiment = r.get("sentiment", "neutral")
                m.stance = r.get("stance", "")
                m.theme = r.get("theme", "")
                m.summary = r.get("summary", "")

    # Pass 1 — cheap, fast model over everything.
    _label(mentions, model)

    # Pass 2 — re-judge the high-stakes items with the stronger model: what
    # drives CRITICAL/WARNING alerts (negatives, critiques), everything from
    # watchlist feeds (a scope mistake there pings humans directly), and
    # anything the cheap model wasn't confident about — including posts it
    # wanted to DROP, since dropping also requires confidence.
    if escalate_model and escalate_model not in ("", "FILL IN"):
        escalate = [m for m in mentions if
                    (m.relevant and (m.sentiment in ("negative", "mixed")
                                     or m.stance == "substantive_critique"
                                     or m.metrics.get("from_watchlist")
                                     or m.platform == "substack"))
                    or m.about_conf == "low"]
        if escalate:
            print(f"  [AI] escalating {len(escalate)} item(s) to {escalate_model}.")
            _label(escalate, escalate_model, fallback=False)
    return mentions


def classify(mentions, config):
    """Run prefilter then either AI or keyword labeling."""
    kept = prefilter(mentions, config)
    llm_used = bool(config.get("llm", {}).get("enabled")
                    and config["llm"].get("api_key", "") not in ("", "FILL IN"))
    if llm_used:
        ai_classify(kept, config["llm"])
    else:
        keyword_fallback(kept)
    # Keep the relevant ones. Dropping requires CONFIDENCE: if even the
    # escalation model wasn't sure a post is unrelated, keep it for counting
    # (as adjacent) — the low confidence itself blocks it from ever paging.
    out = []
    for m in kept:
        if m.relevant:
            out.append(m)
        elif llm_used and m.about_conf == "low":
            m.about, m.relevant = "aifp_other", True
            out.append(m)
    return out

# ===== from analyze.py =====




# --------------------------------------------------------------------------
# Watchlist matching
# --------------------------------------------------------------------------
def _watch_hit(m, watchlist):
    a = (m.author or "").lower().lstrip("@")
    name = (m.author_name or "").lower()
    for w in watchlist:
        handles = [h.lower().lstrip("@") for h in w.get("handles", [])]
        if a and a in handles:
            return w
        # Display-name match requires the FULL watchlist name on word
        # boundaries (catches "Michael Kratsios 🇺🇸", not a random account
        # whose display name is a fragment like "t" — that substring match
        # paged the team about a 2-impression tweet on 2026-07-20).
        if name and re.search(r"\b" + re.escape(w["name"].lower()) + r"\b", name):
            return w
    return None


def _watch_featured(m, watchlist):
    """Watchlist person featured IN the content (full name in the title/text/
    article excerpt) rather than authoring it. Only for article-ish platforms —
    news 'authors' are outlets, so a watched columnist's op-ed or a piece
    quoting a watched official would otherwise never match the watchlist.
    X and Substack already match by author."""
    if m.platform not in ("news", "hackernews", "reddit"):
        return None
    text = (m.text + " " + m.metrics.get("article_excerpt", "")).lower()
    for w in watchlist:
        if re.search(r"\b" + re.escape(w["name"].lower()) + r"\b", text):
            return w
    return None


# --------------------------------------------------------------------------
# Per-post priority + tier
# --------------------------------------------------------------------------
def score_mentions(mentions, config):
    th = config["thresholds"]
    watchlist = config.get("watchlist", [])
    for m in mentions:
        score = float(m.engagement)
        tier = "low"

        # platform-specific "notable on its own" bars
        p = m.metrics
        if m.platform == "twitter":
            if p.get("likes", 0) >= th["twitter"]["likes"] or p.get("reposts", 0) >= th["twitter"]["reposts"] \
               or (m.author_followers or 0) >= th["twitter"]["author_followers"] \
               or p.get("impressions", 0) >= th["twitter"].get("impressions", 10**12):
                tier = "high"
        elif m.platform == "reddit":
            if p.get("score", 0) >= th["reddit"]["score"] or p.get("num_comments", 0) >= th["reddit"]["num_comments"]:
                tier = "high"
        elif m.platform == "hackernews":
            if p.get("points", 0) >= th["hackernews"]["points"] or p.get("num_comments", 0) >= th["hackernews"]["num_comments"]:
                tier = "high"
        elif m.platform == "news":
            # already passed the big-outlet / syndication bar in news.py
            tier = "critical" if p.get("tier") == 1 else "high"

        # substantive critique worth a look even if smallish — but it must have
        # SOME traction (a dozen interactions, or a genuinely big author), or
        # every zero-engagement hot take pings the team. Small critiques still
        # count toward surges/narratives/dashboard regardless.
        if m.stance == "substantive_critique" and tier == "low" and (
                m.engagement >= 10 or (m.author_followers or 0) >= 100000):
            tier = "high"

        # watchlist authors override everything
        w = _watch_hit(m, watchlist)
        if w:
            tier = "critical" if w.get("weight") == "critical" else "high"
            score += 10000 if w.get("weight") == "critical" else 5000
            m.summary = f"[{w['name']}] " + (m.summary or m.text[:120])
        else:
            # …and an article/post that FEATURES a watchlist person is nearly
            # as important as one they wrote.
            fw = _watch_featured(m, watchlist)
            if fw:
                tier = "critical" if fw.get("weight") == "critical" else "high"
                score += 5000 if fw.get("weight") == "critical" else 2500
                m.summary = f"[features {fw['name']}] " + (m.summary or m.text[:120])

        # negative posts from anyone get a small bump (we care about these more)
        if m.sentiment == "negative":
            score += 50

        m.priority = score
        m.tier = tier
    return mentions


# --------------------------------------------------------------------------
# Volume + theme aggregation (counts EVERY relevant mention, big or small)
# --------------------------------------------------------------------------
def aggregate(mentions, config):
    by_platform = Counter(m.platform for m in mentions)
    by_sentiment = Counter(m.sentiment for m in mentions)

    # theme clusters: use AI theme if present, else fall back to stance
    themes = defaultdict(list)
    for m in mentions:
        key = (m.theme or m.stance or "uncategorized").strip().lower()
        themes[key].append(m)

    theme_rows = []
    for key, items in themes.items():
        sents = Counter(i.sentiment for i in items)
        n = len(items)
        neg = sents.get("negative", 0)
        # representative = highest-engagement example in the cluster
        rep = max(items, key=lambda x: x.engagement)
        theme_rows.append({
            "theme": key,
            "count": n,
            "neg_pct": round(100 * neg / n) if n else 0,
            "sentiment_breakdown": dict(sents),
            "example_url": rep.url,
            "example_text": (rep.text[:160] + "…") if len(rep.text) > 160 else rep.text,
            "mostly_small": rep.engagement < 10,   # flag low-engagement-but-loud clusters
        })
    theme_rows.sort(key=lambda r: r["count"], reverse=True)

    # narrative watch
    narrative_hits = []
    for nar in config.get("narratives", []):
        kws = [k.lower() for k in nar["keywords"]]
        hits = [m for m in mentions if any(k in m.text.lower() for k in kws)]
        if hits:
            narrative_hits.append({"label": nar["label"], "count": len(hits),
                                   "alert": len(hits) >= nar.get("alert_count", 999),
                                   "example_url": max(hits, key=lambda x: x.engagement).url})

    return {
        "total": len(mentions),
        "by_platform": dict(by_platform),
        "by_sentiment": dict(by_sentiment),
        "themes": theme_rows,
        "narratives": narrative_hits,
    }


def detect_spike(current_total, baseline_avg, factor, min_mentions=10):
    """A spike needs real volume, not just a big ratio: in quiet periods the
    baseline sits near zero, and 2 mentions against a 0.08 average is '24x
    normal' — mathematically true, editorially garbage. The baseline is also
    floored at 1 so the ratio can't explode."""
    if current_total < min_mentions:
        return False, 0.0
    ratio = current_total / max(baseline_avg, 1.0)
    return ratio >= factor, round(ratio, 1)


def compute_triggers(new_mentions, agg, spike, spike_ratio, config):
    """Decide whether this run is 'noteworthy'. In alert mode we stay silent
    unless at least one trigger fires. Returns (triggered, reasons, items).

    Triggers (the two things you named, plus spike):
      1. A single post/article getting real attention (tier critical/high).
      2. A pattern of many small posts (a theme cluster crossing a size bar).
      3. A volume spike vs. the normal rate.
      4. A configured 'narrative watch' storyline crossing its threshold.
    """
    cluster_alert = int(config.get("cluster_alert_count", 6))
    reasons = []

    individual = [m for m in new_mentions if m.tier in ("critical", "high")]
    if individual:
        reasons.append(f"{len(individual)} high-attention post(s)/article(s)")

    pattern_themes = [t for t in agg["themes"]
                      if t["count"] >= cluster_alert and t["mostly_small"]]
    for t in pattern_themes:
        reasons.append(f"pattern: '{t['theme']}' ({t['count']} small posts, {t['neg_pct']}% neg)")

    if spike:
        reasons.append(f"volume spike {spike_ratio}x normal")

    # Negativity surge — "a lot of people are suddenly saying bad things."
    # This fires on sentiment, independently of raw volume, and pulls the
    # negative posts into view even if individually small.
    neg_mentions = [m for m in new_mentions if m.sentiment == "negative"]
    if len(neg_mentions) >= int(config.get("neg_surge_count", 5)):
        reasons.append(f"⚠️ negativity surge: {len(neg_mentions)} negative mentions")
        for m in neg_mentions:
            if m not in individual:
                individual.append(m)

    fired_narratives = [n for n in agg["narratives"] if n["alert"]]
    for n in fired_narratives:
        reasons.append(f"narrative '{n['label']}' ({n['count']})")

    triggered = bool(reasons)
    return triggered, reasons, individual, pattern_themes, fired_narratives

# ===== from store.py =====



_SEEN = "seen_ids.json"
_HIST = "run_history.json"
_ALERTED = "alerted.json"
_TS = "timeseries.json"
_WARNED = "warned.json"          # narrative-build cooldowns
_DIGEST = "digest_state.json"    # last daily-digest date
_HEALTH = "health_state.json"    # last health-ping signature (repeat suppression)
_MENTIONS = "mentions.json"      # running log of every relevant mention (public)
_NEWS_CLUSTERS = "news_clusters.json"  # cross-run story clusters (pickup accumulates over days)
_RETAG = "retag_state.json"      # applied human re-tags + precedent examples for the classifier


def _load(path, default):
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            return default
    return default


def filter_new(mentions, state_dir="."):
    os.makedirs(state_dir, exist_ok=True)
    path = os.path.join(state_dir, _SEEN)
    seen = set(_load(path, []))
    fresh = [m for m in mentions if f"{m.platform}:{m.id}" not in seen]
    for m in mentions:
        seen.add(f"{m.platform}:{m.id}")
    # keep the file from growing forever
    seen = set(list(seen)[-50000:])
    with open(path, "w") as f:
        json.dump(sorted(seen), f)
    return fresh


def baseline_and_record(current_total, narrative_counts=None, state_dir="."):
    """Return the rolling average of recent runs, then record this run
    (including per-narrative counts, which feed the multi-day build detector)."""
    os.makedirs(state_dir, exist_ok=True)
    path = os.path.join(state_dir, _HIST)
    hist = _load(path, [])
    prev = [h["total"] for h in hist[-12:]]          # last ~12 runs
    avg = (sum(prev) / len(prev)) if prev else 0.0
    hist.append({"ts": datetime.now(timezone.utc).isoformat(), "total": current_total,
                 "narratives": narrative_counts or {}})
    hist = hist[-2000:]          # ≈1 week at the 5-min cadence
    with open(path, "w") as f:
        json.dump(hist, f)
    return avg


def narrative_build_hits(config, state_dir="."):
    """Slow-burn detector: a narrative that accumulates mentions over DAYS even
    though no single 5-min window ever crosses its per-run alert_count. Sums
    the per-run counts recorded in run history over the last
    narrative_build_days; a cooldown stops it re-warning every run."""
    hist = _load(os.path.join(state_dir, _HIST), [])
    now = datetime.now(timezone.utc)
    default_days = int(config.get("narrative_build_days", 3))
    cooldown = timedelta(hours=float(config.get("narrative_build_cooldown_hours", 12)))
    warned = _load(os.path.join(state_dir, _WARNED), {})
    hits = []
    for nar in config.get("narratives", []):
        label = nar["label"]
        days = int(nar.get("build_days", default_days))
        target = int(nar.get("build_count", 3 * int(nar.get("alert_count", 8))))
        cutoff = now - timedelta(days=days)
        total = 0
        for h in hist:
            try:
                if datetime.fromisoformat(h["ts"]) >= cutoff:
                    total += int(h.get("narratives", {}).get(label, 0))
            except Exception:
                continue
        if total < target:
            continue
        last = warned.get(label)
        try:
            if last and now - datetime.fromisoformat(last) < cooldown:
                continue
        except Exception:
            pass
        warned[label] = now.isoformat()
        hits.append({"label": label, "count": total, "days": days})
    if hits:
        with open(os.path.join(state_dir, _WARNED), "w") as f:
            json.dump(warned, f)
    return hits


def record_timeseries(agg, new_mentions, state_dir="."):
    """Append one data point per run for the live dashboard: counts by
    sentiment + platform, top themes, and a few notable items."""
    os.makedirs(state_dir, exist_ok=True)
    path = os.path.join(state_dir, _TS)
    ts = _load(path, [])
    sent = agg["by_sentiment"]
    notable = sorted([m for m in new_mentions if m.tier in ("critical", "high")],
                     key=lambda m: m.priority, reverse=True)[:8]
    ts.append({
        "ts": datetime.now(timezone.utc).isoformat(),
        "total": agg["total"],
        "positive": sent.get("positive", 0),
        "negative": sent.get("negative", 0),
        "neutral": sent.get("neutral", 0) + sent.get("mixed", 0),
        "by_platform": agg["by_platform"],
        "themes": [{"theme": t["theme"], "count": t["count"], "neg_pct": t["neg_pct"]}
                   for t in agg["themes"][:5]],
        # strip the [Name]/[features Name] watchlist tags — this file feeds the
        # PUBLIC dashboard and must not reveal who is on the watchlist
        "notable": [{"platform": m.platform, "author": m.author or m.author_name,
                     "sentiment": m.sentiment, "url": m.url,
                     "summary": re.sub(r"^\[[^\]]{0,80}\]\s*", "", m.summary or m.text[:140])}
                    for m in notable],
    })
    ts = ts[-5000:]
    with open(path, "w") as f:
        json.dump(ts, f)
    return ts


def record_mentions(new_mentions, state_dir="."):
    """Append EVERY relevant mention, big or small, to a running log — the
    team's 'find it and respond' queue (mentions.html reads this). The file is
    public, so watchlist tags are stripped from summaries."""
    if not new_mentions:
        return
    path = os.path.join(state_dir, _MENTIONS)
    log = _load(path, [])
    for m in new_mentions:
        log.append({
            "ts": (m.created_at or datetime.now(timezone.utc)).isoformat(),
            "platform": m.platform,
            "author": m.author or m.author_name,
            "sentiment": m.sentiment,
            "tier": m.tier,
            "about": m.about or "plan_a",
            "engagement": m.engagement,
            "url": m.url,
            "summary": re.sub(r"^\[[^\]]{0,80}\]\s*", "", m.summary or m.text[:160]),
        })
    log = log[-3000:]
    with open(path, "w") as f:
        json.dump(log, f)


def apply_retags(config, state_dir="."):
    """Pull human re-tags from the published Google Sheet CSV (fed by the
    'fix tag' form linked from mentions.html) and apply them to the mention
    log. Recent corrections are also kept as precedent examples that get
    shown to the classifier, so re-tagging teaches it."""
    url = config.get("retag", {}).get("sheet_csv_url", "")
    if not url or "PASTE" in url:
        return
    import csv as _csv, io as _io, hashlib as _hashlib
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "plan-a-monitor/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            rows = list(_csv.reader(_io.StringIO(r.read().decode("utf-8", "ignore"))))
    except Exception as e:
        print(f"  [retag] sheet fetch failed: {e}")
        return
    if len(rows) < 2:
        return
    header = [h.strip().lower() for h in rows[0]]

    def col(*words):
        for i, h in enumerate(header):
            if any(w in h for w in words):
                return i
        return None

    c_url, c_sent, c_scope = col("url", "link"), col("sentiment"), col("scope", "about")
    if c_url is None:
        print("  [retag] no URL column in the sheet — check the form's questions.")
        return
    state = _load(os.path.join(state_dir, _RETAG), {"done": [], "examples": []})
    mlog = _load(os.path.join(state_dir, _MENTIONS), [])
    sent_words = ("positive", "neutral", "negative", "mixed")
    scope_map = {"unrelated": "unrelated", "adjacent": "aifp_other", "aifp": "aifp_other",
                 "plan": "plan_a"}
    changed = 0
    for row in rows[1:]:
        if not row:
            continue
        rid = _hashlib.sha1("|".join(row).encode()).hexdigest()[:16]
        if rid in state["done"]:
            continue
        state["done"].append(rid)
        target = row[c_url].strip() if c_url < len(row) else ""
        cell = lambda c: row[c].lower() if (c is not None and c < len(row)) else ""
        new_sent = next((w for w in sent_words if w in cell(c_sent)), None)
        new_scope = next((v for k, v in scope_map.items() if k in cell(c_scope)), None)
        if not target or (not new_sent and not new_scope):
            continue
        for e in mlog:
            if e.get("url") == target:
                if new_sent:
                    e["sentiment"] = new_sent
                if new_scope:
                    e["about"] = new_scope
                e["corrected"] = True
                state["examples"].append({"summary": (e.get("summary") or "")[:120],
                                          "sentiment": e["sentiment"],
                                          "about": e.get("about", "")})
                changed += 1
                break
    state["done"] = state["done"][-2000:]
    state["examples"] = state["examples"][-10:]
    with open(os.path.join(state_dir, _RETAG), "w") as f:
        json.dump(state, f)
    if changed:
        with open(os.path.join(state_dir, _MENTIONS), "w") as f:
            json.dump(mlog, f)
        print(f"  [retag] applied {changed} human correction(s).")


def filter_unalerted(individual, state_dir=".", escalate_factor=4.0):
    """Don't re-alert the same individual post every cycle. Re-alert only if
    its engagement has grown a lot since we last flagged it (escalation)."""
    os.makedirs(state_dir, exist_ok=True)
    path = os.path.join(state_dir, _ALERTED)
    alerted = _load(path, {})
    fresh = []
    for m in individual:
        key = f"{m.platform}:{m.id}"
        prev = alerted.get(key)
        if prev is None or m.engagement >= max(prev * escalate_factor, prev + 50):
            fresh.append(m)
            alerted[key] = m.engagement
    # trim
    if len(alerted) > 20000:
        alerted = dict(list(alerted.items())[-20000:])
    with open(path, "w") as f:
        json.dump(alerted, f)
    return fresh

# ===== from notify.py =====



_EMOJI = {"positive": "🟢", "negative": "🔴", "neutral": "⚪", "mixed": "🟡", "unknown": "⚪"}
_TIER = {"critical": "🔴", "high": "🟡", "low": "⚪"}


def _datestr(m):
    return m.created_at.strftime("%b %-d") if m.created_at else ""


def _engagement_str(m):
    p = m.metrics
    if m.platform == "twitter":
        f = f", {m.author_followers:,} followers" if m.author_followers else ""
        imp = f", {p.get('impressions',0):,} impressions" if p.get('impressions') else ""
        return f"{p.get('likes',0)} likes / {p.get('reposts',0)} reposts{imp}{f}"
    if m.platform == "reddit":
        return f"{p.get('score',0)} upvotes / {p.get('num_comments',0)} comments (r/{p.get('subreddit','')})"
    if m.platform == "hackernews":
        return f"{p.get('points',0)} points / {p.get('num_comments',0)} comments"
    if m.platform == "news":
        tier = {1: "tier-1", 2: "tier-2"}.get(p.get("tier"), "other")
        extra = f", +{p.get('pickup_count',1)-1} more outlets" if p.get("pickup_count", 1) > 1 else ""
        return f"{p.get('outlet','')} ({tier}){extra}"
    if m.platform == "substack":
        return f"{p.get('outlet','')} · blog/substack"
    return f"{m.engagement} engagement"


def _item_line(m):
    """One consistent block per item, with date and (for HN link posts) the
    underlying article so a bare link-submission reads as an article, not just
    a discussion thread."""
    who = m.author or m.author_name or "unknown"
    date = _datestr(m)
    # ONE dot per item (sentiment) — the message's own tier/color says how
    # urgent it is; a second dot per line read as confusing noise in Slack.
    head = (f"{_EMOJI.get(m.sentiment,'⚪')} *{m.platform}* · {who} · "
            f"{_engagement_str(m)}" + (f" · {date}" if date else ""))
    body = f"   {m.summary or m.text[:150]}"
    art = m.metrics.get("article_url")
    dom = m.metrics.get("domain")
    if m.platform == "hackernews" and art and dom:
        links = f"   📰 <{art}|{dom} article> · <{m.url}|HN discussion>"
    else:
        links = f"   <{m.url}|view>"
    return head + "\n" + body + "\n" + links


def build_digest(new_mentions, agg, spike, spike_ratio, baseline, title):
    lines = []
    sent = agg["by_sentiment"]
    plat = agg["by_platform"]
    lines.append(f"*{title}*")
    plat_str = ", ".join(f"{k}: {v}" for k, v in plat.items()) or "none"
    lines.append(f"*{agg['total']} new mentions*  ({plat_str})")
    lines.append(f"Sentiment — 🟢 {sent.get('positive',0)} · ⚪ {sent.get('neutral',0)} "
                 f"· 🔴 {sent.get('negative',0)} · 🟡 {sent.get('mixed',0)}")

    alerts = []
    if spike:
        alerts.append(f"📈 *Volume spike*: {agg['total']} mentions this window "
                      f"≈ {spike_ratio}× the recent average ({baseline:.0f}).")
    for nar in agg["narratives"]:
        if nar["alert"]:
            alerts.append(f"🚨 *Narrative '{nar['label']}'* gaining traction: "
                          f"{nar['count']} mentions. <{nar['example_url']}|example>")
    if alerts:
        lines.append("\n*⚠️ ALERTS*")
        lines += alerts

    notable = sorted([m for m in new_mentions if m.tier in ("critical", "high")],
                     key=lambda m: m.priority, reverse=True)[:15]
    if notable:
        lines.append("\n*🔎 Posts that need your eyes*")
        for m in notable:
            lines.append(_item_line(m))

    # Volume section: only show clusters that actually represent MORE THAN ONE
    # post — never present a single item as an aggregated "pattern".
    clusters = [t for t in agg["themes"] if t["count"] >= 2]
    if clusters:
        lines.append("\n*📊 What people are saying (by volume)*")
        for t in clusters[:8]:
            tag = " · 💬 mostly small accounts" if t["mostly_small"] and t["count"] >= 5 else ""
            lines.append(f"• *{t['theme']}* — {t['count']} mentions, {t['neg_pct']}% negative{tag}\n"
                         f"   e.g. <{t['example_url']}|“{t['example_text'][:90]}”>")
    return "\n".join(lines)


def build_alert(reasons, individual, pattern_themes, narratives, title):
    lines = [f"*🔔 {title}*", "_" + "; ".join(reasons) + "_"]
    if individual:
        lines.append("\n*Posts/articles getting attention*")
        for m in sorted(individual, key=lambda x: x.priority, reverse=True)[:12]:
            lines.append(_item_line(m))
    # Only describe "patterns" when there's an actual cluster of several posts.
    real_patterns = [t for t in pattern_themes if t["count"] >= 2]
    if real_patterns:
        lines.append("\n*Patterns building (lots of small posts)*")
        for t in real_patterns:
            lines.append(f"• *{t['theme']}* — {t['count']} posts, {t['neg_pct']}% negative "
                         f"<{t['example_url']}|example>")
    if narratives:
        lines.append("\n*Narrative watch*")
        for n in narratives:
            lines.append(f"🚨 *{n['label']}* — {n['count']} mentions <{n['example_url']}|example>")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Tiered alert routing — the CRITICAL / WARNING / heads-up scheme.
# who × sentiment -> tier.  color = Slack attachment sidebar; ping = who to @.
# --------------------------------------------------------------------------
TIER_STYLE = {
    "critical": {"label": "CRITICAL", "emoji": "🔴", "color": "#C0392B", "ping": "channel"},
    "warning":  {"label": "WARNING",  "emoji": "🟠", "color": "#E67E22", "ping": "responders"},
    "positive": {"label": "Positive — influential", "emoji": "🟢", "color": "#2ECC71", "ping": "responders"},
    "neutral":  {"label": "Heads-up — key figure",  "emoji": "⚪", "color": "#B0B0B0", "ping": "responders"},
}
_TIER_ORDER = ["critical", "warning", "positive", "neutral"]


def _individual_tier(m, watchlist, scope="all_aifp"):
    """Tier for a post that stands on its own — a watchlist author, or a post
    that cleared the attention bar (score_mentions set tier high/critical).
    Returns None if it isn't alert-worthy alone (it still feeds the aggregates).
    Two gates before anything can page a human:
    - confidence: a post whose relevance even the escalation model wasn't
      sure about never pings (it still counts everywhere else);
    - scope: with alert_scope 'plan_a', adjacent-AIFP posts don't ping either.
      Default 'all_aifp' lets confidently-adjacent posts page when big enough."""
    if m.about_conf == "low":
        return None
    if scope == "plan_a" and m.about == "aifp_other":
        return None
    key_figure = _watch_hit(m, watchlist) is not None or m.tier in ("critical", "high")
    if not key_figure:
        return None
    if m.sentiment in ("negative", "mixed"):
        return "critical"
    if m.sentiment == "positive":
        return "positive"
    return "neutral"


def _pings(tier_key, responders):
    if TIER_STYLE[tier_key]["ping"] == "channel":
        return "<!channel>"
    return " ".join(f"<@{u}>" for u in responders)


def _tier_payload(tier_key, items, reasons, responders, title):
    style = TIER_STYLE[tier_key]
    header = f"{_pings(tier_key, responders)} {style['emoji']} *{style['label']}* — {title}".strip()
    lines = []
    if reasons:
        lines.append("_" + "; ".join(reasons) + "_")
    for m in sorted(items, key=lambda x: x.priority, reverse=True)[:12]:
        lines.append(_item_line(m))
    if len(items) > 12:
        lines.append(f"…and {len(items) - 12} more.")
    body = "\n".join(lines) if lines else "_(no example posts)_"
    return {
        "text": header,  # the @-mention must live in top-level text to notify
        "attachments": [{"color": style["color"], "text": body,
                         "mrkdwn_in": ["text"], "fallback": style["label"]}],
    }


def build_tier_payloads(new, agg, spike, ratio, config, state_dir="."):
    """One batched, colour-coded Slack message per fired tier; empty list (i.e.
    stay silent) when nothing fires."""
    watchlist = config.get("watchlist", [])
    responders = config.get("slack", {}).get("respond_notify", [])
    title = config.get("slack", {}).get("digest_title", "Plan A monitor")
    scope = config.get("alert_scope", "all_aifp")

    # 1) individual posts that alert on their own, deduped so we don't re-ping.
    alertable = [m for m in new if _individual_tier(m, watchlist, scope)]
    alertable = filter_unalerted(alertable, state_dir, config.get("escalate_factor", 4.0))
    by_tier = defaultdict(list)
    for m in alertable:
        by_tier[_individual_tier(m, watchlist, scope)].append(m)

    # 2) aggregate negativity -> WARNING (surge / narrative / small-post cluster
    #    / spike). Examples shown are the SMALL negatives; the big ones already
    #    went out as CRITICAL, so nothing double-posts.
    warn_reasons = []
    negs = [m for m in new if m.sentiment in ("negative", "mixed")]
    n_neg, total = len(negs), max(agg.get("total", 0), 1)
    neg_share = n_neg / total

    # Negativity surge: needs BOTH enough negatives AND negatives taking over
    # the window — the share test keeps a loud-but-proportionate launch day
    # from warning every 5 minutes, while a real sentiment shift still fires.
    # A cooldown stops back-to-back re-warns for the same ongoing bad cycle;
    # CRITICAL single-post alerts are unaffected by it.
    if n_neg >= int(config.get("neg_surge_count", 5)) and \
       neg_share >= float(config.get("neg_surge_share", 0.4)):
        wpath = os.path.join(state_dir, _WARNED)
        warned = _load(wpath, {})
        now = datetime.now(timezone.utc)
        cooled = False
        try:
            cooled = (now - datetime.fromisoformat(warned["_neg_surge"])) < timedelta(
                hours=float(config.get("neg_surge_cooldown_hours", 2)))
        except Exception:
            pass
        if cooled:
            print("Negativity surge still active — warning suppressed (cooldown).")
        else:
            warn_reasons.append(f"negativity surge — {n_neg} of {agg['total']} mentions "
                                f"negative ({neg_share:.0%})")
            warned["_neg_surge"] = now.isoformat()
            with open(wpath, "w") as f:
                json.dump(warned, f)

    for n in agg.get("narratives", []):
        if n.get("alert"):
            warn_reasons.append(f"narrative '{n['label']}' — {n['count']} mentions")
    for b in narrative_build_hits(config, state_dir):
        warn_reasons.append(f"narrative '{b['label']}' BUILDING — {b['count']} mentions over {b['days']} days")
    # Small-post pattern clusters warn only when MOSTLY NEGATIVE — on a launch
    # day every window has 6+ small posts on some theme, and positive/neutral
    # chatter is dashboard material, not a page. Per-theme cooldown like the
    # surge's, so a persistent hostile theme warns once per cycle, not per run.
    cluster_bar = int(config.get("cluster_alert_count", 6))
    min_neg_pct = 100 * float(config.get("neg_surge_share", 0.4))
    for t in agg.get("themes", []):
        if t["count"] >= cluster_bar and t.get("mostly_small") and t["neg_pct"] >= min_neg_pct:
            key = "_cluster:" + t["theme"]
            wpath = os.path.join(state_dir, _WARNED)
            warned = _load(wpath, {})
            now = datetime.now(timezone.utc)
            try:
                if (now - datetime.fromisoformat(warned[key])) < timedelta(
                        hours=float(config.get("neg_surge_cooldown_hours", 2))):
                    continue
            except Exception:
                pass
            warned[key] = now.isoformat()
            with open(wpath, "w") as f:
                json.dump(warned, f)
            warn_reasons.append(f"pattern '{t['theme']}' — {t['count']} small posts, {t['neg_pct']}% neg")

    # Volume spike pings only when negative-heavy. A mostly neutral/positive
    # spike is information ("it's landing"), not a summons — it posts as a
    # quiet grey no-ping notice instead.
    quiet_spike = None
    if spike:
        line = f"volume spike — {agg['total']} mentions, ≈{ratio}× the recent average"
        if neg_share >= float(config.get("spike_negative_share", 0.25)):
            warn_reasons.append(line)
        else:
            sent = agg.get("by_sentiment", {})
            quiet_spike = {"text": f"📈 *Volume spike (mostly neutral/positive)* — {title}",
                           "attachments": [{"color": "#B0B0B0", "mrkdwn_in": ["text"],
                                            "fallback": "volume spike",
                                            "text": (line + f"\nSentiment — 🟢 {sent.get('positive', 0)} "
                                                     f"· ⚪ {sent.get('neutral', 0) + sent.get('mixed', 0)} "
                                                     f"· 🔴 {sent.get('negative', 0)}")}]}

    payloads = []
    for tier_key in _TIER_ORDER:
        if tier_key == "warning":
            if warn_reasons:
                small = [m for m in negs if _individual_tier(m, watchlist, scope) is None]
                payloads.append(_tier_payload("warning", small, warn_reasons, responders, title))
        elif by_tier.get(tier_key):
            payloads.append(_tier_payload(tier_key, by_tier[tier_key], None, responders, title))
    if quiet_spike:
        payloads.append(quiet_spike)
    return payloads


def post_slack_payload(webhook_url, payload):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(webhook_url, data=body,
                                 headers={"content-type": "application/json"})
    urllib.request.urlopen(req, timeout=20).read()


def post_to_slack(webhook_url, text):
    for chunk in _chunk(text, 3500):
        body = json.dumps({"text": chunk}).encode()
        req = urllib.request.Request(webhook_url, data=body,
                                     headers={"content-type": "application/json"})
        urllib.request.urlopen(req, timeout=20).read()


def _chunk(text, size):
    lines, cur, n = text.split("\n"), [], 0
    for ln in lines:
        if n + len(ln) > size and cur:
            yield "\n".join(cur)
            cur, n = [], 0
        cur.append(ln)
        n += len(ln) + 1
    if cur:
        yield "\n".join(cur)


# --------------------------------------------------------------------------
# Once-a-day summary (GENERAL tier: grey, no pings) built from the same
# timeseries that feeds the dashboard.
# --------------------------------------------------------------------------
def build_daily_digest(config, state_dir="."):
    ts = _load(os.path.join(state_dir, _TS), [])
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    pts = []
    for p in ts:
        try:
            if datetime.fromisoformat(p["ts"]) >= cutoff:
                pts.append(p)
        except Exception:
            continue
    total = sum(p.get("total", 0) for p in pts)
    pos = sum(p.get("positive", 0) for p in pts)
    neg = sum(p.get("negative", 0) for p in pts)
    neu = sum(p.get("neutral", 0) for p in pts)
    plat, themes = Counter(), Counter()
    notable = []
    for p in pts:
        for k, v in (p.get("by_platform") or {}).items():
            plat[k] += v
        for t in p.get("themes", []):
            themes[t["theme"]] += t["count"]
        notable += p.get("notable", [])
    lines = [f"*{total} mentions in the last 24h*"
             + (f"  ({', '.join(f'{k}: {v}' for k, v in plat.most_common())})" if plat else ""),
             f"Sentiment — 🟢 {pos} · ⚪ {neu} · 🔴 {neg}"]
    if themes:
        lines.append("Themes: " + ", ".join(f"{k} ({v})" for k, v in themes.most_common(5)))
    for n in notable[-6:]:
        lines.append(f"• {_EMOJI.get(n.get('sentiment'), '⚪')} {n.get('author', '')} — "
                     f"<{n.get('url', '')}|{(n.get('summary') or '')[:90]}>")
    if total == 0:
        lines.append("_Quiet day — no relevant mentions recorded._")
    log_url = config.get("slack", {}).get("mentions_log_url", "")
    if log_url:
        lines.append(f"<{log_url}|Full mention log →>")
    title = config.get("slack", {}).get("digest_title", "Plan A monitor")
    return {"text": f"⚪ *{title} — daily summary, {now.strftime('%b %-d')}*",
            "attachments": [{"color": "#B0B0B0", "text": "\n".join(lines),
                             "mrkdwn_in": ["text"], "fallback": "daily summary"}]}


def maybe_daily_digest(config, wh, live, state_dir="."):
    """Post the daily digest on the first run after the configured hour."""
    dd = config.get("daily_digest", {})
    if not dd.get("enabled"):
        return
    now = datetime.now(timezone.utc)
    if now.hour < int(dd.get("hour_utc", 23)):
        return
    path = os.path.join(state_dir, _DIGEST)
    state = _load(path, {})
    today = now.strftime("%Y-%m-%d")
    if state.get("last_sent") == today:
        return
    payload = build_daily_digest(config, state_dir)
    if live:
        post_slack_payload(wh, payload)
        print("Posted daily digest.")
    else:
        print("\n" + "-"*70 + "\nDAILY DIGEST (dry run)\n" + "-"*70 + "\n"
              + payload["text"] + "\n" + payload["attachments"][0]["text"])
    state["last_sent"] = today
    with open(path, "w") as f:
        json.dump(state, f)

# ===== main =====
def _env_overlay(c):
    s=c.setdefault("slack",{})
    if os.getenv("SLACK_WEBHOOK_URL"): s["webhook_url"]=os.getenv("SLACK_WEBHOOK_URL")
    # Slack member IDs are private -> they come from secrets, not the config.
    if os.getenv("SLACK_ERROR_NOTIFY"): s["error_notify"]=os.getenv("SLACK_ERROR_NOTIFY").strip()
    if os.getenv("SLACK_RESPOND_NOTIFY"):
        s["respond_notify"]=[x.strip() for x in os.getenv("SLACK_RESPOND_NOTIFY").split(",") if x.strip()]
    l=c.setdefault("llm",{})
    if os.getenv("ANTHROPIC_API_KEY"): l["api_key"]=os.getenv("ANTHROPIC_API_KEY")
    src=c.setdefault("sources",{})
    tw=src.setdefault("twitter",{})
    if os.getenv("X_BEARER_TOKEN"): tw["bearer_token"]=os.getenv("X_BEARER_TOKEN"); tw["enabled"]=True
    rd=src.setdefault("reddit",{})
    if os.getenv("REDDIT_CLIENT_ID"):
        rd["client_id"]=os.getenv("REDDIT_CLIENT_ID"); rd["client_secret"]=os.getenv("REDDIT_CLIENT_SECRET",""); rd["enabled"]=True
        rd.setdefault("user_agent","plan-a-monitor")
    # The watchlist + substack feeds come from a SECRET, never from the public
    # config file, so the repo never reveals who is being watched (DESIGN.md §6).
    # Cloud: GitHub Actions secret WATCHLIST_YAML. Local: watchlist.local.yaml
    # (gitignored).
    wl_src = os.getenv("WATCHLIST_YAML")
    if not wl_src and os.path.exists("watchlist.local.yaml"):
        with open("watchlist.local.yaml") as f:
            wl_src = f.read()
    if wl_src:
        import yaml
        try:
            data = yaml.safe_load(wl_src) or {}
            if isinstance(data, list):
                c["watchlist"] = data
            elif isinstance(data, dict):
                if data.get("watchlist"):
                    c["watchlist"] = data["watchlist"]
                if data.get("substack_feeds"):
                    c["substack_feeds"] = data["substack_feeds"]
        except Exception as e:
            _note_error(f"WATCHLIST_YAML failed to parse ({e}) — watchlist alerts are OFF this run.")
    return c
def load_config(path):
    import yaml
    with open(path) as f:
        return _env_overlay(yaml.safe_load(f))
def run(config, dry_run=False, mentions=None, state_dir="."):
    _ERRORS.clear()
    if not config.get("watchlist"):
        _note_error("watchlist is EMPTY — WATCHLIST_YAML secret missing/unset? Watchlist alerts are OFF.")
    if not config.get("sources", {}).get("twitter", {}).get("enabled"):
        _note_error("X/Twitter is OFF — X_BEARER_TOKEN secret missing? It's the most important source.")
    try:
        apply_retags(config, state_dir)
    except Exception as e:
        print(f"  [retag] failed: {e}")
    now = datetime.now(timezone.utc)
    # GitHub's cron is best-effort: gaps of 2-3 HOURS between scheduled runs
    # have been observed. If the last run was longer ago than the configured
    # windows, stretch them to cover the gap (dedup makes overlap harmless) —
    # otherwise the short X window would silently skip everything in between.
    gap_min = 0.0
    hist = _load(os.path.join(state_dir, _HIST), [])
    if hist:
        try:
            gap_min = (now - datetime.fromisoformat(hist[-1]["ts"])).total_seconds() / 60
        except Exception:
            pass
    pad_min = gap_min + 10                      # gap plus a safety overlap
    lb_hours = min(max(config.get("lookback_hours", 6), pad_min / 60), 12)
    # News gets its own stretch: if Google News has been 503ing (see
    # health_state), widen the window back to the last successful fetch so
    # articles published during the outage aren't missed once it recovers.
    hstate = _load(os.path.join(state_dir, _HEALTH), {})
    try:
        news_gap_h = (now - datetime.fromisoformat(hstate["news_last_ok"])).total_seconds() / 3600
        config["news_lookback_hours"] = min(max(lb_hours, news_gap_h + 0.25), 24)
        if news_gap_h > 1:
            print(f"  [news] last successful fetch {news_gap_h:.1f}h ago — widening news window.")
    except Exception:
        config["news_lookback_hours"] = lb_hours
    config["twitter_lookback_minutes"] = min(
        max(config.get("twitter_lookback_minutes", 10), pad_min), 12 * 60)
    if gap_min > 30:
        print(f"  [cron] {gap_min:.0f} min since last run — widening fetch windows to cover the gap.")

    # News runs on its own slower clock (Google 503-throttles datacenter IPs
    # when polled every 5 min). Skip the fetch unless the interval has passed;
    # the stretched news window above means an hourly pull misses nothing.
    news_on = config.get("sources", {}).get("news", {}).get("enabled")
    news_attempted = False
    if news_on:
        try:
            mins_since_try = (now - datetime.fromisoformat(hstate["news_last_try"])).total_seconds() / 60
        except Exception:
            mins_since_try = 1e9
        if mins_since_try < float(config.get("news_fetch_interval_minutes", 60)):
            config["sources"]["news"]["enabled"] = False
            print(f"  [news] fetched {mins_since_try:.0f} min ago — next pull in "
                  f"{float(config.get('news_fetch_interval_minutes', 60)) - mins_since_try:.0f} min.")
        else:
            news_attempted = True

    since = now - timedelta(hours=lb_hours)
    if mentions is None: mentions = fetch_all(config, since)
    print(f"Fetched {len(mentions)} raw mentions.")
    if news_on:
        config["sources"]["news"]["enabled"] = True   # restore for downstream checks
    if news_attempted:
        hs = _load(os.path.join(state_dir, _HEALTH), {})
        hs["news_last_try"] = now.isoformat()
        if not any(e.startswith("[News]") for e in _ERRORS):
            hs["news_last_ok"] = now.isoformat()      # feeds the outage-stretch above
        with open(os.path.join(state_dir, _HEALTH), "w") as f:
            json.dump(hs, f)
    # A failed news pull is routine (Google throttling) — only worth a ping if
    # news has been dark for hours; the widened window recovers the articles.
    if any(e.startswith("[News]") for e in _ERRORS):
        try:
            down_h = (now - datetime.fromisoformat(hstate["news_last_ok"])).total_seconds() / 3600
        except Exception:
            down_h = 1e9
        if down_h < float(config.get("news_alert_after_hours", 6)):
            _ERRORS[:] = [e for e in _ERRORS if not e.startswith("[News]")]
            print(f"  [news] pull failed (throttled; down {down_h:.1f}h) — retrying next window, no ping.")
    # Same treatment for X: Cloudflare challenges GitHub's runner IPs at
    # random, the 5-min cadence + widened windows self-heal a failed run, and
    # a dark-for-hours condition is the only thing worth a page. The window is
    # tighter than news's because X is the most important source.
    if config.get("sources", {}).get("twitter", {}).get("enabled"):
        if not any(e.startswith("[X]") for e in _ERRORS):
            hs = _load(os.path.join(state_dir, _HEALTH), {})
            hs["x_last_ok"] = now.isoformat()
            with open(os.path.join(state_dir, _HEALTH), "w") as f:
                json.dump(hs, f)
        else:
            try:
                x_down_h = (now - datetime.fromisoformat(hstate["x_last_ok"])).total_seconds() / 3600
            except Exception:
                x_down_h = 1e9
            if x_down_h < float(config.get("x_alert_after_hours", 2)):
                _ERRORS[:] = [e for e in _ERRORS if not e.startswith("[X]")]
                print(f"  [X] fetch blocked this run (Cloudflare; last ok {x_down_h:.1f}h ago) — "
                      f"self-heals next run, no ping.")
    _llm = config.get("llm", {})
    if _llm.get("enabled") and _llm.get("api_key", "") in ("", "FILL IN"):
        _note_error("ANTHROPIC_API_KEY not set — running keyword-only classification (low fidelity).")
    mentions = classify(mentions, config); print(f"{len(mentions)} judged relevant.")
    new = filter_new(mentions, state_dir); print(f"{len(new)} new since last run.")
    score_mentions(new, config); agg = aggregate(new, config)
    baseline = baseline_and_record(agg["total"],
                                   {n["label"]: n["count"] for n in agg["narratives"]},
                                   state_dir)
    spike, ratio = detect_spike(agg["total"], baseline, config.get("spike_factor", 3.0),
                                int(config.get("spike_min_mentions", 10)))
    record_timeseries(agg, new, state_dir)
    record_mentions(new, state_dir)
    title = config.get("slack", {}).get("digest_title", "Plan A monitor")
    mode = config.get("mode", "alerts")
    wh = config.get("slack", {}).get("webhook_url", "")
    live = not (dry_run or not wh or wh == "FILL IN")

    # Health check: if anything failed this run (a source error, an AI
    # fallback, a missing key), ping the operator — never degrade silently.
    # A cooldown stops the SAME persistent problem from re-paging every 5 min;
    # any new/different problem still pings immediately.
    if _ERRORS:
        # PER-ERROR cooldowns (a single last-signature slot let alternating
        # X/News errors bypass the cooldown every run — the Jul 4-5 ping storm).
        # Keys are digit-normalized so "3 item(s)" vs "5 item(s)" don't count
        # as different problems.
        hpath = os.path.join(state_dir, _HEALTH)
        hstate2 = _load(hpath, {})
        errs = hstate2.get("errs", {})
        cd = timedelta(hours=float(config.get("health_ping_cooldown_hours", 6)))
        now2 = datetime.now(timezone.utc)
        fresh = []
        for e in sorted(set(_ERRORS)):
            key = re.sub(r"\d+", "#", e)
            last = errs.get(key)
            try:
                if last and now2 - datetime.fromisoformat(last) < cd:
                    continue
            except Exception:
                pass
            fresh.append((key, e))
        if not fresh:
            print(f"Health issues ({len(_ERRORS)}) all recently pinged — suppressed (cooldown).")
        else:
            notify_id = config.get("slack", {}).get("error_notify", "")
            ping = ((f"<@{notify_id}> " if notify_id else "")
                    + f"⚠️ *Plan A monitor* hit {len(fresh)} issue(s) this run:\n"
                    + "\n".join(f"• {e}" for _, e in fresh[:20]))
            if live:
                try:
                    post_to_slack(wh, ping); print(f"Posted health warning ({len(fresh)} issue(s)).")
                except Exception as e:
                    print(f"  [health] could not post error ping: {e}")
            else:
                print("\n" + "-"*70 + "\nHEALTH (dry run)\n" + "-"*70 + "\n" + ping)
            for key, _ in fresh:
                errs[key] = now2.isoformat()
            hstate2 = _load(hpath, {})       # re-load: news_last_ok may have changed
            hstate2.pop("sig", None); hstate2.pop("ts", None)
            hstate2["errs"] = dict(list(errs.items())[-50:])
            with open(hpath, "w") as f:
                json.dump(hstate2, f)

    # Once-a-day quiet summary (no pings) — fires on the first run after the
    # configured hour, driven by the same 5-min cron.
    try:
        maybe_daily_digest(config, wh, live, state_dir)
    except Exception as e:
        print(f"  [digest] daily digest failed: {e}")

    if mode == "digest":
        message = build_digest(new, agg, spike, ratio, baseline, title)
        if live:
            post_to_slack(wh, message); print("Posted digest to Slack.")
        else:
            print("\n"+"="*70+"\nDIGEST (dry run)\n"+"="*70+"\n"+message)
        return message

    # alerts mode — tiered routing: one batched, colour-coded message per fired
    # tier (CRITICAL/WARNING/positive/heads-up), silent when nothing fires.
    payloads = build_tier_payloads(new, agg, spike, ratio, config, state_dir)
    if not payloads:
        print("Nothing noteworthy this run — staying quiet."); return None
    if live:
        for p in payloads:
            post_slack_payload(wh, p)
        print(f"Posted {len(payloads)} tier alert(s) to Slack.")
    else:
        print("\n"+"="*70+f"\nALERTS (dry run) — {len(payloads)} message(s)\n"+"="*70)
        for p in payloads:
            print(f"\n▸ {p['text']}\n{p['attachments'][0]['text']}")
    return payloads
def selftest(config):
    """Post one clearly-labeled sample alert per tier through the real Slack
    pipeline (verifies pings, colors, member IDs), plus a live connectivity
    check of every enabled source. Touches no state files."""
    import tempfile
    _ERRORS.clear()
    state_dir = tempfile.mkdtemp(prefix="selftest-")
    now = datetime.now(timezone.utc)
    wl = config.get("watchlist", [])
    wl_name = wl[0]["name"] if wl else "Watchlist Person"
    wl_handle = (wl[0].get("handles") or ["@watchlist_person"])[0] if wl else "@watchlist_person"

    def mk(i, author, name, text, sent, followers=0, imp=0, likes=0, reposts=0):
        m = Mention(platform="twitter", id=f"selftest-{i}", url="https://example.com/selftest",
                    text=text, author=author, author_name=name,
                    author_followers=followers, created_at=now,
                    engagement=likes + 2 * reposts,
                    metrics={"likes": likes, "reposts": reposts, "quotes": 0,
                             "replies": 0, "impressions": imp})
        m.relevant, m.sentiment, m.summary = True, sent, text
        return m

    fake = [
        mk(1, wl_handle, wl_name, "TEST: sample negative take from a watchlist person", "negative"),
        mk(2, "@viral_account", "Viral Account", "TEST: sample viral negative post", "negative",
           followers=50_000, imp=250_000, likes=900, reposts=300),
        mk(3, wl_handle, wl_name, "TEST: sample positive take from a watchlist person", "positive"),
        mk(4, "@big_account", "Big Account", "TEST: sample neutral post from a large account",
           "neutral", followers=400_000, imp=120_000, likes=60, reposts=10),
    ] + [mk(10 + i, f"@small{i}", f"Small {i}", "TEST: sample small negative post", "negative")
         for i in range(6)]

    score_mentions(fake, config)
    agg = aggregate(fake, config)
    cfg2 = dict(config)
    cfg2["slack"] = dict(config.get("slack", {}))
    cfg2["slack"]["digest_title"] = ("🧪 SELFTEST (ignore) — "
                                     + config.get("slack", {}).get("digest_title", "Plan A monitor"))
    payloads = build_tier_payloads(fake, agg, False, 0.0, cfg2, state_dir)

    # live connectivity check: one small real fetch per enabled source
    try:
        counts = Counter(m.platform for m in fetch_all(config, now - timedelta(minutes=30)))
        src_line = (", ".join(f"{k}: {v}" for k, v in counts.items())
                    or "no matching posts in the last 30 min (normal when quiet)")
    except Exception as e:
        src_line = f"fetch failed: {e}"
    err_line = "\n".join("• " + e for e in _ERRORS) if _ERRORS else "none"
    payloads.append({"text": "🧪 *SELFTEST — source connectivity*",
                     "attachments": [{"color": "#B0B0B0", "mrkdwn_in": ["text"],
                                      "text": f"Fetched just now — {src_line}\nSource errors: {err_line}",
                                      "fallback": "selftest"}]})

    wh = config.get("slack", {}).get("webhook_url", "")
    for p in payloads:
        if wh and wh != "FILL IN":
            post_slack_payload(wh, p)
        else:
            print("\n▸ " + p["text"] + "\n" + p["attachments"][0]["text"])
    print(f"Selftest complete: {len(payloads)} message(s)"
          + (" posted to Slack." if wh and wh != "FILL IN" else " (printed; no webhook set)."))


def stresstest(config, state_dir="."):
    """Push a labeled burst of synthetic mentions through the FULL pipeline —
    state files, dashboard, spike/surge detection, Slack — to prove volume
    shows up end to end. Every ping is redirected to the operator so nobody
    else gets paged by a drill. Artifacts are flagged and removed afterwards
    with --stresstest-clean."""
    _ERRORS.clear()
    now = datetime.now(timezone.utc)
    op = config.get("slack", {}).get("error_notify", "")
    cfg = dict(config)
    cfg["slack"] = dict(config.get("slack", {}))
    cfg["slack"]["respond_notify"] = [op] if op else []
    cfg["slack"]["digest_title"] = ("🧪 STRESSTEST (ignore) — "
                                    + config.get("slack", {}).get("digest_title", "Plan A monitor"))

    def mk(i, sent, theme, text, plat="twitter", imp=0, likes=0, reposts=0, followers=0):
        m = Mention(platform=plat, id=f"stresstest-{i}", url="https://example.com/stresstest",
                    text=text, author=f"@stress_user{i}", author_name=f"Stress User {i}",
                    author_followers=followers, created_at=now,
                    engagement=likes + 2 * reposts,
                    metrics=({"likes": likes, "reposts": reposts, "quotes": 0, "replies": 0,
                              "impressions": imp} if plat == "twitter"
                             else {"points": likes, "num_comments": reposts}))
        m.relevant, m.sentiment, m.theme, m.summary = True, sent, theme, text
        return m

    fake = [mk(i, "negative", "stresstest wave A", f"STRESSTEST: negative reaction #{i}")
            for i in range(18)]
    fake += [mk(20 + i, "negative", "stresstest wave B", f"STRESSTEST: skeptical thread #{i}",
                plat="hackernews", likes=3, reposts=2) for i in range(6)]
    fake += [mk(30 + i, "neutral", "stresstest chatter", f"STRESSTEST: neutral mention #{i}")
             for i in range(5)]
    fake += [mk(40 + i, "positive", "stresstest praise", f"STRESSTEST: positive mention #{i}")
             for i in range(5)]
    fake.append(mk(50, "negative", "stresstest viral", "STRESSTEST: sample viral negative post",
                   imp=300_000, likes=1200, reposts=400, followers=80_000))

    score_mentions(fake, cfg)
    agg = aggregate(fake, cfg)
    baseline = baseline_and_record(agg["total"],
                                   {n["label"]: n["count"] for n in agg["narratives"]}, state_dir)
    spike, ratio = detect_spike(agg["total"], baseline, cfg.get("spike_factor", 3.0))
    record_timeseries(agg, fake, state_dir)
    # flag what we just wrote so --stresstest-clean can surgically remove it
    for fname in (_TS, _HIST):
        path = os.path.join(state_dir, fname)
        data = _load(path, [])
        if data:
            data[-1]["test"] = True
            with open(path, "w") as f:
                json.dump(data, f)

    payloads = build_tier_payloads(fake, agg, spike, ratio, cfg, state_dir)
    redirect = f"<@{op}>" if op else ""
    for p in payloads:
        p["text"] = p["text"].replace("<!channel>", redirect)   # drill: never page the channel
    wh = config.get("slack", {}).get("webhook_url", "")
    for p in payloads:
        if wh and wh != "FILL IN":
            post_slack_payload(wh, p)
        else:
            print("\n▸ " + p["text"] + "\n" + p["attachments"][0]["text"][:500])
    print(f"Stresstest: {len(fake)} synthetic mentions injected, {len(payloads)} alert message(s), "
          f"spike={spike} ({ratio}x baseline {baseline:.1f}). The dashboard should now show the burst. "
          f"Remove it afterwards with --stresstest-clean (Actions: mode 'stresstest-clean').")


def stresstest_clean(state_dir="."):
    """Remove everything a stresstest wrote into the state files."""
    for fname in (_TS, _HIST):
        path = os.path.join(state_dir, fname)
        data = _load(path, [])
        kept = [e for e in data if not e.get("test")]
        with open(path, "w") as f:
            json.dump(kept, f)
        print(f"{fname}: removed {len(data) - len(kept)} test entries.")
    path = os.path.join(state_dir, _ALERTED)
    alerted = _load(path, {})
    kept_a = {k: v for k, v in alerted.items() if "stresstest" not in k}
    with open(path, "w") as f:
        json.dump(kept_a, f)
    print(f"{_ALERTED}: removed {len(alerted) - len(kept_a)} test entries.")
    seen_path = os.path.join(state_dir, _SEEN)
    seen = _load(seen_path, [])
    kept_s = [s for s in seen if "stresstest" not in s]
    with open(seen_path, "w") as f:
        json.dump(kept_s, f)
    print(f"{_SEEN}: removed {len(seen) - len(kept_s)} test entries.")
    print("Stresstest data removed; the dashboard reverts on its next refresh.")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true",
                    help="post labeled sample alerts (one per tier) + source connectivity check")
    ap.add_argument("--stresstest", action="store_true",
                    help="inject a labeled synthetic burst through the full pipeline (pings operator only)")
    ap.add_argument("--stresstest-clean", action="store_true",
                    help="remove stresstest data from the state files")
    args = ap.parse_args()
    if args.selftest:
        return selftest(load_config(args.config))
    if args.stresstest:
        return stresstest(load_config(args.config))
    if args.stresstest_clean:
        return stresstest_clean()
    run(load_config(args.config), dry_run=args.dry_run)
if __name__ == "__main__":
    sys.exit(main())
