"""Compare two curve accounts byte by byte. Read-only, no assumptions.

THIS IS THE TOOL THAT CRACKED IT, so it is kept rather than left as an
ad-hoc command. pumpmap tried to find the live fields by watching ONE
account move over time, and failed for a reason worth recording: freshly
launched tokens mostly do not trade, so nothing moved in the window and no
field could be told from a constant.

Comparing two accounts at DIFFERENT points on the curve needs no movement
and no waiting. Whatever differs between them is live state; whatever
matches across two accounts in different conditions is configuration.

WHAT IT FOUND, and why the 8-byte scan was misleading: the byte-level diff
shows 32 contiguous differing bytes starting at byte 83. Thirty-two bytes is
a Solana Pubkey -- the creator -- and it sits at an offset that is not a
multiple of 8, so every u64 read past byte 83 straddles it and returns
garbage. A scan that only looks at multiples of 8 cannot see this; it just
reports large meaningless numbers.

THE ARITHMETIC TEST, which needs no layout knowledge at all. If offset 16 is
virtual_sol_reserves -- and it is, because it reproduces the launch event's
own figure on fresh tokens -- then for two honest curves the token reserves
must sit in the INVERSE ratio of their SOL, because pump.fun initialises
every curve to the same constant product. So every byte offset is scanned
for a value in that ratio, and any candidate must also produce the curve
constant of ~3.2e10. A ratio match whose product is five orders out is a
misaligned coincidence, not a field.

Read-only. No wallet, no order, no sheet, no row written.
"""
import base64
import os
import struct
import sys

RPCS = [u.strip() for u in (os.environ.get("SOLANA_RPCS") or
        "https://api.mainnet-beta.solana.com").split(",") if u.strip()]
LAMPORTS = 1_000_000_000
TOKEN_DEC = 10 ** 6
SOL_OFF = int(os.environ.get("PUMPDIFF_SOL_OFF") or 16)
K_EXPECT = 30.0 * 1.073e9
TOL = float(os.environ.get("PUMPDIFF_TOL") or 0.02)


def fetch(keys):
    import requests
    last = ""
    for url in RPCS:
        try:
            r = requests.post(url, json={
                "jsonrpc": "2.0", "id": 1, "method": "getMultipleAccounts",
                "params": [keys, {"encoding": "base64",
                                  "commitment": "confirmed"}]}, timeout=30)
        except Exception as e:
            last = f"{type(e).__name__}: {str(e)[:70]}"
            continue
        if not r.ok:
            last = f"HTTP {r.status_code}"
            continue
        j = r.json() or {}
        if "error" in j:
            last = f"rpc error {str(j['error'])[:90]}"
            continue
        vals = ((j.get("result") or {}).get("value")) or []
        if len(vals) != len(keys):
            last = f"length {len(vals)} != {len(keys)}"
            continue
        return vals, ""
    return [], last


def main():
    keys = sys.argv[1:3]
    if len(keys) != 2:
        print("usage: pumpdiff.py <curveA> <curveB>")
        print("  two bonding-curve accounts at DIFFERENT points on the curve")
        return 2
    print("=" * 96)
    print("CURVE ACCOUNT DIFF -- what differs is state, what matches is "
          "config")
    print("=" * 96)
    vals, err = fetch(keys)
    if not vals or not all(vals):
        print(f"\n  could not read both accounts: {err or 'one was null'}")
        return 1
    blobs = [base64.b64decode(v["data"][0]) for v in vals]
    n = min(len(b) for b in blobs)
    print(f"\n  lengths {[len(b) for b in blobs]}")
    for v in vals:
        print(f"  owner {v['owner']}")

    print(f"\n  {'off':>5} {'A (u64)':>26} {'B (u64)':>26}  same?")
    for off in range(8, n - 7, 8):
        (a,) = struct.unpack_from("<Q", blobs[0], off)
        (b,) = struct.unpack_from("<Q", blobs[1], off)
        print(f"  {off:>5} {a:>26,} {b:>26,}  "
              f"{'SAME' if a == b else 'DIFFERS'}")

    # THE BYTE MAP IS THE POINT. An 8-aligned view cannot show a 32-byte
    # Pubkey sitting at a non-multiple of 8, and that is what breaks every
    # later read.
    print(f"\n  byte-level diff map ({n} bytes, X = differs):")
    m = "".join("X" if blobs[0][i] != blobs[1][i] else "."
                for i in range(n))
    for i in range(0, n, 32):
        print(f"    {i:>4}: {m[i:i + 32]}")
    runs, i = [], 0
    while i < n:
        if m[i] == "X":
            j = i
            while j < n and m[j] == "X":
                j += 1
            runs.append((i, j - i))
            i = j
        else:
            i += 1
    for start, length in runs:
        note = "  <- 32 bytes: a Pubkey" if length == 32 else ""
        print(f"    differing run at byte {start}, {length} byte(s){note}")
        if length == 32 and start % 8:
            print(f"       and {start} is NOT a multiple of 8, so every u64")
            print(f"       read past it straddles the key and is garbage.")

    # ------------------------------------------------- the arithmetic test
    (sa,) = struct.unpack_from("<Q", blobs[0], SOL_OFF)
    (sb,) = struct.unpack_from("<Q", blobs[1], SOL_OFF)
    solA, solB = sa / LAMPORTS, sb / LAMPORTS
    print(f"\n{'-' * 96}")
    print(f"WHERE IS THE TOKEN RESERVE? (vSol taken from offset {SOL_OFF})")
    print("-" * 96)
    print(f"  vSol A {solA:,.4f}   vSol B {solB:,.4f}")
    if not solA or not solB:
        print("  one account has zero there; the test cannot run.")
        return 0
    want = solB / solA
    print(f"  so vTokensA / vTokensB must be {want:.6f} for two honest "
          f"curves\n")
    hits = 0
    for off in range(0, n - 7):
        (va,) = struct.unpack_from("<Q", blobs[0], off)
        (vb,) = struct.unpack_from("<Q", blobs[1], off)
        if not va or not vb or va == vb:
            continue
        if abs((va / vb) / want - 1.0) > TOL:
            continue
        hits += 1
        kA, kB = solA * (va / TOKEN_DEC), solB * (vb / TOKEN_DEC)
        ok = 0.2 <= (kA / K_EXPECT) <= 5.0
        print(f"  byte {off:>4}: ratio {va / vb:.6f}, product "
              f"{kA:.4e} / {kB:.4e}")
        print(f"    {'PLAUSIBLE' if ok else 'REJECTED'} -- the curve "
              f"constant is ~{K_EXPECT:.3e}")
        if not ok:
            print("    a ratio match whose product is orders out is a "
                  "misaligned coincidence, not a field")
    print(f"\n{'=' * 96}")
    if not hits:
        print("  NO byte offset holds a value in that ratio. The token")
        print("  reserve is not in this account, so these two accounts")
        print("  cannot both be honest curves read with one layout.")
    else:
        print("  See above: a candidate counts only if its product is the")
        print("  curve constant. Otherwise the layout is not the problem and")
        print("  the account being followed is.")
    print("\n  Nothing was changed. This only reads.")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
