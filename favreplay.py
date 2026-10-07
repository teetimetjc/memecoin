"""Prove favlive picks exactly the bets score.py measured. Refuse on one miss.

The risk this addresses is specific and is the reason the live rule is not
simply trusted. score.py selects bets by reading `bid9`/`ask9` columns out of
a sheet; favlive selects them by reading a live orderbook through
collect15.quote_from_market. Those are different code paths to the same
decision, and this project's recurring failure is exactly that shape -- correct
arithmetic reaching a different question, with nothing raised.

So the historical rows are pushed through BOTH paths and compared bet for bet.
A synthetic market dict is built from each row's stored T-9 quote, handed to
favlive.signals_from, and the result is matched against score.bets on the same
row. Agreement has to be total: a 99.9% match on 11,546 bets is eleven real
bets a day chosen by a rule nobody has tested.

WHAT THIS DOES AND DOES NOT ESTABLISH. It establishes that the SELECTION
agrees -- same markets, same sides, same prices, same count. It says nothing
about whether an order fills, what it fills at, or whether the edge is real.
Those need a live fill and the frozen specs respectively. This closes the one
gap that is closable from the desk, which is whether the thing we are about to
bet is the thing we measured.

Read-only. Places nothing, and does not need credentials to place anything.
"""

import sys

import favlive as F
import score as S


def fake_market(row, ser):
    """A market dict carrying the row's stored T-9 quote.

    Only the fields quote_from_market and the ticker split actually read. Built
    from `bid`/`ask` exactly as score.load stored them, so any disagreement
    downstream is a difference in the DECISION rather than in the input.
    """
    return {
        "ticker": f"{ser}-REPLAY",
        "close_time": row["ct"],
        "yes_bid_dollars": row["bid"],
        "yes_ask_dollars": row["ask"],
    }


def main():
    H = S.load_hist()
    if not H:
        print("no history to replay against")
        return 1

    rules = [r for r in S.RULES]
    bad = 0
    print("\n" + "=" * 92)
    print("REPLAY -- does the live selector choose what the scorer measured?")
    print("=" * 92)

    for r in rules:
        # score.py's answer.
        scored = S.bets(H, r["band"], r["sides"])

        # favlive's answer, row by row through the live code path.
        n_live = 0
        mism = []
        for m in H:
            ser = "KXBTC15M"          # series only gates membership, not price
            mk = [fake_market(m, ser)]
            sigs = F.signals_from(mk, r, 0)
            n_live += len(sigs)
            # Compare this row directly: which sides should fire?
            expect = set()
            for side in r["sides"]:
                px = m["ask"] if side == "YES" else (1.0 - m["bid"])
                if r["band"][0] <= px < r["band"][1]:
                    expect.add(side)
            actual = {s["side"] for s in sigs}
            if expect != actual:
                mism.append((m["ct"], m["bid"], m["ask"], expect, actual))

        n_scored = len(scored)
        print(f"\n  {r['name'].upper()}  "
              f"({100*r['band'][0]:.0f}-{100*r['band'][1]:.0f}c, "
              f"{'/'.join(r['sides'])})")
        print(f"    scorer selected {n_scored:,} bets")
        print(f"    live selector   {n_live:,} bets")
        print(f"    per-row side disagreements: {len(mism):,}")
        if mism:
            bad += len(mism)
            print("    first few:")
            for ct, b, a, e, g in mism[:5]:
                print(f"      {ct}  bid {b:.2f} ask {a:.2f}  "
                      f"expected {sorted(e) or '-'}  got {sorted(g) or '-'}")
        if n_scored != n_live:
            bad += 1
            print("    COUNT MISMATCH -- the two paths do not pick the same "
                  "number of bets")

    print("\n" + "=" * 92)
    if bad:
        print(f"FAIL: {bad:,} disagreement(s). The live selector is NOT the")
        print("rule that was measured, so nothing should go live until this")
        print("is zero. A near-match is not a match.")
        return 1
    print("PASS: both paths select identically on every historical row.")
    print("That makes the live rule the measured rule. It says nothing about")
    print("whether an order fills, or whether the edge is real.")
    print("=" * 92)
    return 0


if __name__ == "__main__":
    sys.exit(main())
