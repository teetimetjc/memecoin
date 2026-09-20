"""Champion and challengers for the bounce idea, frozen and judged forward.

WHY THIS EXISTS. Every bounce number produced so far was chosen after seeing
the data it was measured on: sweep the grid, notice the best cell, quote it.
That is how v6 stayed convincing for six weeks, and re-running the same loop
faster would only reach the same wrong place sooner.

So each rule below carries a FREEZE time, and is only ever scored on windows
that closed after it. A rule cannot be tuned to data it has not met.

THE COMPARISON IS PAIRED. A challenger is judged against the champion on the
SAME windows, never against the champion's lifetime average -- five coins in
one window share a market move, and two rules sampled over different stretches
are mostly being compared on which stretch they got. The difference per window
is what gets tested.

THE BAR IS DELIBERATELY HIGH. Six challengers means six chances to look good
by luck, so promotion needs |z| >= 2.58 (roughly Bonferroni for six at 95%),
computed on window-level differences rather than per-trade -- the trades
inside a window are not independent of each other.

AND THE DRIFT GUARD OUTRANKS ALL OF IT. Buying the cheap side is mechanically
a bet that price comes back, so a market trending one way flatters every rule
here at once. If the judging window finished above its strike far from half
the time, the comparison still runs but the verdict says so: a rule that only
wins in a downtrend has not been shown to win.
"""

import math
from collections import defaultdict

# The champion is the rule the evidence pointed at before any of this was
# frozen: mid-cheap entries, two minutes in, take-profit at double. It is the
# source of truth, and it is NOT claimed to be profitable -- only to be the
# thing everything else is measured against.
CHAMPION = {
    "name": "CHAMP", "why": "20-35c, +2min, sell 2x, hold if it never hits",
    "frozen": "2026-09-20 18:00",
    "entry_s": 120, "lo": 20.0, "hi": 35.0, "take": 2.0,
    "exit": "settle", "stop": None,
}

# One difference each, so a win can be attributed to the thing that changed.
CHALLENGERS = [
    {**CHAMPION, "name": "B1", "why": "enter a minute earlier (+60s)",
     "entry_s": 60},
    {**CHAMPION, "name": "B2", "why": "take profit sooner (1.5x)",
     "take": 1.5},
    {**CHAMPION, "name": "B3", "why": "never hold -- bail out before the close",
     "exit": "bail"},
    {**CHAMPION, "name": "B4", "why": "dearer entries (30-45c)",
     "lo": 30.0, "hi": 45.0},
    {**CHAMPION, "name": "B5", "why": "cut it at half (stop loss)",
     "stop": 0.5},
    {**CHAMPION, "name": "B6", "why": "cheapest entries only (under 25c)",
     "lo": 5.0, "hi": 25.0},
]
ALL_RULES = [CHAMPION] + CHALLENGERS
PROMOTE_Z = 2.58


def _fee(n, p):
    return math.ceil(round(0.07 * n * p * (1 - p), 9) * 100) / 100.0


def play(tr, rule, stake=10.0):
    """Net P&L of one setup under one rule, or None if the rule passes on it.

    Works from the RAW book so each rule may choose its own entry moment:
    which side is cheap, and at what price, is decided at that rule's own
    read, not inherited from whenever the payload happened to look.
    """
    ya, yb = tr.get("ya") or [], tr.get("yb") or []
    step = tr.get("st") or 30.0
    first = tr.get("f")
    if first is None or not ya or not yb:
        return None
    off = (rule["entry_s"] - first) / step
    if off < -0.01 or abs(off - round(off)) > 0.01:
        return None
    i = int(round(off))
    if i >= len(ya) or i >= len(yb):
        return None
    a_i, b_i = ya[i], yb[i]
    if a_i is None or b_i is None:
        return None

    # Exactly one side cheap. Both cheap means a wide book, not an
    # opportunity; neither means there is no setup here at all.
    no_ask = 100.0 - b_i
    cheap_yes = a_i <= rule["hi"]
    cheap_no = no_ask <= rule["hi"]
    if cheap_yes == cheap_no:
        return None
    entry = a_i if cheap_yes else no_ask
    if not (rule["lo"] <= entry < rule["hi"]):
        return None

    e = entry / 100.0
    n = stake / e
    cost = stake + _fee(n, e)
    target = entry * rule["take"]
    stop = entry * rule["stop"] if rule.get("stop") else None

    # Exits sell into the BID of whichever side is held.
    for k in range(i + 1, min(len(ya), len(yb))):
        b, a = yb[k], ya[k]
        if b is None or a is None:
            continue
        px = b if cheap_yes else (100.0 - a)
        if target < 100 and px >= target:
            x = px / 100.0
            return round(n * x - cost - _fee(n, x), 4)
        if stop is not None and px <= stop:
            x = max(px, 1.0) / 100.0
            return round(n * x - cost - _fee(n, x), 4)

    if rule["exit"] == "bail":
        seen = [(yb[k] if cheap_yes else (100.0 - ya[k]))
                for k in range(i + 1, min(len(ya), len(yb)))
                if yb[k] is not None and ya[k] is not None]
        if not seen:
            return None
        x = max(seen[-1], 1.0) / 100.0
        return round(n * x - cost - _fee(n, x), 4)

    ab = tr.get("ab")
    if ab is None:
        return None
    won = ab if cheap_yes else (not ab)
    return round((n if won else 0.0) - cost, 4)


def _window_sums(trades, rule):
    g = defaultdict(float)
    cnt = defaultdict(int)
    for tr in trades:
        v = play(tr, rule)
        if v is None:
            continue
        g[tr["t"]] += v
        cnt[tr["t"]] += 1
    return g, cnt


def compare(trades, rule):
    """Challenger vs champion, paired on the windows where BOTH acted."""
    a, ca = _window_sums(trades, CHAMPION)
    b, cb = _window_sums(trades, rule)
    shared = sorted(set(a) & set(b))
    if len(shared) < 8:
        return {"windows": len(shared), "z": None, "verdict": "too few windows"}
    diffs = [b[w] - a[w] for w in shared]
    m = sum(diffs) / len(diffs)
    if len(diffs) < 2:
        return {"windows": len(shared), "z": None, "verdict": "too few windows"}
    var = sum((d - m) ** 2 for d in diffs) / (len(diffs) - 1)
    se = math.sqrt(var / len(diffs)) if var > 0 else 0.0
    z = (m / se) if se > 0 else 0.0
    return {
        "windows": len(shared),
        "champ": round(sum(a[w] for w in shared), 2),
        "rule": round(sum(b[w] for w in shared), 2),
        "diff_per_window": round(m, 3),
        "z": round(z, 2),
        "verdict": ("BEATS champion" if z >= PROMOTE_Z
                    else "WORSE than champion" if z <= -PROMOTE_Z
                    else "no better than champion"),
    }


def drift(trades):
    """How often the judging windows finished above their strike."""
    s = [t for t in trades if t.get("w") is not None]
    if not s:
        return None
    above = sum(1 for t in s if (t["w"] if t.get("y") else not t["w"]))
    return round(above / len(s) * 100.0, 1)


def build(trades):
    """Score every rule on windows that closed after IT was frozen."""
    out = {"promote_z": PROMOTE_Z, "rules": []}
    for rule in ALL_RULES:
        live = [t for t in trades if t["t"] >= rule["frozen"]]
        vals = [v for v in (play(t, rule) for t in live) if v is not None]
        g, _ = _window_sums(live, rule)
        row = {
            "n": rule["name"], "why": rule["why"], "f": rule["frozen"],
            "trades": len(vals),
            "total": round(sum(vals), 2) if vals else 0.0,
            "ev": round(sum(vals) / len(vals), 3) if vals else None,
            "windows": len(g),
            "drift": drift(live),
        }
        if rule is not CHAMPION:
            row["vs"] = compare(live, rule)
        out["rules"].append(row)
    ch = out["rules"][0]
    out["fair"] = (ch["drift"] is not None and abs(ch["drift"] - 50) <= 8)
    return out
