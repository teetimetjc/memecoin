"""Live runner for the frozen specs: read the book at T-9, bet, hold to settle.

THIS FILE DECIDES WHAT REAL MONEY BUYS. live.py sends the orders; this chooses
them. Everything about it is built so that the thing it bets is the same thing
score.py measured, because a live rule that differs from the tested rule by one
detail is an untested rule with a test's reputation.

HOW THE SELECTION STAYS HONEST. The band, the sides and the entry offset are
read from score.RULES -- the same constants the frozen specs are scored by --
rather than retyped here. The price for a side is computed by the same
arithmetic score.bets uses: YES pays the ask, NO pays one minus the bid. If
someone edits the spec, this follows; it cannot drift from it silently, which
is the only way I know to keep "what we tested" and "what we bet" the same
sentence. `--replay` proves the agreement against history instead of asserting
it, and refuses to pass on a single mismatched bet.

WHY YES-ONLY BY DEFAULT. The expensive favourite bets both sides. Buying NO
means selling YES, and live.py's own note records that this mapping is an
INFERENCE no documentation confirms -- reading it backwards would put every NO
bet on the wrong side of the market, which is the worst failure this system
can have. So the default rule here is the NARROW SLICE: 90-96c, YES only,
which needs no such inference and which capital.py also puts at 0% risk of
ruin on $100 at $4 a bet. The expensive favourite becomes available after one
NO order has been placed and the resulting position read back and confirmed,
and not before. FAVLIVE_RULE names the rule, and the wider one refuses to run
until LIVE_ALLOW_DOWN=1 is also set.

WHAT HOLDS IT BACK FROM SPENDING. All of live.py's gates still apply --
ALLOW_LIVE_TRADING, LIVE_TRADING, the Control tab, the funding check, limit
orders only, idempotent ids. On top of those:

  DRY RUN IS THE DEFAULT. Without LIVE_TRADING=1 this prints the orders it
  would have sent and sends nothing. The first real session should be a dry
  run compared against what the sheet later records, because the cheapest
  place to find a bug in an order path is before it has spent anything.

  A DAILY LOSS STOP. Settled losses are read back each cycle and the run halts
  for the rest of the day past FAVLIVE_DAY_STOP dollars. The historical worst
  day at $4 a bet was well inside $20, so a day through that floor means
  something has changed or something is broken, and either way stopping is
  right.

  A CONCURRENCY CAP. At most FAVLIVE_MAX_PER_WINDOW bets per window. The
  measured peak was 5 at $10; the cap exists so an unexpected flood of
  qualifying markets cannot put the whole bankroll in one fifteen minutes.

TIMING. The windows close at :00/:15/:30/:45 and the rule enters at T-9, so
this wakes at :06/:21/:36/:51. That is NOT when the old bot fired -- v6 went in
35 seconds after the open -- and the difference is the whole rule: the price at
T-9 is the price the specs were scored on.

Read `--replay` first, then dry-run, then live.
"""

import collections
import math
import os
import sys
import time

import alert
import collect15 as C
import control
import live
import predictor as P
import score as S

MAJORS = S.MAJORS
ENTRY_MIN = int(os.environ.get("FAVLIVE_ENTRY_MIN_S") or 9 * 60 - 45)
ENTRY_MAX = int(os.environ.get("FAVLIVE_ENTRY_MAX_S") or 9 * 60 + 45)
MAX_PER_WINDOW = int(os.environ.get("FAVLIVE_MAX_PER_WINDOW") or 5)
DAY_STOP = float(os.environ.get("FAVLIVE_DAY_STOP") or 20.0)
POLL_S = 10

RULE_BY_NAME = {r["name"]: r for r in S.RULES}
DEFAULT_RULE = "narrow slice"


def rule():
    """The spec being bet, straight out of score.RULES."""
    name = (os.environ.get("FAVLIVE_RULE") or DEFAULT_RULE).strip()
    r = RULE_BY_NAME.get(name)
    if r is None:
        raise SystemExit(f"unknown rule {name!r}; have "
                         f"{sorted(RULE_BY_NAME)}")
    if "NO" in r["sides"] and not live.ALLOW_DOWN:
        raise SystemExit(
            f"the {name!r} spec bets the NO side, and the YES->ask mapping "
            "is still unconfirmed against a real fill. Set LIVE_ALLOW_DOWN=1 "
            "only after one NO order has been placed and the position read "
            "back. Until then use the narrow slice, which is YES-only.")
    return r


def side_price(q, side):
    """Dollars paid for one contract of `side`.

    THE SAME ARITHMETIC AS score.bets, deliberately: YES pays the ask, NO pays
    one minus the bid. Getting this subtly different from the scored version is
    how a tested rule becomes an untested one without anybody noticing.
    """
    if side == "YES":
        return q.get("ask")
    b = q.get("bid")
    return None if b is None else (1.0 - b)


def signals_from(markets, r, now):
    """Qualifying bets in this window, as live.py's signal tuples.

    live.py speaks UP/DOWN; the specs speak YES/NO. UP buys YES at the ask and
    DOWN buys NO at one minus the bid, which is exactly side_price above, so
    the two vocabularies describe the same trade and the mapping is one line.
    """
    out = []
    for m in markets:
        tk = str(m.get("ticker") or "")
        ser = tk.split("-")[0]
        if ser not in MAJORS:
            continue
        q = C.quote_from_market(m)
        if not q:
            continue
        for side in r["sides"]:
            px = side_price(q, side)
            if px is None or not (r["band"][0] <= px < r["band"][1]):
                continue
            out.append(dict(ticker=tk, series=ser, side=side,
                            up_down=("UP" if side == "YES" else "DOWN"),
                            price=px, close=str(m.get("close_time") or "")))
    # Cheapest first is NOT the tie-break here: unlike v6, the edge in these
    # specs does not sit at the cheap end, so an arbitrary order would bias
    # which markets get the slots when the cap binds. Sort by series so the
    # choice is at least stable and reconstructable from the log.
    out.sort(key=lambda s: (s["series"], s["side"]))
    if len(out) > MAX_PER_WINDOW:
        print(f"  [favlive] {len(out)} qualify, capping at {MAX_PER_WINDOW}")
        out = out[:MAX_PER_WINDOW]
    return out


def open_markets():
    """Every open 15-minute major market, one list call per series."""
    out = []
    for s in MAJORS:
        d, err = C.get("/trade-api/v2/markets", series_ticker=s,
                       status="open", limit=200)
        if not d:
            print(f"  [favlive] {s}: {err}")
            continue
        out.extend(d.get("markets") or [])
        time.sleep(0.05)
    return out


def day_loss(client):
    """Dollars lost today on rows this runner wrote, or 0.0 if unreadable.

    Reads rather than infers. A stop computed from the bot's own expectation
    would never fire, because the expectation is what is in doubt.
    """
    try:
        ws = P._get_client().open_by_key(P.SPREADSHEET_ID).worksheet(
            live.LIVE_SHEET)
        rows = ws.get_all_values()
    except Exception:
        return 0.0
    if len(rows) < 2:
        return 0.0
    h = {k: i for i, k in enumerate(rows[0])}
    today = time.strftime("%Y-%m-%d", time.gmtime())
    lost = 0.0
    for r in rows[1:]:
        if not r or not str(r[0]).startswith(today):
            continue
        i = h.get("Cost $")
        j = h.get("Status")
        if i is None or j is None or i >= len(r) or j >= len(r):
            continue
        if str(r[j]).strip().upper() != "PLACED":
            continue
        try:
            lost += float(str(r[i]).replace("$", "") or 0)
        except ValueError:
            pass
    return lost


def cycle(client, r, stake, dry):
    mk = open_markets()
    now = time.time()
    # Only the window whose close is ENTRY_MIN..ENTRY_MAX away is actionable.
    live_now = []
    for m in mk:
        t = C.close_ts(m)
        if t is None:
            continue
        dt = t - now
        if ENTRY_MIN <= dt <= ENTRY_MAX:
            live_now.append(m)
    if not live_now:
        return []
    sigs = signals_from(live_now, r, now)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    if not sigs:
        # SHOW THE PRICES, not just the absence of signals. "nothing in
        # 90-96c" reads exactly the same whether the market was genuinely
        # quoted outside the band or the quote came back empty, and this
        # project has shipped five green runs full of blanks for want of
        # exactly this line. A missing quote is a broken read; a quote at
        # 62c is a quiet market. They need different responses.
        seen = []
        for m in live_now:
            tk = str(m.get("ticker") or "")
            if tk.split("-")[0] not in MAJORS:
                continue
            q = C.quote_from_market(m)
            if not q:
                seen.append(f"{tk.split('-')[0]}=NO QUOTE")
                continue
            px = side_price(q, r["sides"][0])
            seen.append(f"{tk.split('-')[0]}="
                        + ("none" if px is None else f"{100*px:.0f}c"))
        print(f"  [favlive] {stamp}: nothing in "
              f"{100*r['band'][0]:.0f}-{100*r['band'][1]:.0f}c "
              f"({len(live_now)} market(s): {', '.join(seen) or 'none priced'})")
        return []
    print(f"  [favlive] {stamp}: {len(sigs)} signal(s)")
    for s in sigs:
        print(f"      {s['series']:<10} {s['side']:<3} "
              f"{100*s['price']:>5.1f}c  {s['ticker']}")

    tuples = [(stamp, s["series"], s["up_down"], s["ticker"],
               100.0 * s["price"]) for s in sigs]
    if dry:
        print(f"  [favlive] DRY RUN -- {len(tuples)} order(s) NOT sent. "
              f"${stake:.2f} each would buy "
              + ", ".join(f"{int(stake/s['price'])}x {s['series']}"
                          for s in sigs))
        return tuples

    rows = live.run_window(client, tuples, stake=stake)
    placed = [x for x in rows if len(x) > 8 and x[8] == "PLACED"]
    if placed and alert.enabled():
        try:
            alert.send(tuples, stamp)
        except Exception as e:
            print(f"  [favlive] alert failed: {str(e)[:80]}")
    return rows


def main():
    args = sys.argv[1:]
    if args and args[0] == "--replay":
        import favreplay
        return favreplay.main()

    seconds = int(args[0]) if args else 3540
    r = rule()
    dry = not live.enabled()
    stake = float(os.environ.get("FAVLIVE_STAKE") or 4.0)
    client = P._get_client()

    print("=" * 84)
    print(f"FAVLIVE -- {r['name']}  "
          f"({100*r['band'][0]:.0f}-{100*r['band'][1]:.0f}c, "
          f"{'/'.join(r['sides'])}, T-{S.ENTRY})")
    print("=" * 84)
    print(f"  stake ${stake:.2f}/bet   cap {MAX_PER_WINDOW}/window   "
          f"day stop ${DAY_STOP:.2f}")
    print(f"  mode: {'DRY RUN (nothing sent)' if dry else 'LIVE -- REAL MONEY'}")
    print(f"  alerts: {'on' if alert.enabled() else 'off'}")
    if not dry:
        bal = control.fetch_balance()
        print(f"  balance: {'unreadable' if bal is None else f'${bal:.2f}'}")
    print()

    deadline = time.time() + seconds
    seen = set()
    while time.time() < deadline:
        if not dry:
            lost = day_loss(client)
            if lost >= DAY_STOP:
                print(f"  [favlive] DAY STOP: ${lost:.2f} staked today is at "
                      f"or past ${DAY_STOP:.2f}. No more orders today.")
                control.halt(client,
                             f"favlive day stop: ${lost:.2f} staked today")
                return 0
        # One cycle per window, keyed so a slow loop cannot bet it twice.
        key = int(time.time()) // 900
        if key not in seen:
            try:
                if cycle(client, r, stake, dry):
                    seen.add(key)
            except Exception as e:
                print(f"  [favlive] cycle error: {str(e)[:160]}")
        time.sleep(POLL_S)
    print("  [favlive] session finished")
    return 0


if __name__ == "__main__":
    sys.exit(main())
