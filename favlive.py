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
import ladder
import live
import abort
import dedupe
import heartbeat
import shadow
import t3shadow
import version
import predictor as P
import score as S

# THE SERIES THIS SESSION TRADES. Defaults to score.MAJORS, the five coins
# the frozen spec was measured on, and can be overridden per session the
# same way the sides and the band can -- by a workflow, visibly, without
# editing a frozen spec.
#
# WHY IT IS AN OVERRIDE AND NOT AN EDIT. score.MAJORS is what the spec was
# scored against. A session that trades a different set is running a
# DIFFERENT RULE, and the record has to be able to say so. The series is
# recoverable from each bet's ticker, so no column is needed; what matters
# is that the frozen five keep meaning the frozen five.
#
# goldoil measured KXWTI15M at +3.79 in the 90-96c band, positive in both
# chronological halves -- second only to SOL across every series tested --
# and KXGOLD15M at +1.23 that FLIPS sign between halves, which is noise.
# Only the first of those earned a session.
_series_env = (os.environ.get("FAVLIVE_SERIES") or "").strip()
MAJORS = (tuple(x.strip().upper() for x in _series_env.split(",") if x.strip())
          if _series_env else S.MAJORS)
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
# THE BAND CAN BE NARROWED THE SAME WAY, AND ONLY NARROWED.
#
# The band lives in score.RULES and had no override at all, so a session
# that set MIN_ENTRY_CENTS/MAX_ENTRY_CENTS -- which are a separate guard in
# live.py -- ran the spec's 90-96c while its workflow said 90-94c. The T-3
# dry run caught exactly that: the banner read "90-96c" with the workflow
# asking for 90-94c, and nothing in the log said the setting had been
# ignored.
#
# Written as "90-94" in cents. It must be a SUBSET of the spec's band: a
# narrower slice of a measured rule is still inside what was measured,
# whereas widening it would bet prices the spec never covered while still
# calling itself that spec. Anything outside is refused loudly rather than
# clamped, because a clamp is another silent disagreement between the
# workflow and what actually ran.
BAND_OVERRIDE = (os.environ.get("FAVLIVE_BAND") or "").strip()
# A SEPARATE STAKE PER SIDE, because they are not the same bet. YES has a
# five-day live record; NO has a 2,052-bet null and one confirmed fill. Equal
# sizing would assert an equal confidence that does not exist.
STAKE_BY_SIDE = {}
# QUIET HOURS: scan and log, but place nothing. "4-12" means 04:00-11:59 UTC,
# which is 11pm-7am in Chicago while CDT holds.
#
# WHY. In the live record that window is 46 bets at -3.95% ROI while the rest
# of the day is 96 bets at +1.15%. Four of its five losses filled BELOW the
# quote and three of those were one 08:35 window at 61-67c against quotes of
# 90-94c. The mechanism is statable in a sentence -- overnight books are thin,
# so orders sweep further for worse prices -- and the backtest agrees: 11pm-3am
# CT is negative on BOTH sides there.
#
# WHAT THE PAPER RECORD WILL NOT SHOW, and this matters more than it sounds.
# The signals logged here are priced at the QUOTE. The overnight problem is
# the FILL, and a skipped order has no fill. So the paper record of this
# window will look systematically BETTER than trading it would have been, and
# the gap is precisely the thing that made the window lose. Read it as "did
# the favourite hold", never as "we would have made this money".
# SHADOW MODE: observation only, default ON because it cannot place, block
# or delay an order -- it runs after the window's orders are already sent.
# Set FAVLIVE_SHADOW=0 to silence it entirely.
SHADOW_ON = (os.environ.get("FAVLIVE_SHADOW") or "1").strip() not in ("0", "", "off")
# A dry session has no fills to measure, so the abort check normally has
# nothing to do. This lets a dry run exercise the code path anyway, which is
# the point of having a dry run for an order-sending module.
ABORT_DRY_LOG = (os.environ.get("ABORT_DRY_LOG") or "0").strip() == "1"
QUIET_UTC = (os.environ.get("FAVLIVE_QUIET_UTC") or "").strip()
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

# SHADOW RULES: NAMED BANDS THAT ARE NOT FROZEN SPECS, AND MUST NOT BE
# MISTAKEN FOR THEM.
#
# score.RULES is the pre-registered text, and score.py ITERATES IT: adding an
# entry there does not merely make a band available, it makes the frozen
# forward test start reporting a third rule. The one property that test has
# is that nobody can adjust what it measures, so a band invented today does
# not belong in it. This registry exists so a new band can be shadowed
# without touching that file at all.
#
# EVERY ENTRY HERE IS PAPER UNTIL A WORKFLOW GIVES IT A LIVE STEP, and
# nothing in this file grants one. need_n is the pre-registered sample at
# which the band gets judged, kept in the same shape as score.RULES so a
# rule that ever earns promotion can be moved across unchanged.
#
# low slice -- 88-90c YES at T-9. bandsweep measured 88-89c at +2.04% net
# ROI over 201 markets, positive in both chronological halves, against a
# 90-96c control that reproduced the live band at +1.36%. It is shadowed
# rather than bet because 89-90c, the cent BETWEEN it and the live band,
# FLIPPED sign between halves (+2.92% / -0.49%). No market misprices 88-89c,
# prices 89-90c correctly, then misprices 90-96c again; prices do not work
# in stripes. So one of those rows is noise, and the candidate is the one
# whose older half is +0.40% -- positive by a rounding error.
#
# THE PASS CONDITION, recorded here and not only in the workflow: ~100
# settled paper bets, net ROI positive against this cohort's OWN mean cost
# per contract, AND 88-89c and 89-90c both positive. Failing the adjacency
# half is a stop, not a reason to pool the cents until the sign returns.
SHADOW_RULES = (
    dict(name="low slice", spec="specs/low_slice.md",
         band=(0.88, 0.90), sides=("YES",), need_n=100, shadow=True),
)

# The frozen specs first, then the shadows. Written in this order
# deliberately: a shadow rule can never shadow a frozen spec's name, because
# a name collision would silently rebind what "narrow slice" means.
RULE_BY_NAME = {r["name"]: r for r in S.RULES}
for _sr in SHADOW_RULES:
    if _sr["name"] in RULE_BY_NAME:
        raise SystemExit(
            f"shadow rule {_sr['name']!r} collides with a frozen spec of the "
            "same name. Rename the shadow: a collision here would rebind what "
            "the frozen spec means while still calling itself that spec.")
    RULE_BY_NAME[_sr["name"]] = _sr
DEFAULT_RULE = "narrow slice"


def rule():
    """The spec being bet, from score.RULES or the shadow registry."""
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
    if BAND_OVERRIDE:
        try:
            lo_s, hi_s = BAND_OVERRIDE.split("-")
            lo, hi = float(lo_s) / 100.0, float(hi_s) / 100.0
        except ValueError:
            raise SystemExit(
                f"FAVLIVE_BAND={BAND_OVERRIDE!r} is not 'LO-HI' in cents, "
                "e.g. '90-94'")
        if not (lo < hi):
            raise SystemExit(f"FAVLIVE_BAND={BAND_OVERRIDE!r} is not an "
                             "increasing range")
        slo, shi = r["band"]
        if lo < slo - 1e-9 or hi > shi + 1e-9:
            raise SystemExit(
                f"FAVLIVE_BAND={BAND_OVERRIDE!r} is outside the "
                f"{r['name']!r} spec's {100*slo:.0f}-{100*shi:.0f}c band. "
                "The band may be narrowed, never widened: a narrower slice "
                "is still inside what was measured, a wider one bets prices "
                "the spec never covered.")
        r = dict(r)
        r["band"] = (lo, hi)
        r["name"] = f"{r['name']} [band={100*lo:.0f}-{100*hi:.0f}c]"
    # A SHADOW RULE REFUSES TO RUN WITH MONEY, IN CODE, NOT BY CONFIGURATION.
    #
    # lowshadow.yml has no live step and passes no live switches, which is the
    # first line of defence. This is the second, and it is the one that holds
    # when someone later copies that workflow, adds a live step to it and
    # forgets what the band was: the band and the prohibition travel together
    # in the registry above, so the guard cannot be left behind by an edit to
    # a different file.
    #
    # It fails LOUDLY rather than quietly dropping to paper, because a session
    # that was asked to bet and silently did not is the exact failure this
    # project keeps rediscovering.
    if r.get("shadow") and live.enabled():
        raise SystemExit(
            f"{r['name']!r} is a SHADOW rule and live trading is enabled. "
            "Shadow bands are measured on paper and have not passed their "
            "pre-registered condition; betting one would spend money on "
            "prices no frozen spec covers. Promote it into score.RULES with "
            "its spec written first, or unset LIVE_TRADING/"
            "ALLOW_LIVE_TRADING.")
    if "NO" in r["sides"] and not live.ALLOW_DOWN:
        raise SystemExit(
            f"the {name!r} spec bets the NO side, and the YES->ask mapping "
            "is still unconfirmed against a real fill. Set LIVE_ALLOW_DOWN=1 "
            "only after one NO order has been placed and the position read "
            "back. Until then use the narrow slice, which is YES-only.")
    return r


def quiet_now():
    """True if orders are suppressed right now. Open range: 4-12 is 4..11."""
    if not QUIET_UTC or "-" not in QUIET_UTC:
        return False
    try:
        a, b = (int(x) for x in QUIET_UTC.split("-", 1))
    except ValueError:
        return False
    h = time.gmtime().tm_hour
    # A wrapping window such as 22-6 is read as "22:00 through 05:59".
    return (a <= h < b) if a < b else (h >= a or h < b)


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
    """Every open 15-minute major market, one list call per series.

    DO NOT TRUST status="open" AS THE ONLY WAY TO ASK.
    
    On 2026-10-24 this returned an EMPTY LIST on every call for hours. The
    request itself succeeded -- no error line was printed, the balance and
    settlements endpoints answered normally on the same credentials -- so
    the call was fine and the filter simply matched nothing. The exchange
    had stopped describing these markets as "open".

    The bot placed nothing from 15:5x onward and said nothing about it,
    because cycle() returned on an empty market list without printing. Four
    and a half hours, three strategies, one silent filter.

    So the status filter is now a PREFERENCE, not a requirement: ask for
    open markets, and if that comes back empty, ask again without the filter
    and decide locally from close_time, which is the field the entry window
    is computed from anyway. A market whose close has passed is dropped
    here; everything else is handed on and cycle() picks the window.

    The fallback announces itself. A silent fallback would fix tonight and
    hide the next change exactly the way the status filter hid this one.
    """
    out = []
    used_fallback = False
    for s in MAJORS:
        d, err = C.get("/trade-api/v2/markets", series_ticker=s,
                       status="open", limit=200)
        if not d:
            print(f"  [favlive] {s}: {err}")
            continue
        got = d.get("markets") or []

        if not got:
            # Same series, no status filter. Anything already closed is
            # discarded below, so this cannot widen what gets bet -- it only
            # stops a vocabulary change from emptying the list.
            d2, err2 = C.get("/trade-api/v2/markets",
                             series_ticker=s, limit=200)
            if d2:
                now = time.time()
                for m in (d2.get("markets") or []):
                    t = C.close_ts(m)
                    if t is not None and t > now:
                        got.append(m)
                if got and not used_fallback:
                    used_fallback = True
                    print(f"  [favlive] status='open' returned nothing for "
                          f"{s}; falling back to an unfiltered list and "
                          f"selecting on close_time ({len(got)} still open).")
            elif err2:
                print(f"  [favlive] {s} (unfiltered retry): {err2}")

        out.extend(got)
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
    """First readable dollar value among `keys`.

    THE OLD RULE WAS A GUESS AND IT WAS WRONG. It read:

        return x / 100.0 if (not k.endswith("_dollars") and abs(x) > 1000) else x

    so a bare cents field only converted when it exceeded 1000. A settlement
    revenue of 400 -- four dollars -- is under that threshold and sailed
    through as $400. On 2026-10-12 this made favlive report the day's net as
    "+$14343.07" against a true figure near -$40, and it printed $0.00 and
    +$400.00 in the same session.

    The consequence was not cosmetic: the $50 day stop read this number, so
    it could never have tripped. The account ran all day with a brake that
    was reporting five figures of imaginary profit.

    THE RULE NOW: a *_dollars field is dollars, anything else is cents, with
    no magnitude guess anywhere. Sizes do not disambiguate units -- $4 and
    400c are the same money and only the field name says which is written.
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
        return x if k.endswith("_dollars") else x / 100.0
    return None


_QUIET_SAID_AT = 0.0


def cycle(client, r, stake, dry, factor=1.0):
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
        # THE ONLY SILENT EXIT IN THIS FUNCTION, AND IT COST A DAY.
        #
        # On 2026-10-24 the session printed 485 day-stop lines over 90
        # minutes and nothing else: no scan, no signals, no error. The loop
        # was healthy, the heartbeat was writing, the job was green, and the
        # bot placed nothing for four and a half hours. Every other branch
        # below reports what it saw; this one returned an empty list and left
        # no trace, so "no market is near its entry window" was
        # indistinguishable from "the markets read came back empty".
        #
        # Those need opposite responses -- the first is normal between
        # windows, the second is a broken read -- so the line below says
        # which. Rate limited to once a minute because this branch is hit on
        # every poll between windows, which is most of them.
        global _QUIET_SAID_AT
        if now - _QUIET_SAID_AT > 60:
            _QUIET_SAID_AT = now
            if not mk:
                print(f"  [favlive] {time.strftime('%H:%M:%S', time.gmtime())}"
                      ": open_markets() returned NOTHING -- this is a broken "
                      "read, not a quiet market.")
            else:
                nxt = None
                for m in mk:
                    t = C.close_ts(m)
                    if t is None or t < now:
                        continue
                    if nxt is None or t < nxt:
                        nxt = t
                near = (f"{nxt - now:.0f}s" if nxt else "none upcoming")
                print(f"  [favlive] {time.strftime('%H:%M:%S', time.gmtime())}"
                      f": {len(mk)} market(s) open, none in the entry window "
                      f"({ENTRY_MIN:.0f}-{ENTRY_MAX:.0f}s before close); "
                      f"nearest close is {near} away.")
        return []
    sigs = signals_from(live_now, r, now)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())

    # DO NOT BUY WHAT ANOTHER STRATEGY ALREADY HOLDS. Only engaged when
    # FAVLIVE_SKIP_HELD=1, which is set for the T-3 session and not for the
    # frozen T-9 one -- the later strategy yields, the established one is
    # untouched.
    #
    # live._client_order_id namespaces by ORDER_PREFIX on purpose, so the
    # exchange's duplicate rejection will NOT catch this: two strategies on
    # one market are two unrelated orders as far as Kalshi is concerned.
    # See dedupe.py on why that doubles exposure on a single outcome and
    # splits the settlement so neither record is true.
    if sigs and dedupe.enabled():
        held = dedupe.held_tickers(
            client, exclude_prefix=os.environ.get("ORDER_PREFIX", "").strip())
        before = len(sigs)
        sigs = [s for s in sigs if s["ticker"] not in held]
        if before != len(sigs):
            print(f"  [favlive] skipped {before - len(sigs)} market(s) "
                  "already held by another strategy")
        if not sigs:
            print("  [favlive] every signal was already held -- "
                  "nothing to place this window.")
            return []
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
        if quiet_now() and live.enabled():
            print(f"  [favlive] QUIET HOURS ({QUIET_UTC} UTC) -- "
                  f"{len(tuples)} signal(s) logged, nothing placed.")
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
        st = stake_for("YES" if side_name == "UP" else "NO", stake) * factor
        print(f"  [favlive] {len(part)} {side_name} order(s) at ${st:.2f}")
        rows.extend(live.run_window(client, part, stake=st))
    # SHADOW OBSERVATION, AFTER THE ORDERS AND AFTER NOTHING ELSE DEPENDS ON
    # IT. Placed strictly here so it cannot sit between the decision and the
    # order: an extra round trip in that gap is extra time for the ask to
    # move, which would change real fills. See shadow.py on what the
    # post-submission reading can and cannot tell us.
    if not dry and SHADOW_ON:
        try:
            shadow.observe(client, tuples, rows, stake)
        except Exception as e:
            print(f"  [favlive] shadow failed (ignored): {str(e)[:80]}")

    # WHAT THESE SAME SIGNALS LOOK LIKE AT T-3. Only the decisions are
    # recorded here; the book is read six minutes later by t3shadow.sample
    # in the main loop. Placed after the orders for the same reason the
    # shadow observation is: nothing may sit between the decision and the
    # order.
    try:
        t3shadow.remember(tuples, rows, stake)
    except Exception as e:
        print(f"  [favlive] t3 remember failed (ignored): {str(e)[:80]}")

    # THE ABORT CHECK. Reads the real fill and the real bid, decides, and
    # writes the row down. It only SENDS a sell when ABORT_SELL=1 and the
    # session is live -- unset, it is a measurement that costs nothing and
    # tells us what arming it would have been worth.
    if not dry or ABORT_DRY_LOG:
        try:
            abort.consider(client, rows, dry)
        except Exception as e:
            print(f"  [favlive] abort check failed (ignored): {str(e)[:80]}")

    heartbeat.count_window(sum(1 for x in rows
                               if len(x) > 8 and x[8] == "PLACED"))
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
    # THE SERIES, ON SCREEN, EVERY SESSION. The band override was ignored
    # silently for a whole dry run because the banner never printed what
    # was actually being scanned; a setting that does not appear in the
    # log is a setting nobody can check.
    # THE SELECTOR BAND AND THE ORDER GUARD MUST AGREE, and nothing used
    # to check that they did.
    #
    # live.py keeps its own MIN_ENTRY/MAX_ENTRY from MIN_ENTRY_CENTS and
    # MAX_ENTRY_CENTS, defaulting to 20c and 50c -- the v6 range. The
    # selector band comes from score.RULES. A session that sets one and
    # not the other selects a signal and then drops it in run_window as
    # "nothing betable", BEFORE any row is written, so the sheet shows no
    # trace and the log line is indistinguishable from a quiet market.
    #
    # That is exactly what happened to the T-3 session: FAVLIVE_BAND was
    # 90-94c, MAX_ENTRY_CENTS was unset so the guard stood at 50c, and
    # fifteen in-band signals were discarded in silence over five hours.
    # The heartbeat's signal-window counter was the only thing that knew.
    #
    # So it is now checked at startup and refused loudly. A band wholly
    # outside the guard means every signal would be thrown away, which is
    # never what anyone meant to configure.
    lo_c, hi_c = 100.0 * r["band"][0], 100.0 * r["band"][1]
    if lo_c < live.MIN_ENTRY or hi_c > live.MAX_ENTRY:
        raise SystemExit(
            f"the {r['name']!r} band is {lo_c:.0f}-{hi_c:.0f}c but the order "
            f"guard admits only {live.MIN_ENTRY:.0f}-{live.MAX_ENTRY:.0f}c "
            "(MIN_ENTRY_CENTS / MAX_ENTRY_CENTS). Every signal would be "
            "discarded in run_window as 'nothing betable', before any row "
            "was written. Set both to match the band.")
    print(f"  entry guard: {live.MIN_ENTRY:.0f}-{live.MAX_ENTRY:.0f}c "
          f"(band {lo_c:.0f}-{hi_c:.0f}c)")
    print(f"  series: {', '.join(MAJORS)}"
          + ("" if MAJORS == S.MAJORS else "   <- NOT the frozen five"))
    print("=" * 84)
    sides_txt = "/".join(r["sides"])
    stake_txt = f"${stake:.2f}/bet"
    if "NO" in r["sides"]:
        no_stake = stake_for("NO", stake)
        if abs(no_stake - stake) > 1e-9:
            stake_txt = (f"YES ${stake:.2f} / NO ${no_stake:.2f} per bet")
    # WRITE DOWN WHAT THIS SESSION IS before it places anything. Every
    # comparison in this project is "period X vs period Y", and what separates
    # them is a settings change that previously lived only in a git commit.
    # Logged for dry sessions too: a dry period is still a period the record
    # has to be able to explain.
    try:
        version.log(client, stake, dry)
    except Exception as e:
        print(f"  [favlive] version log failed (ignored): {str(e)[:80]}")

    ds_txt = "OFF (no automatic halt)" if DAY_STOP_OFF else f"${DAY_STOP:.2f}"
    if QUIET_UTC:
        print(f"  quiet hours: {QUIET_UTC} UTC -- scan and log, place nothing")
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
    last_ladder_note = [""]   # so the ladder line prints on change, not per poll
    quiet_said = [False]      # so the quiet-hours banner prints on entry only
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
        # THE LADDER, replayed from the exchange each cycle rather than held
        # in memory. Sessions here are cancelled and restarted constantly, and
        # a ladder kept in process state would reset to full stake every time
        # -- the opposite of what it is for.
        factor = 1.0
        if not dry:
            lad_stake, lad_halt, lad_note = ladder.state(stake)
            if lad_note != last_ladder_note[0]:
                print(f"  [ladder] {lad_note}")
                last_ladder_note[0] = lad_note
            if lad_halt:
                print(f"  [favlive] LADDER HALT: {lad_note}")
                control.halt(client, f"ladder halt: {lad_note}")
                return 0
            factor = (lad_stake / stake) if stake else 1.0

        # QUIET HOURS SUPPRESS THE ORDER, NOT THE SCAN. The window still
        # gets looked at and the signals still get printed, so the question
        # "would it have been right?" stays answerable from the log -- and
        # the M15 sheet keeps recording every market's quote and outcome
        # regardless, which is the real paper record.
        q = quiet_now()
        if q and not dry and not quiet_said[0]:
            print(f"  [favlive] QUIET HOURS {QUIET_UTC} UTC -- scanning and "
                  "logging, placing nothing.")
            quiet_said[0] = True
        if not q:
            quiet_said[0] = False

        # One cycle per window, keyed so a slow loop cannot bet it twice.
        key = int(time.time()) // 900
        if key not in seen:
            try:
                if cycle(client, r, stake, dry or q, factor):
                    seen.add(key)
            except Exception as e:
                print(f"  [favlive] cycle error: {str(e)[:160]}")

        # THE T-3 SAMPLE RUNS EVERY POLL, not once per window. The T-3
        # moment arrives six minutes after the T-9 decision, long after
        # cycle() has returned for that window, so the loop has to be the
        # thing looking. It places nothing and swallows its own errors.
        t3shadow.sample(client)

        # THE PULSE. Written to the sheet rather than the log, because a
        # killed runner takes its log with it -- the oil session died on
        # 2026-10-22 and GitHub serves 404 for that job to this day. It is
        # rate limited inside heartbeat.beat and swallows its own errors.
        heartbeat.beat(client, "live" if not dry else "dry", stake,
                       MAJORS,
                       f"{100*r['band'][0]:.0f}-{100*r['band'][1]:.0f}c")
        time.sleep(POLL_S)
    print("  [favlive] session finished")
    return 0


if __name__ == "__main__":
    sys.exit(main())
