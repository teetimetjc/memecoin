"""Which pump.fun / Solana data sources actually answer, and in what shape?

WHY A PROBE BEFORE A SCANNER. Twice this week a reader was written against
field names that seemed obvious and were wrong: a deposit's amount is
`amount_cents` not `amount`, a fill's size is `count_fp` not `count`, and a
settlement's `revenue` is cents while the field beside it is dollars. Both
times the code summed nothing, reported zero, and printed a confident
total built from no data.

So nothing gets built on these endpoints until they have been asked what
they return. This fetches each candidate, prints the HTTP status, the shape
of the payload, and the KEYS of one record verbatim. It interprets nothing.

It also costs nothing to be wrong here: every call is a public GET, no key
is used, no wallet is touched, no order exists.

Read-only in the strongest sense -- it has no credentials to be read-only
WITH.
"""
import json
import os
import sys

import requests

TIMEOUT = 20
UA = {"User-Agent": "memecoin-research/1.0"}

# Candidates, each with what it is hoped to provide. None is assumed to
# work; the run reports which do.
SOURCES = [
    ("pump.fun newest coins",
     "https://frontend-api.pump.fun/coins"
     "?offset=0&limit=5&sort=created_timestamp&order=DESC&includeNsfw=false"),
    ("pump.fun newest (v3 host)",
     "https://frontend-api-v3.pump.fun/coins"
     "?offset=0&limit=5&sort=created_timestamp&order=DESC&includeNsfw=false"),
    ("pumpportal data",
     "https://pumpportal.fun/api/data"),
    ("dexscreener latest token profiles",
     "https://api.dexscreener.com/token-profiles/latest/v1"),
    ("dexscreener latest boosts",
     "https://api.dexscreener.com/token-boosts/latest/v1"),
    ("dexscreener search 'pump'",
     "https://api.dexscreener.com/latest/dex/search?q=pump"),
    ("jupiter token list (tradable)",
     "https://tokens.jup.ag/tokens?tags=verified"),
]


def shape(obj, depth=0):
    """A one-line description of what came back, without interpreting it."""
    if isinstance(obj, list):
        return f"list[{len(obj)}]" + (
            f" of {shape(obj[0], depth+1)}" if obj and depth < 2 else "")
    if isinstance(obj, dict):
        if depth >= 2:
            return f"dict[{len(obj)} keys]"
        return "dict{" + ", ".join(sorted(obj)[:14]) + (
            ", ..." if len(obj) > 14 else "") + "}"
    return type(obj).__name__


def first_record(obj):
    """The first dict that looks like a row, wherever it is nested."""
    if isinstance(obj, dict):
        for k in ("coins", "pairs", "data", "results", "tokens"):
            v = obj.get(k)
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v[0]
        return obj
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        return obj[0]
    return None


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    print("=" * 100)
    print("PUMP.FUN / SOLANA DATA SOURCE PROBE")
    print("  Asks each endpoint what it returns. Builds nothing, assumes "
          "nothing, uses no credentials.")
    print("=" * 100)

    working = []
    for name, url in SOURCES:
        if only and only.lower() not in name.lower():
            continue
        print(f"\n{'-' * 100}")
        print(f"{name}")
        print(f"  {url[:96]}")
        try:
            r = requests.get(url, headers=UA, timeout=TIMEOUT)
        except Exception as e:
            print(f"  FAILED: {str(e)[:160]}")
            continue
        ct = r.headers.get("content-type", "")
        print(f"  HTTP {r.status_code}   {len(r.content):,} bytes   {ct[:60]}")
        if not r.ok:
            print(f"  body: {r.text[:200]}")
            continue
        if "json" not in ct.lower():
            print(f"  NOT JSON -- first 200 chars: {r.text[:200]!r}")
            continue
        try:
            d = r.json()
        except Exception as e:
            print(f"  JSON PARSE FAILED: {str(e)[:120]}")
            continue

        print(f"  shape: {shape(d)}")
        rec = first_record(d)
        if not isinstance(rec, dict):
            print("  no record-shaped object found")
            continue
        working.append((name, url, sorted(rec)))
        print(f"  ONE RECORD'S KEYS ({len(rec)}):")
        for k in sorted(rec):
            v = rec[k]
            sv = json.dumps(v, default=str)
            print(f"    {k:<34} {sv[:70]}")

    print("\n" + "=" * 100)
    if working:
        print(f"  {len(working)} source(s) answered with records:")
        for name, _u, keys in working:
            # Flag the fields a scanner would actually need.
            want = {
                "mint/address": [k for k in keys
                                 if k in ("mint", "address", "tokenAddress",
                                          "baseToken", "id")],
                "created time": [k for k in keys
                                 if "creat" in k.lower() or "pairCreated" in k],
                "price": [k for k in keys if "price" in k.lower()],
                "liquidity": [k for k in keys
                              if "liquid" in k.lower() or "reserve" in k.lower()
                              or "market_cap" in k.lower()],
            }
            print(f"\n    {name}")
            for label, found in want.items():
                print(f"      {label:<14} {found if found else 'NOT PRESENT'}")
        print()
        print("  The scanner gets built against whichever of these carries a")
        print("  mint, a creation time and a price. Anything missing a price")
        print("  needs a second call per token, which is a rate-limit problem")
        print("  rather than a detail.")
    else:
        print("  NOTHING ANSWERED. No scanner can be built on these sources")
        print("  from this runner; the next step is finding a source that")
        print("  does, not guessing at one that might.")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
