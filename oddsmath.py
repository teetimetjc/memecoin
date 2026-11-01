"""American odds, vig removal and the Kalshi fee. Pure arithmetic, no network.

SEPARATED FROM THE SCANNER ON PURPOSE. Every number the line-gap project
produces is this file's output multiplied by a market reading, so a bias
here is a bias in every row of the log and in every decision taken from it.
Keeping it free of network calls means it can be tested exhaustively against
values worked out by hand, which oddstest.py does, and which runs with no
API key and no quota.

THE VIG REMOVAL IS THE DANGEROUS PART, and not for the obvious reason.

The obvious method -- scale both sides so they sum to 1 -- is called
proportional or multiplicative de-vigging, and it is BIASED. It assumes the
bookmaker spreads the margin evenly in proportion to price, and books do not:
they load more margin onto the longshot, because that is the side the public
buys. Proportional removal therefore hands the longshot too much fair
probability and the favourite too little.

That is the favourite-longshot bias, the same effect this project spent six
weeks trading at 90-96c on crypto. The size of it at ordinary moneylines is
roughly one to two percentage points -- against a three-point edge threshold.
So the choice of method can manufacture most of a "gap" on its own.

THE ANSWER IS NOT TO PICK THE BEST METHOD. There is no way to know which is
right for a given book on a given night. So all three are computed for every
market and all three are logged, and a gap counts only if it survives the
most conservative of them. A disagreement between methods is information:
it says the margin structure is doing the work, not the market.

  multiplicative  q_i = p_i / sum(p).  The naive one. Included because it is
                  what everyone does and what the hypothesis assumed.
  shin            Assumes some share z of volume is informed, and solves for
                  it. Designed for exactly this bias; shrinks longshots.
  power           q_i = p_i^k with k solved so the result sums to 1. A
                  different shape of correction, included so that agreement
                  between shin and power means something.

Solved by bisection rather than a closed form: the closed forms are easy to
misremember and hard to check, and bisection on a monotone function is
verifiable by eye and returns something that provably sums to 1.

Read-only arithmetic. Places nothing, fetches nothing.
"""
import math

# Kalshi's taker fee coefficient, verified against 162 of 163 real fills on
# the crypto series. NOT verified on sports series -- linegap.py prints a
# warning about that rather than assuming it carries over.
FEE_COEFF = 0.07


def american_to_prob(odds):
    """American odds to raw implied probability, vig included.

    -110 -> 110/(110+100) = 0.5238    +150 -> 100/(150+100) = 0.40
    """
    o = float(odds)
    if o == 0:
        raise ValueError("American odds cannot be zero")
    if o < 0:
        return (-o) / ((-o) + 100.0)
    return 100.0 / (o + 100.0)


def prob_to_american(p):
    """Inverse, for printing a fair line next to a book's line."""
    if not (0.0 < p < 1.0):
        raise ValueError(f"probability out of range: {p}")
    # STRICTLY GREATER, so that p == 0.5 returns +100 and not -100. Both
    # denote even money and the arithmetic is identical, but every book
    # writes +100, and a fair line printed as -100 beside a book's +100
    # reads as a disagreement where there is none.
    if p > 0.5:
        return -100.0 * p / (1.0 - p)
    return 100.0 * (1.0 - p) / p


def devig_multiplicative(raws):
    """Scale to sum 1. The naive method, and the biased one."""
    s = sum(raws)
    if s <= 0:
        raise ValueError("probabilities must be positive")
    return [p / s for p in raws]


def devig_power(raws, tol=1e-12, iters=200):
    """q_i = p_i ** k, with k solved so the result sums to 1.

    sum(p_i**k) decreases as k rises for p_i < 1, so the function is
    monotone and bisection cannot get lost.
    """
    if any(p <= 0 or p >= 1 for p in raws):
        raise ValueError("power de-vig needs every raw probability in (0,1)")
    lo, hi = 1.0, 1.0
    # Expand the bracket until the sum crosses 1 rather than assuming a range.
    while sum(p ** hi for p in raws) > 1.0 and hi < 64.0:
        hi *= 2.0
    if sum(p ** hi for p in raws) > 1.0:
        raise ValueError("power de-vig failed to bracket a solution")
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        if sum(p ** mid for p in raws) > 1.0:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    k = 0.5 * (lo + hi)
    return [p ** k for p in raws]


def _shin_q(raws, z, s):
    """Shin's fair probabilities for a given informed-trade share z."""
    out = []
    for p in raws:
        inner = z * z + 4.0 * (1.0 - z) * (p * p) / s
        out.append((math.sqrt(inner) - z) / (2.0 * (1.0 - z)))
    return out


def devig_shin(raws, tol=1e-12, iters=200):
    """Shin (1993): solve for the share of informed volume, z.

    Shrinks longshots more than favourites, which is the correction the
    favourite-longshot bias calls for. z=0 reduces to multiplicative, so a
    market with no detectable asymmetry returns the naive answer and the
    three methods agree -- which is why agreement is meaningful.
    """
    s = sum(raws)
    if s <= 1.0:
        # No vig to remove (or a crossed book). Nothing to solve for.
        return devig_multiplicative(raws)
    lo, hi = 0.0, 0.99
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        if sum(_shin_q(raws, mid, s)) > 1.0:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return _shin_q(raws, 0.5 * (lo + hi), s)


def devig_all(raws):
    """Every method, keyed by name. The scanner logs all of them."""
    return {
        "multiplicative": devig_multiplicative(raws),
        "shin": devig_shin(raws),
        "power": devig_power(raws),
    }


def fair_consensus(raws, index):
    """(lowest, highest, spread) fair probability for one side.

    THE LOWEST IS THE ONE THAT DECIDES. An edge is fair minus cost, so the
    most conservative fair probability gives the most conservative edge, and
    a gap that survives it survives the method disagreement. The spread is
    logged beside it: a wide spread means the de-vig is doing the work.
    """
    vals = [m[index] for m in devig_all(raws).values()]
    return min(vals), max(vals), max(vals) - min(vals)


def fee_cents(contracts, price):
    """Kalshi taker fee in whole cents, rounded UP at the ORDER level.

    The rounding is per order, not per contract, so the per-contract cost
    falls as size rises: one contract at 50c pays 2c, but ten pay 18c, which
    is 1.8c each. An edge computed with a per-contract 2c is pessimistic at
    size and would hide real gaps.
    """
    if contracts <= 0:
        return 0
    if not (0.0 < price < 1.0):
        raise ValueError(f"price out of range: {price}")
    exact = FEE_COEFF * contracts * price * (1.0 - price)
    return int(math.ceil(exact * 100.0 - 1e-9))


def fee_per_contract(contracts, price):
    """The same fee expressed per contract, in probability points.

    Edge is in points, so the fee has to be too, or the subtraction is
    comparing a probability with a price.
    """
    if contracts <= 0:
        return 0.0
    return fee_cents(contracts, price) / 100.0 / contracts


def net_edge(fair_prob, ask_price, contracts):
    """Edge in PERCENTAGE POINTS, after the fee, for buying at the ask.

    Positive means the fair probability exceeds what the contract costs all
    in. Everything here is a quote: it says nothing about whether the
    contract can actually be bought, which is depth, and which killed the
    last two ideas this project tried.
    """
    cost = ask_price + fee_per_contract(contracts, ask_price)
    return 100.0 * (fair_prob - cost)
