"""Tell a human which bets to place, in time for them to actually place them.

The API cannot place orders from GitHub Actions -- every attempt is refused
with market_not_found for markets that are demonstrably live and priced. But
the Kalshi app on a phone can trade them, and the phone has a person attached
to it, so the bot decides and the person executes.

That is not a downgrade. The whole 1,300-bet history is priced at the quote
taken about 35 seconds into the window, and a human tapping a phone can hit
that; the order API demonstrably cannot. This path is closer to the backtest
than the automated one would have been.

WHICH MEANS THE MESSAGE IS THE PRODUCT. Someone is reading it on a phone,
possibly half awake, with a few minutes to act, and a misread costs real
money. So it says what to tap, not what the model thinks:

  BET UP / BET DOWN    the decision, first and in those words, because that
                       is how the reader thinks about it. "BUY NO ... above
                       0.081395" requires inverting it in your head to see you
                       are betting on a fall, and that inversion is where a
                       mistake gets made with real money on it.
  win if X is ABOVE/   the condition spelled out, so the direction can be
  BELOW <strike>       checked against the decision without any inference
  tap "Yes" / "No"     the button, kept but demoted: it matters at the moment
                       of tapping, not when deciding
  a maximum price      so a moved book does not turn a good bet into a bad one
  a contract count     so the stake is right without mental arithmetic
  the close time       in the reader's own timezone, not UTC
  what was SKIPPED     so silence about a coin is never ambiguous

One message per window, never one per coin. If nothing qualifies, nothing is
sent -- an alert that usually says "do nothing" trains you to ignore it.
"""

import os
from datetime import timedelta
from zoneinfo import ZoneInfo

# Where the reader lives, not where the server runs. A 14:45 UTC close means
# nothing at a glance; 9:45am does.
LOCAL_TZ = ZoneInfo(os.environ.get("ALERT_TZ", "America/Chicago"))

# Only these are worth a notification. Same cut the strategy uses: the 50-60c
# bucket measures -$1.32 a bet and above 60c is worse.
MAX_ENTRY = 50.0

# One cent over the quote, matching the automated path's slip buffer. Beyond
# that, missing the bet is better than overpaying for it.
SLIP = 1.0


# Waking hours, in LOCAL_TZ. Roughly 73% of the 96 daily windows carry a bet,
# so alerting on all of them is about seventy notifications a day, one every
# twenty minutes, through the night. An alert you sleep through is not neutral
# -- it teaches you to swipe the next one away, including the one that
# mattered. Overnight windows still get collected and graded; they just do not
# buzz, because nobody was going to place them anyway.
QUIET_START = int(os.environ.get("ALERT_HOUR_START", "7"))    # inclusive
QUIET_END = int(os.environ.get("ALERT_HOUR_END", "22"))       # exclusive


# A tappable link per bet. Kalshi's site answers 429 to datacenter IPs, so
# the URL shape could not be verified from here or from CI -- and a link that
# 404s at 35 seconds into a window is worse than no link, because it spends
# the seconds the bet needed. So it is a template, unset by default: no
# KALSHI_URL_TEMPLATE means no links, and the alert is exactly as it was.
#
# Placeholders: {ticker} {event} {series} {series_lower} {coin} {slug}
#
# A real link, copied from the app, looks like:
#   kalshi.com/markets/kxbtc15m/btc-15-min--7669745-target/KXBTC15M-26SEP172230
# so the default is that shape. The slug's digits are the strike with its
# decimal point removed -- 76,697.45 becomes 7669745 -- which is the one part
# that has to be computed rather than read off the ticker.
DEFAULT_TEMPLATE = "https://kalshi.com/markets/{series_lower}/{slug}/{event}"
URL_TEMPLATE = os.environ.get("KALSHI_URL_TEMPLATE", DEFAULT_TEMPLATE).strip()

SERIES = {"BTC": "KXBTC15M", "ETH": "KXETH15M", "SOL": "KXSOL15M",
          "XRP": "KXXRP15M", "DOGE": "KXDOGE15M"}


def strike_slug(coin, strike):
    """The slug segment, e.g. btc-15-min--7669745-target, or "" if unknown."""
    if strike in (None, ""):
        return ""
    try:
        # repr-free formatting: 76697.45 -> "76697.45", 0.081395 -> "0.081395".
        # %r or str() on a float can produce scientific notation for small
        # values, which would silently build a wrong link.
        txt = f"{float(strike):.10f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return ""
    digits = txt.replace(".", "").replace("-", "")
    return f"{coin.lower()}-15-min--{digits}-target" if digits else ""


def market_url(coin, ticker, strike=None):
    """A link to this market, or "" when it cannot be built confidently."""
    if not URL_TEMPLATE or not ticker:
        return ""
    slug = strike_slug(coin, strike)
    if "{slug}" in URL_TEMPLATE and not slug:
        return ""            # no strike means no honest link; send none
    try:
        return URL_TEMPLATE.format(
            ticker=ticker,
            event=ticker.rsplit("-", 1)[0],
            series=SERIES.get(coin, ""),
            series_lower=SERIES.get(coin, "").lower(),
            coin=coin.lower(),
            slug=slug,
        )
    except (KeyError, IndexError):
        return ""            # a malformed template must not break the alert


def enabled():
    return os.environ.get("BET_ALERTS", "").strip() == "1"


def in_hours(now_utc):
    """Is it a reasonable hour to buzz someone's phone?"""
    h = now_utc.astimezone(LOCAL_TZ).hour
    if QUIET_START <= QUIET_END:
        return QUIET_START <= h < QUIET_END
    return h >= QUIET_START or h < QUIET_END      # a range crossing midnight


def _stake():
    try:
        return float(os.environ.get("ALERT_STAKE", "5"))
    except ValueError:
        return 5.0


def build(signals, boundary, targets=None):
    """(title, message, top_bet), or (None, None, None) if nothing qualifies.

    `signals` = [(ts, symbol, side, ticker, entry_cents)]
    `targets` = {symbol: strike price} so the message can name the market.
    """
    stake = _stake()
    targets = targets or {}
    close_local = (boundary + timedelta(minutes=15)).astimezone(LOCAL_TZ)

    bets, skips = [], []
    for _ts, symbol, side, ticker, entry in signals:
        coin = symbol.replace("USDT", "")
        if not ticker or entry is None or entry <= 0:
            skips.append(f"{coin} no price")
            continue
        if entry >= MAX_ENTRY:
            skips.append(f"{coin} {entry:.0f}c too pricey")
            continue
        limit = min(99, int(entry + SLIP) + (1 if (entry + SLIP) % 1 else 0))
        contracts = int(stake / (limit / 100.0))
        if contracts < 1:
            skips.append(f"{coin} too pricey for ${stake:.0f}")
            continue
        bets.append({
            "coin": coin,
            "side": side,
            "buy": "YES" if side == "UP" else "NO",
            "entry": entry,
            "limit": limit,
            "contracts": contracts,
            "cost": contracts * limit / 100.0,
            "strike": targets.get(symbol),
            "url": market_url(coin, ticker, targets.get(symbol)),
        })

    if not bets:
        return None, None, None

    bets.sort(key=lambda b: b["entry"])          # cheapest first: best EV first
    title = (f"{len(bets)} bet{'' if len(bets) == 1 else 's'} "
             f"· close {close_local:%-I:%M%p}".replace("AM", "am").replace("PM", "pm"))

    lines = []
    for b in bets:
        lines.append(f"{b['coin']} — BET {b['side']}")
        if b["strike"]:
            # The win condition, not the market's name. Betting DOWN on a
            # market titled "above X" is the one place this could be misread.
            above_below = "ABOVE" if b["side"] == "UP" else "BELOW"
            strike = f"{b['strike']:,}" if b["strike"] >= 1 else f"{b['strike']}"
            lines.append(f"   win if {b['coin']} is {above_below} {strike}")
        lines.append(f"   tap \"{b['buy'].title()}\" · pay up to {b['limit']}c")
        lines.append(f"   {b['contracts']} contracts (${b['cost']:.2f})")
        if b["url"]:
            # Pushover's own url field holds ONE link and a window can name
            # several bets, so each coin gets an inline link and the top bet
            # also gets the button.
            lines.append(f"   <a href=\"{b['url']}\">open {b['coin']} market</a>")
        lines.append("")

    if skips:
        lines.append("skip: " + ", ".join(skips))
    lines.append(f"closes {close_local:%-I:%M%p}".replace("AM", "am").replace("PM", "pm")
                 + " — don't bet after that")
    return title, "\n".join(lines).strip(), bets[0]


def send(signals, boundary, targets=None):
    """Build and send. Never raises: a failed alert must not affect collection."""
    import predictor as P
    from datetime import datetime, timezone
    try:
        if not in_hours(datetime.now(timezone.utc)):
            local = datetime.now(timezone.utc).astimezone(LOCAL_TZ)
            print(f"  [alert] {local:%H:%M} local is outside "
                  f"{QUIET_START:02d}:00-{QUIET_END:02d}:00; no notification.")
            return False
        title, message, top = build(signals, boundary, targets)
        if not title:
            print("  [alert] nothing qualifies this window; no notification sent.")
            return False
        P.send_pushover(title, message,
                        url=top.get("url") or None,
                        url_title=f"Open {top['coin']} market" if top.get("url") else None,
                        html=bool(URL_TEMPLATE))
        print(f"  [alert] sent -- {title}")
        return True
    except Exception as e:
        print(f"  [alert] skipped: {e}")
        return False


# --- regression: URLs verified against links copied from the real app -------
# Two is enough to pin the rule, and they are the two extremes: BTC has the
# largest strikes and DOGE the smallest, where the leading zero in 0.0842487
# -> 00842487 is the part a naive formatter would drop.
KNOWN_URLS = [
    ("BTC", "KXBTC15M-26SEP172230-30", 76697.45,
     "https://kalshi.com/markets/kxbtc15m/btc-15-min--7669745-target/"
     "KXBTC15M-26SEP172230"),
    ("DOGE", "KXDOGE15M-26SEP180015-15", 0.0842487,
     "https://kalshi.com/markets/kxdoge15m/doge-15-min--00842487-target/"
     "KXDOGE15M-26SEP180015"),
]


def selftest():
    bad = 0
    for coin, ticker, strike, want in KNOWN_URLS:
        got = market_url(coin, ticker, strike)
        ok = got == want
        bad += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  {coin}")
        if not ok:
            print(f"        got  {got}")
            print(f"        want {want}")
    print("  URL rule matches every known-real link."
          if not bad else f"  {bad} link(s) no longer match.")
    return bad


if __name__ == "__main__":
    import sys
    sys.exit(selftest())
