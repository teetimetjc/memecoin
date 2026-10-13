"""Shrink the stake after a losing streak, and halt on a deep drawdown.

WHY A STREAK AND NOT A DRAWDOWN TRIGGER. Measured over the 136-bet live
record, normalised to a flat $10 stake:

    flat $10, no rule                     net +17.57   max drawdown -40.00
    drawdown >= $15 from peak -> $5        +3.67              -28.52
    drawdown >= $20 from peak -> $5        +0.54              -31.65
    2 losses in a row -> $4, 3 wins back  +26.40              -26.22
    3 losses in a row -> $4, 3 wins back  +24.13              -29.98

The streak rules improved BOTH profit and drawdown; the drawdown rules
mostly made things worse. There is a mechanism rather than a coincidence:
losses cluster, because bets sharing a close window resolve the same way in
23 of 24 cases. A streak fires AT the bad window. A drawdown trigger fires
after the damage, then holds you small through the recovery -- which is
exactly what the +$0.54 row is.

Three in a row rather than two: both tested within noise of each other, and
three triggered once over the record where two triggered three times. Fewer
interventions is less opportunity to be fitting three data points.

THE NUMBERS ARE NOT THE EVIDENCE. That +$26 comes from three trigger events
on one account over six days. What the test supports is the DIRECTION --
every streak variant at least matched flat, every drawdown variant did worse,
and there is a reason why. Treat the thresholds as a reasonable choice, not
as optimised, and do not retune them when a week goes badly.

IT CREATES NO EDGE. If the rule's edge is zero this changes the variance and
nothing else; if the edge is negative the only correct action is to stop.
This exists to keep the account solvent long enough to find out which.

STATELESS BY CONSTRUCTION, which is the part that matters operationally.
Sessions here are cancelled and restarted constantly -- six times on
2026-10-12 alone -- and a ladder held in process memory would silently reset
to full stake on every restart, which is the opposite of what it is for. So
the whole history is replayed from the exchange's own settlements on each
call and the current rung is derived, never remembered.
"""

import os

import requests

import predictor as P


# Settled losses in a row that drop the stake.
DOWN_AFTER = int(os.environ.get("LADDER_DOWN_AFTER") or 3)
# Settled wins in a row that restore it.
UP_AFTER = int(os.environ.get("LADDER_UP_AFTER") or 3)
# The reduced stake, in dollars.
REDUCED = float(os.environ.get("LADDER_REDUCED") or 4.0)
# Drawdown from the equity peak that halts betting outright. This replaces
# the day stop when that is off: a much looser brake, but a brake.
HALT_DRAWDOWN = float(os.environ.get("LADDER_HALT_DRAWDOWN") or 60.0)
# How many settlements to read. The record is 136 bets; 500 leaves headroom
# before the peak itself starts rolling off the back of the window, which
# would make the drawdown look smaller than it is.
LIMIT = int(os.environ.get("LADDER_LIMIT") or 500)
# ONLY SETTLEMENTS AFTER THIS COUNT. An ISO timestamp, or empty for all of
# them.
#
# WHY IT EXISTS. Replaying the whole history put the account straight onto
# the reduced rung: the ladder tripped at the 08:35 three-loss window on
# 2026-10-12 and there has been no three-win streak since to restore it. So
# switching the ladder on would silently have cancelled the stake increase
# the operator had just asked for, which is a confusing way to lose an
# argument nobody had.
#
# Starting the ladder from a chosen moment keeps the stake where it was put
# and still arms the brake going forward. The cost is honest and worth
# stating: a drawdown that began before this timestamp is invisible to the
# halt, so the $60 floor measures from here, not from the account's real
# peak.
START = (os.environ.get("LADDER_START") or "").strip()


def _money(d, keys):
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
        return x
    return None


def settlements():
    """Every settlement the exchange will give us, oldest first, or None.

    None rather than [] on failure, because "I could not read it" and "there
    are none" must not be the same value -- treating an unreadable history as
    an empty one would reset the ladder to full stake on a network hiccup.
    """
    hdrs = P._kalshi_headers("GET", "/trade-api/v2/portfolio/settlements")
    if not hdrs:
        return None
    try:
        r = requests.get(f"{P.KALSHI_BASE}/portfolio/settlements",
                         headers=hdrs, params={"limit": LIMIT}, timeout=15)
        if not r.ok:
            return None
        items = r.json().get("settlements") or []
    except Exception:
        return None
    out = []
    for s in items:
        rev = _money(s, ("revenue_dollars", "revenue"))
        cost = _money(s, ("cost_dollars", "cost", "yes_total_cost_dollars"))
        when = str(s.get("settled_time") or "")
        if rev is None or cost is None or not when:
            continue
        if START and when <= START:
            continue
        out.append((when, rev - cost, rev > 0))
    out.sort(key=lambda x: x[0])
    return out


def state(base_stake):
    """(stake, halt, note) given the settled history.

    On an unreadable history it returns the base stake and does NOT halt:
    a guard that stops all betting whenever the network hiccups is worse
    than the thing it guards against, and the same reasoning already governs
    the last-look guard in live.py.
    """
    rows = settlements()
    if rows is None:
        return base_stake, False, "settlements unreadable; ladder not applied"
    if not rows:
        return base_stake, False, (
            "no settlements since the ladder start; full stake"
            if START else "no settlements yet; full stake")

    # Replay the ladder forward. Reduced is sticky until UP_AFTER wins in a
    # row, which is the hysteresis that stops it flapping on alternating
    # results.
    reduced = False
    losses = 0
    wins = 0
    run = 0.0
    peak = 0.0
    trough = 0.0
    for _when, pnl, won in rows:
        run += pnl
        peak = max(peak, run)
        trough = min(trough, run - peak)
        if won:
            wins += 1
            losses = 0
            if reduced and wins >= UP_AFTER:
                reduced = False
                wins = 0
        else:
            losses += 1
            wins = 0
            if not reduced and losses >= DOWN_AFTER:
                reduced = True
                losses = 0

    dd = run - peak
    stake = REDUCED if reduced else base_stake
    if dd <= -HALT_DRAWDOWN:
        return stake, True, (
            f"drawdown ${dd:.2f} from a peak of ${peak:+.2f} is at or past "
            f"${HALT_DRAWDOWN:.2f}")
    note = ((f"since {START}, " if START else "")
            + f"{len(rows)} settled, net ${run:+.2f}, peak ${peak:+.2f}, "
            f"drawdown ${dd:.2f}, "
            + (f"REDUCED to ${stake:.2f} "
               f"({DOWN_AFTER} losses in a row; {UP_AFTER} wins restores it)"
               if reduced else f"full stake ${stake:.2f}")
            + f", worst drawdown seen ${trough:.2f}")
    return stake, False, note
