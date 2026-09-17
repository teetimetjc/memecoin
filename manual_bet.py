"""Place ONE bet a human asked for by name. Not a signal, not a strategy.

Everything else that spends money here is driven by the champion rule: it
decides the coin, the side and whether the price qualifies. Four windows in a
row it decided "nothing", which answers a question nobody was asking. The open
question is simpler -- CAN this account place an order at all -- and answering
it does not require waiting for the rule to agree.

So this takes the coin, the side and the stake as arguments and places exactly
that, once.

What it deliberately does NOT apply:

  the 50c price cut   That is a STRATEGY rule about which bets are worth
                      making. This bet is not being made because it is worth
                      making; it is being made to see whether the order lands.
                      Refusing on price would answer a question nobody asked.

  the CVD threshold   Same reason. There is no signal here.

What it still applies, because these are about not losing money by accident:

  ONE order           No loop, no retry across windows, no second chance.
  the stake cap       Contracts are floored, never rounded up into overspend.
  a real ticker       Resolved by matching the window's close time.
  read-back           Positions are read after, so what was actually opened is
                      printed rather than assumed.

The order itself goes through live.place() -- the same code path, body and
signing the automated bets use. A hand-rolled request here would prove that a
hand-rolled request works, which is not the thing in doubt.
"""

import os
import sys
from datetime import datetime, timedelta, timezone

import requests

import live
import predictor as P


def resolve_ticker(symbol, when=None):
    """This window's market ticker for `symbol`, matched on close time.

    Not looked up by price: at the start of a window the market exists but its
    book is a placeholder, so a price-validating lookup reports no market at
    exactly the wrong moment.
    """
    series = P.KALSHI_SERIES.get(symbol)
    if not series:
        return None, f"no Kalshi series for {symbol}"
    now = when or datetime.now(timezone.utc)
    boundary = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
    close = boundary + timedelta(minutes=15)
    want = f"{close:%Y-%m-%dT%H:%M}"

    hdrs = P._kalshi_headers("GET", "/trade-api/v2/markets") or {}
    r = requests.get("https://api.elections.kalshi.com/trade-api/v2/markets",
                     params={"series_ticker": series, "limit": 200},
                     headers=hdrs, timeout=15)
    if not r.ok:
        return None, f"markets list HTTP {r.status_code}"
    for mk in r.json().get("markets", []):
        if str(mk.get("close_time", ""))[:16] == want:
            return mk["ticker"], f"window {boundary:%H:%M}-{close:%H:%M} UTC"
    return None, f"no {series} market closing at {want}"


def main():
    symbol = (sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT").upper()
    side = (sys.argv[2] if len(sys.argv) > 2 else "DOWN").upper()
    stake = float(sys.argv[3]) if len(sys.argv) > 3 else 5.0

    print("=" * 62)
    print(f"MANUAL BET -- {symbol} {side} ${stake:.2f}")
    print("=" * 62)

    if side not in ("UP", "DOWN"):
        print(f"  side must be UP or DOWN, got {side!r}")
        return 1
    if not (0 < stake <= 25):
        print(f"  stake ${stake:.2f} outside the $0-25 this is allowed to risk")
        return 1
    if not live.enabled():
        print("  LIVE_TRADING / ALLOW_LIVE_TRADING are not both set. Nothing sent.")
        return 1

    ticker, note = resolve_ticker(symbol)
    print(f"  ticker : {ticker or '(none)'}  -- {note}")
    if not ticker:
        return 1

    entry = live.current_entry(ticker, side)
    print(f"  price  : {'(order host has no price yet)' if entry is None else f'{entry:.0f}c to bet {side}'}")
    if entry is None:
        print("  The order host is not quoting this market yet. Nothing sent.")
        return 1

    # The price cut is a strategy rule and this is not a strategy bet. Lifted
    # explicitly and loudly rather than quietly edited out of live.place().
    if entry >= live.MAX_ENTRY:
        print(f"  note   : {entry:.0f}c is above the {live.MAX_ENTRY:.0f}c strategy cut; "
              f"placing anyway because this bet was asked for by name.")
        live.MAX_ENTRY = 100.0

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    status, detail, n, cost, oid = live.place(ts, symbol, side, ticker, entry, stake)
    print()
    print(f"  STATUS : {status}")
    print(f"  detail : {detail or '(none)'}")
    print(f"  size   : {n} contract(s), ${cost:.2f}")
    print(f"  order  : {oid or '(none)'}")

    # Written to the same tab as every other bet, so the record stays in one
    # place and a manual bet cannot masquerade as a signal later.
    try:
        live.append(P._get_client(), [[
            ts, symbol, side, ticker, round(entry, 1),
            min(99, int(entry + live.SLIP_BUFFER_CENTS)), n, cost,
            status, oid, (detail + " [manual]").strip(),
        ]])
        print("  logged to Live Bets.")
    except Exception as e:
        print(f"  (could not log to Live Bets: {e})")

    print()
    live.verify_positions()
    print("=" * 62)
    return 0 if status == "PLACED" else 1


if __name__ == "__main__":
    sys.exit(main())
