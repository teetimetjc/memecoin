"""Does copying another Polymarket trader make money? Places nothing.

THE QUESTION, IN TWO PARTS, IN ORDER.

  1. PERSISTENCE. Do profitable wallets STAY profitable? Split each wallet's
     own history in half chronologically, rank on the first half, score on
     the second. If a good first half does not predict a good second half,
     there is nothing to copy and part 2 is moot.
  2. LAG. If persistence exists, does a follower entering minutes behind
     still profit? Not in this file -- it needs per-market price history --
     and there is no point building it until part 1 passes.

WHY THIS VENUE AND NOT KALSHI. Kalshi's trades are anonymous: you can see
price, size and time but never who. Polymarket settles on-chain, so every
trade carries a wallet, and the Data API serves full history unauthenticated.

WHY COPYING COULD WORK HERE WHEN IT CANNOT ON PUMP.FUN. Copying means
entering late by construction, and on a fast market that is fatal -- our own
flip data put entries under 15s at a +44.5% MEAN (median only +4.94%) and
entries at 60s+ at -1.77%. But a Polymarket contract resolving in weeks
barely moves in the minutes it takes to follow, so lateness costs little.
Latency is the whole argument, and it is the one thing that differs.

HOW P&L IS RECONSTRUCTED, and what is refused rather than guessed:

  BUY     cash out = size x price
  SELL    cash in  = size x price
  REDEEM  cash in  = usdc_size, the dollars actually received
  YIELD   EXCLUDED. Interest on idle collateral is not trading skill, and
          counting it would credit a wallet for holding cash.
  MERGE
  SPLIT   THESE CARRY NO token_id. Merging a YES+NO pair back into a dollar
          moves real money that cannot be attributed to any position, so a
          wallet that uses them materially CANNOT be scored per position.
          Such wallets are REFUSED and counted, never modelled around.

ONLY CLOSED POSITIONS ARE SCORED. An open position has its cost counted and
no revenue yet, which would read as a loss that is not one.

READ-ONLY. It places no order, needs no key, and touches no wallet.
"""
import collections
import json
import os
import statistics as st
import time
import urllib.parse
import urllib.request

API = "https://data-api.polymarket.com"
# How much of a wallet's dollar flow may be unattributable MERGE/SPLIT
# before the wallet is refused outright.
MAX_UNATTRIBUTABLE = float(os.environ.get("POLYCOPY_MAX_UNATTR") or 0.02)
MIN_CLOSED = int(os.environ.get("POLYCOPY_MIN_CLOSED") or 20)
WALLETS = int(os.environ.get("POLYCOPY_WALLETS") or 40)
PERMS = int(os.environ.get("POLYCOPY_PERMS") or 400)
SHARE_TOL = 1e-6


def get(path, **params):
    """One GET, retried. USES requests, NOT urllib: urllib came back 403
    from this environment's HTTPS proxy on every call while curl and
    requests both worked, and the rest of this repo already uses requests.
    """
    import requests
    url = f"{API}{path}"
    last = None
    for attempt in range(4):
        try:
            r = requests.get(url, params=params, timeout=30,
                             headers={"accept": "application/json"})
            if r.status_code == 429:
                time.sleep(2.0 * (attempt + 1))
                last = "429"
                continue
            if not r.ok:
                last = f"HTTP {r.status_code}"
                time.sleep(1.0 * (attempt + 1))
                continue
            return r.json()
        except Exception as e:
            last = str(e)[:90]
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET {path} failed: {last}")


def rows_of(d):
    return d if isinstance(d, list) else ((d or {}).get("data") or [])


def paged(path, **params):
    """Every page. A single page read as 'all history' is a mistake this
    project has made twice before, on fills and on settlements."""
    out, cursor, n = [], None, 0
    while n < 60:
        p = dict(params)
        if cursor:
            p["cursor"] = cursor
        d = get(path, **p)
        rs = rows_of(d)
        out.extend(rs)
        n += 1
        cursor = (d or {}).get("next_cursor") if isinstance(d, dict) else None
        if not cursor or not rs:
            break
        time.sleep(0.12)
    return out


def f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def score_positions(rows):
    """Final P&L per RESOLVED position, from Polymarket's own accounting.

    WHY THIS REPLACED MY OWN RECONSTRUCTION. The first version rebuilt P&L
    from the activity feed and only scored positions where
    bought-sold-redeemed reached zero. That dropped 41.7% of positions as
    "still open" -- and a resolved position whose worthless shares were
    never redeemed looks exactly like an open one. So losses were being
    discarded while wins (which get redeemed, because redeeming pays) were
    kept. It produced p=0.000 and a sample where almost every wallet was
    profitable, in a market that is close to zero-sum. That is the shape of
    survivorship, not skill.

    The positions endpoint carries the exchange's own realized and
    unrealized P&L, the cost basis and the entry fees, plus a status:
    REDEEMABLE means the market has resolved and the number is final,
    whether or not the shares were ever claimed. OPEN means it has not.
    Only RESOLVED positions are scored, so a loser counts whether or not
    its owner bothered to collect nothing.
    """
    out, open_n = [], 0
    for r in rows:
        status = str(r.get("status") or "").upper()
        cost = f(r.get("total_cost_usdc"))
        pnl = f(r.get("total_pnl"))
        first = f(r.get("first_entry_at"))
        if status != "REDEEMABLE":
            open_n += 1
            continue
        if cost is None or pnl is None or first is None or cost <= 0:
            continue
        out.append(dict(first=first, cost=cost, profit=pnl))
    return out, open_n


GAMMA = "https://gamma-api.polymarket.com"


def gamma(path, **params):
    import requests
    for attempt in range(4):
        try:
            r = requests.get(f"{GAMMA}{path}", params=params, timeout=30,
                             headers={"accept": "application/json"})
            if r.ok:
                return r.json()
            time.sleep(1.0 * (attempt + 1))
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return None


def historical_wallets(want, lo_date, hi_date):
    """Wallets that traded in markets which CLOSED in a past window.

    WHY THIS EXISTS. The recent-trade feed can only return wallets that are
    trading NOW, so every wallet it offers is a survivor. A wallet with a
    brilliant first half that blew up and stopped can never appear, and that
    absence alone could manufacture the persistence the elite tail shows.
    Sampling from markets that resolved months ago includes the quitters,
    because their trades are still in those markets' histories.
    """
    # FILTER AT THE SERVER. Paging the whole closed-market list with offset
    # returns the oldest first (2020-21 at offset 0, 2024 by offset 2000) and
    # 422s past about 6000, so the target window is never reached that way --
    # the first attempt found 0 markets for exactly this reason.
    # end_date_min / end_date_max are supported and do it directly.
    mk, offset = [], 0
    while len(mk) < 4000 and offset < 5500:
        rows = gamma("/markets", closed="true", limit=500, offset=offset,
                     end_date_min=lo_date, end_date_max=hi_date)
        rows = rows if isinstance(rows, list) else ((rows or {}).get("data")
                                                    or [])
        if not rows:
            break
        for m in rows:
            end = str(m.get("endDate") or "")[:10]
            cid = str(m.get("conditionId") or m.get("condition_id") or "")
            if cid and lo_date <= end <= hi_date:
                mk.append(cid)
        offset += 500
        time.sleep(0.1)
    import random as _r
    _r.Random(1).shuffle(mk)
    print(f"  {len(mk)} market(s) closing between {lo_date} and {hi_date}")
    seen = collections.Counter()
    used = 0
    for cid in mk:
        if len(seen) >= want:
            break
        try:
            d = get("/v2/trades", condition_id=cid, limit=1000)
        except Exception:
            continue
        used += 1
        for r in rows_of(d):
            w = str(r.get("proxy_wallet") or "")
            if w:
                seen[w] += 1
        if used % 25 == 0:
            print(f"    {used} market(s) read, {len(seen)} wallet(s)")
        time.sleep(0.05)
    print(f"  {used} market(s) read, {len(seen)} distinct wallet(s)")
    return seen


def score_union(acts, pos):
    """Every RESOLVED position, from both endpoints, each counted once.

    WHY A UNION IS REQUIRED, measured rather than assumed. Of the redeemed
    positions found in the activity feed across four wallets -- 237 winners
    and 118 losers -- ZERO still appeared in /positions. Redeeming removes a
    position from that endpoint. So:

      /activity REDEEM events  = the CLAIMED positions. Skewed to winners,
                                 because claiming a winner pays and claiming
                                 a loser costs gas to collect nothing.
      /positions REDEEMABLE    = the UNCLAIMED resolved ones. Skewed to
                                 losers, for the same reason.

    Scoring either alone picks a side. My first version used activity-closure
    and found almost every wallet profitable (p=0.000). The second used
    REDEEMABLE and found a median of -100%. Both were the same bug seen from
    opposite ends, and the sanity check is what caught the second one.

    So: take every REDEEMABLE position from /positions, add every
    activity-resolved position whose token is NOT among them, and score the
    two together. OPEN positions are excluded -- their cost is spent and
    their revenue has not happened.
    """
    gross = unattr = 0.0
    p_by_tok = {}
    for r in pos:
        tok = str(r.get("token_id") or "")
        if tok:
            p_by_tok[tok] = r

    out, seen_tok, open_n = [], set(), 0
    # 1. the resolved-but-unclaimed side, with the exchange's own P&L
    for tok, r in p_by_tok.items():
        status = str(r.get("status") or "").upper()
        if status != "REDEEMABLE":
            open_n += 1
            continue
        cost, pnl = f(r.get("total_cost_usdc")), f(r.get("total_pnl"))
        first = f(r.get("first_entry_at"))
        if cost is None or pnl is None or first is None or cost <= 0:
            continue
        out.append(dict(first=first, cost=cost, profit=pnl, src="unclaimed"))
        seen_tok.add(tok)

    # 2. the claimed side, rebuilt from the activity cash flows
    acc = collections.defaultdict(
        lambda: dict(bought=0.0, sold=0.0, redeemed=0.0, redeems=0,
                     cash_out=0.0, cash_in=0.0, first=None))
    for a in acts:
        t = str(a.get("type") or "").upper()
        usd = f(a.get("usdc_size")) or 0.0
        if t in ("MERGE", "SPLIT"):
            unattr += abs(usd)
            gross += abs(usd)
            continue
        if t == "YIELD" or t not in ("TRADE", "REDEEM"):
            continue
        tok = str(a.get("token_id") or "")
        if not tok:
            unattr += abs(usd)
            gross += abs(usd)
            continue
        sz = f(a.get("size")) or 0.0
        ts = f(a.get("timestamp"))
        p = acc[tok]
        if t == "REDEEM":
            p["redeemed"] += sz
            p["redeems"] += 1
            p["cash_in"] += usd
            gross += abs(usd)
            continue
        px = f(a.get("price"))
        if px is None:
            continue
        cash = sz * px
        gross += cash
        side = str(a.get("side") or "").upper()
        if side == "BUY":
            p["bought"] += sz
            p["cash_out"] += cash
            if p["first"] is None or (ts is not None and ts < p["first"]):
                p["first"] = ts
        elif side == "SELL":
            p["sold"] += sz
            p["cash_in"] += cash

    if gross <= 0:
        return [], 0, "no attributable flow"
    share = unattr / gross
    if share > MAX_UNATTRIBUTABLE:
        return [], open_n, (f"{100*share:.1f}% of flow is unattributable "
                            f"MERGE/SPLIT")

    for tok, p in acc.items():
        if tok in seen_tok or p["first"] is None or p["cash_out"] <= 0:
            continue
        # Resolved here means the shares are gone: sold off, or redeemed.
        # A redeem of any size settles the position, including a $0 redeem
        # of a loser -- which is the case the first version kept dropping.
        if p["redeems"] == 0 and (p["bought"] - p["sold"]) > SHARE_TOL:
            continue
        out.append(dict(first=p["first"], cost=p["cash_out"],
                        profit=p["cash_in"] - p["cash_out"], src="claimed"))
    return out, open_n, f"{100*share:.2f}% unattributable"


def score_wallet(acts):
    """Realized P&L per CLOSED position, or a refusal reason.

    Returns (positions, note) where positions is a list of dicts with the
    position's first-buy time, cost and profit, or ([], reason).
    """
    gross = unattr = 0.0
    pos = collections.defaultdict(
        lambda: dict(bought=0.0, sold=0.0, redeemed=0.0,
                     cash_out=0.0, cash_in=0.0, first=None))
    for a in acts:
        t = str(a.get("type") or "").upper()
        usd = f(a.get("usdc_size")) or 0.0
        if t in ("MERGE", "SPLIT"):
            unattr += abs(usd)
            gross += abs(usd)
            continue
        if t == "YIELD":
            continue
        if t not in ("TRADE", "REDEEM"):
            continue
        tok = str(a.get("token_id") or "")
        if not tok:
            unattr += abs(usd)
            gross += abs(usd)
            continue
        sz = f(a.get("size")) or 0.0
        px = f(a.get("price"))
        ts = f(a.get("timestamp"))
        p = pos[tok]
        if t == "REDEEM":
            p["redeemed"] += sz
            p["cash_in"] += usd
            gross += abs(usd)
            continue
        side = str(a.get("side") or "").upper()
        if px is None:
            continue
        cash = sz * px
        gross += cash
        if side == "BUY":
            p["bought"] += sz
            p["cash_out"] += cash
            if p["first"] is None or (ts is not None and ts < p["first"]):
                p["first"] = ts
        elif side == "SELL":
            p["sold"] += sz
            p["cash_in"] += cash
        else:
            continue

    if gross <= 0:
        return [], "no attributable flow"
    share = unattr / gross
    if share > MAX_UNATTRIBUTABLE:
        return [], (f"{100*share:.1f}% of flow is unattributable "
                    f"MERGE/SPLIT")

    out = []
    for tok, p in pos.items():
        if p["first"] is None or p["cash_out"] <= 0:
            continue
        left = p["bought"] - p["sold"] - p["redeemed"]
        if left > SHARE_TOL:
            continue                      # still open; cost without revenue
        out.append(dict(first=p["first"], cost=p["cash_out"],
                        profit=p["cash_in"] - p["cash_out"]))
    return out, f"{100*share:.2f}% unattributable"


def roi(ps):
    c = sum(p["cost"] for p in ps)
    return (sum(p["profit"] for p in ps) / c) if c > 0 else None


def main():
    print("=" * 100)
    print("DOES COPYING A POLYMARKET TRADER WORK? -- part 1, persistence")
    print("=" * 100)
    print("  READ-ONLY: no order, no key, no wallet.\n")

    # Candidate wallets come from the public trade feed. There is no
    # documented leaderboard endpoint, and picking wallets BY PROFIT would
    # guarantee a profitable first half -- which is the thing being tested.
    # Selecting on ACTIVITY instead is neutral with respect to the outcome.
    print("  finding wallets from the public trade feed...")
    # TWO FIXES OVER THE FIRST VERSION, both of which cost real sample.
    #
    # 1. THE FEED MUST BE PAGINATED. Calling it six times with no cursor
    #    returned the same page six times -- 3,000 rows that were really
    #    500, and 267 wallets that looked like a wide sample.
    # 2. DO NOT TAKE THE MOST ACTIVE. The busiest wallets are market makers,
    #    which is precisely who MERGE and SPLIT positions all day and
    #    therefore cannot be scored per position: four of the nine refusals
    #    in the first run were the most active wallets. Selecting on volume
    #    selects for unscoreable wallets AND for a different business than
    #    the one being tested. A deterministic spread across everything
    #    seen is neutral.
    # THE FEED IS NOT PAGINATED, AND limit CAPS AT 1000. Probed: offset is
    # rejected with HTTP 400, no next_cursor is returned, limit=2000 is a
    # 400, and limit=1000 succeeds with ~460 distinct wallets. So a single
    # snapshot is all one call can give, and the way to widen the pool is to
    # POLL OVER TIME: each snapshot holds the most recent trades, so fresh
    # wallets appear as fresh trades arrive.
    #
    # AND NO "2+ TRADES" FILTER. The first version required a wallet to
    # appear twice in one snapshot, which threw away three quarters of the
    # candidates for nothing: appearing once in a 1,000-trade window says
    # nothing about how much history a wallet has, and MIN_CLOSED already
    # requires a real history before a wallet is scored.
    source = (os.environ.get("POLYCOPY_SOURCE") or "recent").lower()
    if source == "historical":
        lo = os.environ.get("POLYCOPY_FROM") or "2026-01-01"
        hi = os.environ.get("POLYCOPY_TO") or "2026-06-30"
        print(f"  SOURCE: historical markets, survivorship-free "
              f"({lo} to {hi})")
        seen = historical_wallets(WALLETS * 3, lo, hi)
        pool = sorted(seen)
        import random as _r0
        _r0.Random(0).shuffle(pool)
        cands = pool[:WALLETS]
        print(f"  sampling {len(cands)} of {len(pool)}\n")
    else:
        cands = None
    seen = collections.Counter()
    polls = int(os.environ.get("POLYCOPY_POLLS") or 6)
    gap = float(os.environ.get("POLYCOPY_POLL_GAP") or 20)
    for i in range(polls if cands is None else 0):
        d = get("/v2/trades", limit=1000)
        for r in rows_of(d):
            w = str(r.get("proxy_wallet") or "")
            if w:
                seen[w] += 1
        if i < polls - 1:
            time.sleep(gap)
    if cands is None:
        pool = sorted(seen)
        import random as _r
        _r.Random(0).shuffle(pool)
        cands = pool[:WALLETS]
        print(f"  {polls} snapshot(s), {len(seen)} distinct wallet(s), "
              f"sampling {len(cands)}\n")

    good, refused = [], []
    tot_open = claimed = unclaimed = 0
    for i, w in enumerate(cands):
        try:
            acts = paged("/v2/activity", user=w, limit=500,
                         full_history="true")
            rows = paged("/v2/positions", user=w, limit=500,
                         full_history="true")
        except Exception as e:
            refused.append((w, f"fetch failed: {str(e)[:60]}"))
            continue
        ps, open_n, note = score_union(acts, rows)
        tot_open += open_n
        if len(ps) < MIN_CLOSED:
            refused.append((w, f"{len(ps)} resolved position(s), "
                               f"{open_n} open ({note})"))
            continue
        src = collections.Counter(x["src"] for x in ps)
        claimed += src.get("claimed", 0)
        unclaimed += src.get("unclaimed", 0)
        ps.sort(key=lambda p: p["first"])
        cut = len(ps) // 2
        a, b = roi(ps[:cut]), roi(ps[cut:])
        if a is None or b is None:
            refused.append((w, "half with no cost"))
            continue
        hit_a = (100.0 * sum(1 for x in ps[:cut] if x["profit"] > 0)
                 / max(1, cut))
        hit_b = (100.0 * sum(1 for x in ps[cut:] if x["profit"] > 0)
                 / max(1, len(ps) - cut))
        good.append(dict(w=w, n=len(ps), first=a, second=b,
                         hit_a=hit_a, hit_b=hit_b,
                         cost=sum(p["cost"] for p in ps),
                         profit=sum(p["profit"] for p in ps)))
        print(f"  {w[:14]}..  {len(ps):>4} resolved  "
              f"1st ROI {100*a:>+8.2f}% (hit {hit_a:>5.1f}%)   "
              f"2nd {100*b:>+8.2f}% (hit {hit_b:>5.1f}%)   "
              f"${sum(p['profit'] for p in ps):>+9,.0f}")

    print(f"\n  {len(good)} wallet(s) scored, {len(refused)} refused")
    for w, why in refused[:10]:
        print(f"    {w[:14]}.. {why}")
    if len(good) < 8:
        print("\n  TOO FEW WALLETS TO TEST PERSISTENCE. Nothing is claimed:")
        print("  this is an absence of data, not a finding about copying.")
        print("=" * 100)
        return 0

    # ------------------------------------------------ THE ZERO-SUM SANITY CHECK
    # Polymarket is close to zero-sum: one side's gain is the other's loss,
    # minus fees. So a sample of wallets chosen for ACTIVITY, not for profit,
    # must come out roughly break-even or slightly negative. If it comes out
    # broadly profitable, the measurement is selecting winners somewhere and
    # the persistence test below would be measuring that selection.
    #
    # This is the check the first version did not have. It reported p=0.000
    # on a sample where almost every wallet made money, and the reason was
    # that unredeemed losers were being dropped. A result has to survive
    # this before the null test means anything.
    wins = sum(1 for g in good if g["profit"] > 0)
    pooled_cost = sum(g["cost"] for g in good)
    pooled_profit = sum(g["profit"] for g in good)
    pooled_roi = (100.0 * pooled_profit / pooled_cost) if pooled_cost else 0.0
    print(f"\n{'-' * 100}")
    print("ZERO-SUM SANITY CHECK -- does a neutral sample break even?")
    print("-" * 100)
    print(f"  wallets profitable: {wins} of {len(good)} "
          f"({100.0*wins/len(good):.1f}%)")
    print(f"  pooled: ${pooled_profit:+,.0f} on ${pooled_cost:,.0f} staked "
          f"= {pooled_roi:+.2f}%")
    print(f"  positions still open and excluded: {tot_open:,}")
    print(f"  resolved positions scored: {claimed:,} claimed "
          f"(from activity) + {unclaimed:,} unclaimed (from positions)")
    if claimed == 0 or unclaimed == 0:
        print("  *** ONE SIDE IS MISSING ENTIRELY. Claimed positions skew to")
        print("  winners and unclaimed to losers, so a sample drawn from only")
        print("  one of them is selected by outcome. Not trustworthy.")
    biased = wins > 0.70 * len(good) or pooled_roi > 15.0
    if biased:
        print("\n  *** THIS SAMPLE IS NOT NEUTRAL. In a near zero-sum market")
        print("  a sample picked for activity cannot be this profitable, so")
        print("  the measurement is still selecting winners. The")
        print("  persistence figures below are NOT trustworthy and no")
        print("  finding is claimed from them.")
    else:
        print("\n  consistent with a near zero-sum market, so the sample is")
        print("  not obviously selecting winners.")

    # ---------------------------------------------------- the persistence test
    good.sort(key=lambda g: -g["first"])
    half = len(good) // 2
    top, bot = good[:half], good[half:]
    t2 = st.median([g["second"] for g in top])
    b2 = st.median([g["second"] for g in bot])
    base = st.median([g["second"] for g in good])
    edge = 100.0 * (t2 - base)
    print(f"\n{'-' * 100}")
    print("PERSISTENCE: rank on the FIRST half, score on the SECOND")
    print("-" * 100)
    print(f"  better first half ({len(top)} wallets): "
          f"median 2nd-half ROI {100*t2:+.2f}%")
    print(f"  worse  first half ({len(bot)} wallets): "
          f"median 2nd-half ROI {100*b2:+.2f}%")
    print(f"  all wallets baseline:                  {100*base:+.2f}%")
    print(f"  edge of picking on the first half:     {edge:+.2f} points")

    # The same permutation null used on the pump.fun features: shuffle which
    # wallet had the good first half, re-run the split, and see how often
    # chance produces an edge this large. Calibrated on noise beforehand.
    import random
    random.seed(0)
    seconds = [g["second"] for g in good]
    null = []
    for _ in range(PERMS):
        s = seconds[:]
        random.shuffle(s)
        null.append(100.0 * (st.median(s[:half]) - st.median(s)))
    null.sort()
    worse = sum(1 for e in null if e >= edge)
    p = worse / len(null)
    print(f"\n  PERMUTATION TEST, {len(null)} shuffles:")
    print(f"    no-signal edge: median {null[len(null)//2]:+.2f}, "
          f"90th {null[9*len(null)//10]:+.2f}, max {null[-1]:+.2f} points")
    print(f"    the real edge is {edge:+.2f}, beaten by {worse} "
          f"of {len(null)}   p = {p:.3f}")

    # ------------------------------------------------- THE ELITE TAIL
    # THE MEDIAN SPLIT ANSWERS A DIFFERENT QUESTION. It asks whether
    # better-than-average wallets persist, and they do not persist into
    # profit. It is blind by construction to a small elite tail: the top few
    # of fifty-odd is a bucket of five, which is the best-of-N trap that has
    # produced every false positive in this project.
    #
    # With a large enough sample the top decile is itself a real sample, so
    # it can be asked honestly -- with the same permutation null, built to
    # mirror this exact procedure including the bucket size.
    elite_hits = []
    for frac, label in ((0.10, "top 10%"), (0.20, "top 20%")):
        k = int(len(good) * frac)
        if k < 8:
            print(f"\n  {label}: only {k} wallet(s) -- too few to test "
                  f"honestly, so it is not tested.")
            continue
        elite = good[:k]
        e2 = st.median([g["second"] for g in elite])
        e_edge = 100.0 * (e2 - base)
        e_hit = st.median([g["hit_b"] for g in elite])
        null_e = []
        for _ in range(PERMS):
            sh = seconds[:]
            random.shuffle(sh)
            null_e.append(100.0 * (st.median(sh[:k]) - st.median(sh)))
        null_e.sort()
        worse_e = sum(1 for e in null_e if e >= e_edge)
        pe = worse_e / len(null_e)
        print(f"\n{'-' * 100}")
        print(f"ELITE TAIL: {label} by first-half ROI ({k} wallets), "
              f"scored on the second half")
        print("-" * 100)
        print(f"  median 2nd-half ROI {100*e2:+.2f}%   "
              f"median 2nd-half hit rate {e_hit:.1f}%")
        print(f"  edge over baseline  {e_edge:+.2f} points")
        print(f"  no-signal edge: median {null_e[len(null_e)//2]:+.2f}, "
              f"90th {null_e[9*len(null_e)//10]:+.2f}, "
              f"max {null_e[-1]:+.2f}")
        print(f"  p = {pe:.3f}")
        if pe <= 0.05 and e2 > 0:
            elite_hits.append((label, 100.0 * e2, e_hit, pe, k))
            print("  *** PROFITABLE AND BEATS THE NULL. This is the one")
            print("  result that would justify measuring the follower lag.")
        elif e2 <= 0:
            print("  beats the null or not, this group still loses money,")
            print("  so there is nothing for a copier to keep.")

    print(f"\n{'=' * 100}")
    if biased:
        print("  NO CONCLUSION. The sanity check above failed, so whatever")
        print("  the p-value says, this is measuring a selection effect and")
        print("  not persistence. Fix the measurement before reading it.")
        print("=" * 100)
        return 1
    if p > 0.10:
        print("  NO PERSISTENCE. Picking a wallet on its past record does")
        print("  not predict its future record any better than chance, so")
        print("  there is nothing to copy and the lag question is moot.")
    elif p > 0.05:
        print("  WEAK. Worth re-testing on more wallets before anything is")
        print("  built on it; not worth following money.")
    elif elite_hits:
        print("  THE MEDIAN SPLIT IS USELESS BUT THE TOP TAIL IS NOT.")
        print(f"  Better-than-average wallets still lose "
              f"{abs(100*t2):.2f}%, so ranking loosely is worthless. But:")
        for label, roi_, hit_, pe_, k_ in elite_hits:
            print(f"    {label} ({k_} wallets): {roi_:+.2f}% in the second "
                  f"half, hit {hit_:.1f}%, p={pe_:.3f}")
        print("  THREE THINGS THIS IS STILL NOT:")
        print("  1. A COPIER'S RETURN. That is the trader's own ROI at the")
        print("     trader's own entry price. A follower buys later and")
        print("     worse, and that cost is UNMEASURED. It is now worth")
        print("     measuring -- that is part 2.")
        print("  2. FREE OF SURVIVORSHIP. Candidates come from the RECENT")
        print("     trade feed, so every wallet here is still trading. A")
        print("     wallet with a brilliant first half that blew up and")
        print("     quit never enters the sample, and that absence alone")
        print("     could manufacture persistence. This is the single")
        print("     biggest threat to the result and it is not yet fixed.")
        print("  3. ONE TEST. Three buckets were tried (median, top 20%,")
        print("     top 10%); each null is mirrored to its own bucket but")
        print("     the choice among them is not penalised.")
    elif t2 <= 0:
        # THE GATE THAT MATTERS. "Better than the other wallets" is not
        # "makes money". If the group you would copy is itself losing, then
        # copying it loses, and the lag cost of following can only subtract
        # further -- so part 2 cannot rescue it and is not worth building.
        print("  PERSISTENCE IS REAL BUT USELESS. Ranking on the past does")
        print(f"  beat chance (p={p:.3f}), yet the wallets you would copy")
        print(f"  still LOSE {abs(100*t2):.2f}% on their own trades. Copying")
        print("  a loser more accurately is still losing, and a follower")
        print("  enters later than the wallet did, which only subtracts.")
        print("  Part 2 is not worth building: there is nothing to keep.")
    else:
        print("  PERSISTENCE SURVIVES THE NULL AND THE GROUP IS PROFITABLE.")
        print("  Part 2 is now worth building: whether a follower entering")
        print("  minutes late keeps any of it. Until that is measured this")
        print("  is NOT a strategy, because every copied entry is late.")
    print("\n  AND NOTE WHAT THIS CANNOT SEE. Wallets are not people and one")
    print("  person may run several. A wallet may be hedging somewhere this")
    print("  cannot observe. Only resolved positions are scored.")
    print("  MOST IMPORTANTLY: wallets whose flow is largely MERGE/SPLIT are")
    print("  refused, because their positions cannot be attributed -- and")
    print("  those are the market makers, who are plausibly the profitable")
    print("  cohort. So this measures whether COPYABLE retail wallets")
    print("  persist, not whether anyone on Polymarket makes money.")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
