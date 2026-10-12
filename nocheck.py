"""Confirm, with ONE small real order, that a DOWN bet buys NO.

THE OPEN QUESTION, unanswered since the betting code was written. live.py
sends a DOWN bet as an ASK on the YES contract:

    yes_price = limit_c / 100.0 if side == "UP" else 1.0 - limit_c / 100.0
    "side": "bid" if side == "UP" else "ask",

Selling YES you do not own is the standard way to get NO exposure, and the
price is quoted in YES terms for both directions, so 1 - p is the right
number IF the endpoint reads "ask" as "sell YES". That was inferred from the
shape of the API, never confirmed against a fill. live.py says so in its own
words: "That is standard but it was inferred, not documented."

If the inference is backwards, every NO bet lands on the wrong side of the
market -- not a small loss, the exact opposite position at a price chosen for
the other one. That is why ALLOW_DOWN is off, and why no amount of backtest
evidence can turn it on. Only a real fill can answer it.

WHAT THIS DOES. Finds one major market whose NO price sits in the 90-96c
band, sends a single DOWN order for a few dollars, then reads the account
back and states plainly what was bought:

    the fill, from /portfolio/fills -- which side, at what price
    the position, from /portfolio/positions -- positive is YES, negative NO

A PASS is a negative position in that ticker, acquired at about the NO price
intended. Anything else is a fail and ALLOW_DOWN stays off.

THE SAFETY RAILS, because this is the one script here that spends money on a
code path nobody has tested:

    it refuses to run without NOCHECK_CONFIRM=1, set only in a workflow a
    human dispatches by hand;
    ONE order, ever -- it returns after the first attempt whatever happens;
    MAX_CONTRACTS caps the size regardless of what the stake arithmetic
    produces, so a mispriced market cannot turn $5 into a large position;
    it will not run against a market closing in under MIN_SECONDS, so the
    order is not racing settlement.

WHAT IT DELIBERATELY DOES NOT DO. It does not clear the position. A NO bet
at 92c that confirms the mapping is also an ordinary bet with an ordinary
expected value, and selling it back immediately would pay the spread twice to
avoid a few dollars of variance. It settles on its own like any other bet and
the settlement is the second half of the confirmation.
"""

import os
import sys
import time

import collect15 as C
import control
import live
import predictor as P
import score as S
import tally


MAJORS = list(S.MAJORS)
LO, HI = 90.0, 96.0
STAKE = float(os.environ.get("NOCHECK_STAKE") or 5.0)
# A hard ceiling that does not depend on the stake arithmetic being right.
MAX_CONTRACTS = 6
MAX_STAKE = 6.0
# Do not race settlement: the order needs time to fill and be read back.
# THE BAND ONLY EXISTS NEAR T-9, which is the whole point of the strategy and
# took two failed runs to respect here. The first run was dispatched into the
# dead minute between a close 60s away and the next one 960s away. Widening
# the window then produced the opposite error: it found four markets at 866s
# left -- T-14 -- quoting NO at 47-59c, because fourteen minutes out nothing
# has decided yet and there is no favourite on either side to buy.
#
# So this targets the same offset the bot does, and WAITS for it rather than
# checking once and giving up. A single dispatch now covers whichever window
# comes next instead of depending on the operator's timing.
MIN_SECONDS = 480          # T-8
MAX_SECONDS = 660          # T-11
# How long to keep looking before giving up, and how often to look.
WAIT_S = int(os.environ.get("NOCHECK_WAIT_S") or 900)
POLL_S = 10


def find(quiet=None):
    """One market with NO in band and enough time left. None if there isn't."""
    now = time.time()
    best = None
    # Every candidate seen, so "nothing in band" can be told apart from
    # "nothing was looked at" -- the distinction the first run could not make.
    saw = []
    for s in MAJORS:
        try:
            ms = C.open_markets(s)
        except Exception as e:
            print(f"  {s}: could not list markets: {str(e)[:70]}")
            continue
        for m in ms or []:
            t = C.close_ts(m)
            if t is None:
                continue
            left = t - now
            if not (MIN_SECONDS <= left <= MAX_SECONDS):
                continue
            q = C.quote_from_market(m)
            if not q:
                continue
            bid = q.get("bid") if isinstance(q, dict) else None
            if bid is None:
                continue
            no_px = 100.0 * (1.0 - bid)
            saw.append((s, no_px, left))
            if not (LO <= no_px < HI):
                continue
            cand = (no_px, str(m.get("ticker")), s, left)
            # Prefer the cheapest NO in band: it is the least extreme, so the
            # most ordinary test of the mechanism.
            if best is None or cand[0] < best[0]:
                best = cand
    # Print each close window once, not once per poll: at a 10s poll a
    # 3-minute entry window would otherwise print the same five markets
    # eighteen times.
    if saw:
        key = round(min(x[2] for x in saw) / 60.0)
        if quiet is None or key not in quiet:
            if quiet is not None:
                quiet.add(key)
            stamp = time.strftime("%H:%M:%S", time.gmtime())
            print(f"  {stamp}  {len(saw)} market(s) at T-8..T-11:")
            for ser, px, left in sorted(saw, key=lambda x: x[1]):
                mark = "  <- IN BAND" if LO <= px < HI else ""
                print(f"    {ser:<12} NO {px:>5.1f}c  {left:>5.0f}s left{mark}")
    return best


def main():
    if os.environ.get("NOCHECK_CONFIRM") != "1":
        print("NOCHECK_CONFIRM is not 1 -- refusing to place anything.")
        return 1
    if STAKE > MAX_STAKE:
        print(f"stake ${STAKE:.2f} is above the ${MAX_STAKE:.2f} ceiling.")
        return 1

    print("=" * 84)
    print("DOES A DOWN ORDER ACTUALLY BUY NO?  one small real order")
    print("=" * 84)
    print(f"  stake ${STAKE:.2f}, hard cap {MAX_CONTRACTS} contracts")
    print(f"  live.ALLOW_DOWN = {live.ALLOW_DOWN}")
    if not live.ALLOW_DOWN:
        print("  ALLOW_DOWN is off, so live.place will refuse the order.")
        print("  Set LIVE_ALLOW_DOWN=1 for this job only.")
        return 1

    # RESPECT THE KILL SWITCH. live.place does not consult the Control tab --
    # run_window does, and this calls place directly. Without this check the
    # one script that bets on an untested code path would be the one script
    # that ignores the human's stop button.
    try:
        client = P._get_client()
        state, why = control.get_state(client)
        print(f"  Control tab: {state}" + (f" ({why})" if why else ""))
        if state != control.RUNNING:
            print("  Control tab is not RUNNING -- refusing to place.")
            return 1
    except Exception as e:
        print(f"  could not read the Control tab: {str(e)[:90]}")
        print("  refusing to place: an unreadable switch is not permission.")
        return 1

    print(f"\n  waiting up to {WAIT_S}s for a market with NO in "
          f"{LO:.0f}-{HI:.0f}c at T-8..T-11...")
    deadline = time.time() + WAIT_S
    found = None
    announced = set()
    while time.time() < deadline:
        found = find(quiet=announced)
        if found:
            break
        time.sleep(POLL_S)
    if not found:
        print(f"\n  nothing reached the {LO:.0f}-{HI:.0f}c NO band in "
              f"{WAIT_S}s. That is the market, not a fault: in an undecided "
              "window there is no favourite to buy on either side.")
        return 0
    no_px, ticker, series, left = found
    print(f"\n  chose {series} {ticker}")
    print(f"    NO price {no_px:.1f}c, {left:.0f}s to close")

    want = int(STAKE / (no_px / 100.0))
    if want > MAX_CONTRACTS:
        print(f"    {want} contracts exceeds the {MAX_CONTRACTS} cap; "
              "not sending.")
        return 1
    print(f"    intending about {want} contract(s) of NO at ~{no_px:.1f}c")

    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    print("\n  sending ONE order...")
    status, detail, contracts, cost, oid = live.place(
        stamp, series, "DOWN", ticker, no_px, STAKE)
    print(f"    status {status}  {detail}")
    print(f"    contracts {contracts}  cost ${cost:.2f}  order {oid}")

    if status != "PLACED":
        print("\n  nothing filled, so nothing to confirm. Not a fail -- run "
              "it again when the book is there.")
        return 0

    print("\n  reading the account back...")
    time.sleep(4)

    d = tally.get("/portfolio/fills", limit=50)
    fills = [f for f in ((d or {}).get("fills") or [])
             if str(f.get("ticker") or "") == ticker]
    if not fills:
        print("    no fill rows yet for this ticker (they can lag)")
    for f in fills[:4]:
        side = str(f.get("side") or "")
        yp = tally._f(f.get("yes_price_dollars"))
        np_ = tally._f(f.get("no_price_dollars"))
        cnt = tally._f(f.get("count")) or tally._f(f.get("count_fp"))
        act = str(f.get("action") or f.get("taker_side") or "")
        print(f"    fill: side={side!r} action={act!r} count={cnt} "
              f"yes_price={yp} no_price={np_}")

    pos = live.verify_positions()
    mine = [p for p in (pos or [])
            if str(p.get("ticker") or "") == ticker]

    print("\n" + "=" * 84)
    if not mine:
        print("  INCONCLUSIVE: no position row for that ticker yet. Re-read")
        print("  the account in a minute before concluding anything.")
        return 0
    q = mine[0].get("position") or mine[0].get("market_exposure") or 0
    try:
        qn = float(q)
    except (TypeError, ValueError):
        qn = 0.0
    if qn < 0:
        print(f"  PASS. Position is {q}, which is NO exposure -- a DOWN order")
        print("  does buy NO, and the mapping live.py inferred is correct.")
        print("  The settlement is the other half: a market closing NO should")
        print("  pay this position out.")
    elif qn > 0:
        print(f"  FAIL. Position is {q}, which is YES exposure. A DOWN order")
        print("  bought the side it was meant to sell. ALLOW_DOWN must stay")
        print("  off and live.place's side mapping is wrong.")
    else:
        print(f"  INCONCLUSIVE: position reads {q}.")
    print("=" * 84)
    return 0


if __name__ == "__main__":
    sys.exit(main())
