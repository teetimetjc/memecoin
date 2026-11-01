"""Test oddsmath.py against values worked out by hand. No key, no network.

WHY THIS RUNS FIRST AND ALONE. Every row the line-gap scanner writes is
oddsmath multiplied by a market reading. A sign error or a mis-stated fee
would not announce itself -- it would produce a log full of plausible gaps,
which is the failure mode this project keeps meeting. These values can be
checked on paper, so they are, before any quota is spent.

It exits non-zero on the first failure so a workflow cannot report success
over a broken calculation.
"""
import oddsmath as M

FAILED = []


def check(label, got, want, tol=1e-4):
    ok = abs(got - want) <= tol
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:<52} "
          f"got {got:.6f}  want {want:.6f}")
    if not ok:
        FAILED.append(label)


def check_true(label, cond, note=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"   {note}" if note else ""))
    if not cond:
        FAILED.append(label)


def main():
    print("=" * 84)
    print("ODDS MATH -- checked against hand-worked values")
    print("=" * 84)

    print("\nAmerican odds to raw implied probability")
    check("-110 -> 0.5238", M.american_to_prob(-110), 0.523810)
    check("+100 -> 0.5000", M.american_to_prob(100), 0.500000)
    check("-200 -> 0.6667", M.american_to_prob(-200), 0.666667)
    check("+150 -> 0.4000", M.american_to_prob(150), 0.400000)
    check("-500 -> 0.8333", M.american_to_prob(-500), 0.833333)

    print("\nRound trip through prob_to_american")
    for o in (-110, 100, -200, 150, -500, 275):
        back = M.prob_to_american(M.american_to_prob(o))
        check(f"{o:>5} -> prob -> american", back, float(o), tol=1e-6)

    print("\nThe -110/-110 market: 52.38 each raw, 50/50 fair")
    raws = [M.american_to_prob(-110), M.american_to_prob(-110)]
    check("raw sum (the vig)", sum(raws), 1.047619)
    for name, q in M.devig_all(raws).items():
        check(f"{name:<15} side A", q[0], 0.5)
        check(f"{name:<15} sums to 1", sum(q), 1.0, tol=1e-9)

    print("\nEvery method must sum to 1 on an ASYMMETRIC market (-200 / +165)")
    raws = [M.american_to_prob(-200), M.american_to_prob(165)]
    print(f"  raw: favourite {raws[0]:.4f}  longshot {raws[1]:.4f}  "
          f"sum {sum(raws):.4f}")
    res = M.devig_all(raws)
    for name, q in res.items():
        check(f"{name:<15} sums to 1", sum(q), 1.0, tol=1e-9)
        check_true(f"{name:<15} both sides in (0,1)",
                   all(0.0 < x < 1.0 for x in q))

    print("\nDO THE METHODS DISAGREE, AND BY HOW MUCH?")
    print("  (this is the number that decides whether a 3-point threshold "
          "means anything)")
    for name, q in res.items():
        print(f"    {name:<15} favourite {100*q[0]:6.2f}%   "
              f"longshot {100*q[1]:6.2f}%")
    lo, hi, spread = M.fair_consensus(raws, 1)
    print(f"    longshot spread across methods: {100*spread:.2f} points "
          f"({100*lo:.2f} to {100*hi:.2f})")
    check_true("methods disagree by a non-trivial amount",
               spread > 0.0,
               "-> this is why all three get logged, not just one")

    print("\nSymmetric markets must make the methods AGREE")
    raws = [M.american_to_prob(-110), M.american_to_prob(-110)]
    _lo, _hi, sp = M.fair_consensus(raws, 0)
    check("spread on a symmetric market is ~0", sp, 0.0, tol=1e-6)

    print("\nKalshi fee, rounded up at the ORDER level")
    check("1 contract @ 50c -> 2c", M.fee_cents(1, 0.50), 2, tol=0)
    check("10 contracts @ 50c -> 18c", M.fee_cents(10, 0.50), 18, tol=0)
    check("per contract @ 50c, 1 lot", M.fee_per_contract(1, 0.50), 0.02)
    check("per contract @ 50c, 10 lots", M.fee_per_contract(10, 0.50), 0.018)
    check("100 contracts @ 5c -> 34c", M.fee_cents(100, 0.05), 34, tol=0)
    check("per contract @ 5c, 100 lots ~ 0.0033",
          M.fee_per_contract(100, 0.05), 0.0034, tol=0.0002)
    check_true("rounding is UP, never down",
               M.fee_cents(1, 0.05) == 1,
               "0.33c of fee still costs a whole cent on one contract")

    print("\nNet edge in points")
    # Fair 55%, ask 50c, 10 lots: fee 1.8c, cost 51.8c, edge 3.2 points.
    check("fair 55% vs 50c ask, 10 lots", M.net_edge(0.55, 0.50, 10), 3.2,
          tol=0.01)
    check_true("a fairly-priced market shows ~zero edge",
               abs(M.net_edge(0.50, 0.50, 10)) < 2.0)
    check_true("edge falls as the ask rises",
               M.net_edge(0.55, 0.52, 10) < M.net_edge(0.55, 0.50, 10))

    print()
    print("=" * 84)
    if FAILED:
        print(f"  {len(FAILED)} CHECK(S) FAILED:")
        for f in FAILED:
            print(f"    - {f}")
        print("  The scanner must not run until these pass.")
        print("=" * 84)
        return 1
    print("  all checks passed")
    print("=" * 84)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
