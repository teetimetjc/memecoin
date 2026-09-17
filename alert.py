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

  UP   -> BUY YES      because that is the button in the app
  DOWN -> BUY NO
  the strike price     so the right market gets opened
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
    """(title, message) for this window, or (None, None) if nothing qualifies.

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
            "buy": "YES" if side == "UP" else "NO",
            "entry": entry,
            "limit": limit,
            "contracts": contracts,
            "cost": contracts * limit / 100.0,
            "strike": targets.get(symbol),
        })

    if not bets:
        return None, None

    bets.sort(key=lambda b: b["entry"])          # cheapest first: best EV first
    title = (f"{len(bets)} bet{'' if len(bets) == 1 else 's'} "
             f"· close {close_local:%-I:%M%p}".replace("AM", "am").replace("PM", "pm"))

    lines = []
    for b in bets:
        lines.append(f"{b['coin']} — BUY {b['buy']}")
        if b["strike"]:
            lines.append(f"   market: above {b['strike']:,}")
        lines.append(f"   pay up to {b['limit']}c · {b['contracts']} contracts "
                     f"(${b['cost']:.2f})")
        lines.append("")

    if skips:
        lines.append("skip: " + ", ".join(skips))
    lines.append(f"closes {close_local:%-I:%M%p}".replace("AM", "am").replace("PM", "pm")
                 + " — don't bet after that")
    return title, "\n".join(lines).strip()


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
        title, message = build(signals, boundary, targets)
        if not title:
            print("  [alert] nothing qualifies this window; no notification sent.")
            return False
        P.send_pushover(title, message)
        print(f"  [alert] sent -- {title}")
        return True
    except Exception as e:
        print(f"  [alert] skipped: {e}")
        return False
