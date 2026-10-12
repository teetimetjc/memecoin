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

import os
import sys
import time

import requests

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
# SIDES CAN BE OVERRIDDEN WITHOUT EDITING A FROZEN SPEC. score.RULES is the
# pre-registered text the two specs are scored against, and widening
# "narrow slice" there would quietly change the thing the forward test is
# measuring. This env var layers on top instead, so the spec keeps meaning
# what it meant and the deviation is visible in the workflow file.
SIDES_OVERRIDE = tuple(
    x.strip().upper() for x in
    (os.environ.get("FAVLIVE_SIDES") or "").split(",") if x.strip())
# A SEPARATE STAKE PER SIDE, because they are not the same bet. YES has a
# five-day live record; NO has a 2,052-bet null and one confirmed fill. Equal
# sizing would assert an equal confidence that does not exist.
STAKE_BY_SIDE = {}
# THE DAY STOP CAN BE TURNED OFF, AND ONLY BY NAME. "off" is spelled out
# rather than expressed as a huge number, because 999999 in a workflow file
# reads like a limit to whoever finds it next and is in fact the absence of
# one. Disabling it should be legible at a glance and impossible to set by
# accident -- a typo in a number still parses as a number.
#
# WHAT IS LEFT WHEN IT IS OFF: the funding check, which halts once the
# balance cannot cover a window. That makes the effective floor the account
# balance rather than a loss limit, and the Control tab the only fast stop.
_DAY_STOP_RAW = (os.environ.get("FAVLIVE_DAY_STOP") or "").strip()
DAY_STOP_OFF = _DAY_STOP_RAW.lower() in ("off", "none", "disabled")
DAY_STOP = 20.0 if (DAY_STOP_OFF or not _DAY_STOP_RAW) else float(_DAY_STOP_RAW)
POLL_S = 10
# How often the day's settled P&L is re-read, and how old that figure may get
# before it stops counting as a guard. Five minutes is far finer than the stop
# needs -- a losing day takes hours to build -- and 45 minutes of staleness
# still bounds the damage at a few windows.
PNL_EVERY_S = 300
PNL_MAX_AGE_S = 2700

# EVERY TICKER THIS SESSION HAS TRIED TO ORDER, whether or not the attempt was
# seen to succeed. One market may be bet once, ever.
#
# WHY THIS EXISTS. On 2026-10-08 the bot put two orders into one ETH window
# 26 seconds apart, at 82c and 87c, and lost both: -$6.83 from a single close,
# which turned the live record from +$3.09 to -$1.97. Two orders on one market
# are not two bets. They share one outcome, so this is one bet at 1.9x the
# intended size -- the opposite of the per-window cap's purpose.
#
# The hole was in the window guard below: it recorded a window as done only
# when cycle() RETURNED SOMETHING TRUTHY, so an order that was placed and then
# lost its response -- a timeout, a sheet write that raised, an empty rows list
# -- looked exactly like a window where nothing had happened, and the next poll
# ten seconds later bet it again. THE GUARD WAS KEYED ON SUCCESS WHEN THE
# IRREVERSIBLE ACT IS THE ATTEMPT.
#
# So this set is written BEFORE the order goes out, never after. The trade is
# deliberate and it is the right way round: if the attempt then fails, the
# window is skipped and that costs about 31c of expected value. Betting it
# twice costs $3.69 of real exposure and corrupts the sample.
ORDERED = set()

RULE_BY_NAME = {r["name"]: r for r in S.RULES}
DEFAULT_RULE = "narrow slice"


def rule():
    """The spec being bet, straight out of score.RULES."""
    name = (os.environ.get("FAVLIVE_RULE") or DEFAULT_RULE).strip()
    r = RULE_BY_NAME.get(name)
    if r is None:
        raise SystemExit(f"unknown rule {name!r}; have "
                         f"{sorted(RULE_BY_NAME)}")
    if SIDES_OVERRIDE:
        unknown = [x for x in SIDES_OVERRIDE if x not in ("YES", "NO")]
        if unknown:
            raise SystemExit(f"FAVLIVE_SIDES has {unknown}; only YES and NO")
        r = dict(r)
        r["sides"] = SIDES_OVERRIDE
        r["name"] = f"{r['name']} [sides={'/'.join(SIDES_OVERRIDE)}]"
    if "NO" in r["sides"] and not live.ALLOW_DOWN:
        raise SystemExit(
            f"the {name!r} spec bets the NO side, and the YES->ask mapping "
            "is still unconfirmed against a real fill. Set LIVE_ALLOW_DOWN=1 "
            "only after one NO order has been placed and the position read "
            "back. Until then use the narrow slice, which is YES-only.")
    return r


def stake_for(side, default):
    """Dollars to risk on one bet of `side`.

    FAVLIVE_STAKE_NO is read separately and falls back to the main stake, so
    a workflow that sets neither behaves exactly as it did before both sides
    existed.
    """
    if side == "NO":
        v = os.environ.get("FAVLIVE_STAKE_NO")
        if v:
            try:
                return float(v)
            except ValueError:
                pass
    return default


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
    # The same market can appear twice in one list -- overlapping series
    # queries, a retried page -- and two entries for one ticker would send two
    # orders from a single cycle, which ORDERED cannot catch because it is only
    # consulted once, at the top of this loop.
    this_call = set()
    for m in markets:
        tk = str(m.get("ticker") or "")
        ser = tk.split("-")[0]
        if ser not in MAJORS:
            continue
        # Already bet this market. Not "already bet this window" -- the unit
        # that shares an outcome is the MARKET, and that is the unit a second
        # order doubles down on.
        if tk in ORDERED:
            continue
        q = C.quote_from_market(m)
        if not q:
            continue
        for side in r["sides"]:
            px = side_price(q, side)
            if px is None or not (r["band"][0] <= px < r["band"][1]):
                continue
            if (tk, side) in this_call:
                continue
            this_call.add((tk, side))
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


def day_pnl():
    """Net dollars settled today, from Kalshi's settlements. None if unreadable.

    THE FIRST VERSION OF THIS SUMMED WHAT WAS STAKED AND CALLED IT A LOSS.
    It added the Cost column of every PLACED row and halted past $20, which on
    a quiet two-bet day never binds -- and on a six-bet day halts the bot even
    if every single bet won, then leaves it halted until somebody resumes it by
    hand. A stop that fires on a winning day is measuring the wrong quantity,
    and the name said "loss" while the arithmetic said "turnover".

    Net settled P&L is the right quantity and the exchange is the only
    authority for it: a win pays $1.00 a contract and a loss pays nothing, and
    neither is knowable from the order row that opened the position. Returns
    None rather than 0.0 when the read fails, because "I could not tell" and
    "nothing was lost" must not be the same value -- treating a failed read as
    a flat day is how a stop silently stops stopping.
    """
    hdrs = P._kalshi_headers("GET", "/trade-api/v2/portfolio/settlements")
    if not hdrs:
        return None
    try:
        rr = requests.get(f"{P.KALSHI_BASE}/portfolio/settlements",
                          headers=hdrs, params={"limit": 200}, timeout=15)
        if not rr.ok:
            print(f"  [favlive] settlements HTTP {rr.status_code}")
            return None
        items = rr.json().get("settlements") or []
    except Exception as e:
        print(f"  [favlive] settlements read failed: {str(e)[:80]}")
        return None

    today = time.strftime("%Y-%m-%d", time.gmtime())
    net = 0.0
    for s in items:
        when = str(s.get("settled_time") or "")
        if not when.startswith(today):
            continue
        rev = _money(s, ("revenue_dollars", "revenue"))
        cost = _money(s, ("cost_dollars", "cost", "yes_total_cost_dollars"))
        if rev is None and cost is None:
            continue
        net += (rev or 0.0) - (cost or 0.0)
    return net


def _money(d, keys):
    """First readable dollar value among `keys`, converting cents if needed.

    The _fp/_dollars migration has caught this project six times, so the
    dollar spelling is preferred and a bare value above a plausible dollar
    bound is treated as cents rather than assumed to be dollars.
    """
    for k in keys:
        v = d.get(k)
        if v is None:
            continue
        try:
            x = float(v)
        except (TypeError, ValueError):
            continue
        if x != x:
            continue
        return x / 100.0 if (k.endswith(("_dollars",)) is False and
                             abs(x) > 1000) else x
    return None


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
    # CLAIM THE MARKETS BEFORE SENDING, NOT AFTER. Everything below this line
    # can fail -- the call can time out, the response can be lost, run_window
    # can raise on the way back from a filled order. None of that un-places an
    # order, so none of it may leave a ticker eligible to be bet again.
    #
    # The dry path claims them too, deliberately: a dry run exists to exercise
    # every line the live run will, and a guard that only engages with real
    # money on the line is a guard nothing has tested.
    for s in sigs:
        ORDERED.add(s["ticker"])

    if dry:
        print(f"  [favlive] DRY RUN -- {len(tuples)} order(s) NOT sent. "
              f"${stake:.2f} each would buy "
              + ", ".join(f"{int(stake/s['price'])}x {s['series']}"
                          for s in sigs))
        return tuples

    # ONE CAP ACROSS BOTH SIDES, THEN ONE CALL PER SIDE. signals_from has
    # already truncated to MAX_PER_WINDOW over the combined list, so splitting
    # here cannot smuggle in extra bets -- which calling run_window twice
    # without that ordering absolutely would, since run_window applies its own
    # ceiling per call and would allow the cap on each side separately.
    #
    # The split exists because the two sides carry different stakes, and
    # run_window takes one stake for the whole window.
    rows = []
    for side_name in ("UP", "DOWN"):
        part = [t for t in tuples if t[2] == side_name]
        if not part:
            continue
        st = stake_for("YES" if side_name == "UP" else "NO", stake)
        print(f"  [favlive] {len(part)} {side_name} order(s) at ${st:.2f}")
        rows.extend(live.run_window(client, part, stake=st))
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
    sides_txt = "/".join(r["sides"])
    stake_txt = f"${stake:.2f}/bet"
    if "NO" in r["sides"]:
        no_stake = stake_for("NO", stake)
        if abs(no_stake - stake) > 1e-9:
            stake_txt = (f"YES ${stake:.2f} / NO ${no_stake:.2f} per bet")
    ds_txt = "OFF (no automatic halt)" if DAY_STOP_OFF else f"${DAY_STOP:.2f}"
    print(f"  {stake_txt}   sides {sides_txt}   "
          f"cap {MAX_PER_WINDOW}/window   day stop {ds_txt}")
    if DAY_STOP_OFF:
        print("  *** DAY STOP DISABLED. The only automatic halt left is the")
        print("  *** funding check, so the floor is the account balance.")
        print("  *** Stop it by setting the Control tab to HALTED.")
    print(f"  mode: {'DRY RUN (nothing sent)' if dry else 'LIVE -- REAL MONEY'}")
    print(f"  alerts: {'on' if alert.enabled() else 'off'}")
    if not dry:
        bal = control.fetch_balance()
        print(f"  balance: {'unreadable' if bal is None else f'${bal:.2f}'}")
    print()

    deadline = time.time() + seconds
    seen = set()
    pnl_at = 0.0          # when the day's P&L was last read successfully
    pnl_val = None        # and what it was
    while time.time() < deadline:
        if not dry:
            # ONCE EVERY PNL_EVERY_S, NOT EVERY POLL. This sat inside the
            # ten-second loop, so it called the settlements endpoint 360 times
            # an hour -- abusive, and a standing invitation to be rate limited.
            # Worse, the branch below treats an unreadable call as a reason not
            # to bet, so throttling would have silently stopped all betting
            # while looking exactly like a quiet market.
            if time.time() - pnl_at >= PNL_EVERY_S:
                fresh = day_pnl()
                if fresh is not None:
                    pnl_val, pnl_at = fresh, time.time()
                else:
                    # Keep the last good value and let it age. Betting stops
                    # only once it is too stale to be a guard at all.
                    print("  [favlive] settlements unreadable; using the last "
                          f"figure ({(time.time()-pnl_at)/60:.0f} min old)")
            pnl = pnl_val
            # A LOSS is a negative net. Compared as a magnitude against the
            # stop so the sign cannot be read backwards -- getting that wrong
            # would halt on a good day and run through a bad one.
            if DAY_STOP_OFF:
                # Still READ it, still SAY it. Turning the stop off removes
                # the automatic halt, not the obligation to show the number
                # the halt would have used -- a silent unlimited session is a
                # different and worse thing than a visible one.
                if pnl is not None:
                    print(f"  [favlive] day stop OFF -- net ${pnl:+.2f} "
                          "settled today, no automatic halt")
            elif pnl is not None and pnl <= -DAY_STOP:
                print(f"  [favlive] DAY STOP: net ${pnl:+.2f} settled today is "
                      f"at or past -${DAY_STOP:.2f}. No more orders today.")
                control.halt(client,
                             f"favlive day stop: net ${pnl:+.2f} settled today")
                return 0
            # Block only when the guard is genuinely blind: never read, or so
            # stale it no longer bounds anything. A single failed call is not
            # a reason to stop betting for the session.
            # A STALE GUARD ONLY BLOCKS IF THERE IS A GUARD. This refuses to
            # bet when the day figure is too old to bound anything, which is
            # right when the stop is armed and pointless when it is off --
            # there is nothing being bounded, so an unreadable settlements
            # call would stop all betting for no protective reason at all.
            stale = (time.time() - pnl_at) if pnl_at else 1e9
            if (not DAY_STOP_OFF) and (pnl is None or stale > PNL_MAX_AGE_S):
                print("  [favlive] the day stop cannot be evaluated "
                      + ("(never read)" if pnl is None
                         else f"({stale/60:.0f} min stale)")
                      + " -- not betting this cycle.")
                time.sleep(POLL_S)
                continue
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
