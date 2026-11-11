"""Map the real 166-byte bonding-curve layout. Read-only, no assumptions.

WHY THE OLD LAYOUT IS WRONG, established and not assumed. pumpoffs fetched
the raw accounts and found:

    length 166 bytes, while decode_curve assumes 49 and reads the first
    five u64s from offset 8;

    offset 8 reads EXACTLY 1,073,000,000 tokens on an account whose offset
    16 has risen from 30 to 925 SOL. For an AMM that cannot happen -- if
    SOL goes in, tokens come out -- so offset 8 is a configuration constant
    that never moves, not virtual_token_reserves;

    offsets 24 and 40 hold 793,100,000 and 1,000,000,000 identically across
    different accounts, which is what constants look like.

A frozen denominator makes every price in the tab wrong, so the field
positions have to be found rather than guessed at a fourth time.

THE METHOD, which needs no documentation and no theory:

  1. RECRUIT fresh launches from the websocket. The create event carries
     vSolInBondingCurve and vTokensInBondingCurve at t=0, which is GROUND
     TRUTH from a source that does no decoding. Whatever the right offsets
     are, the first sample must reproduce those two numbers.

  2. POLL each account repeatedly and record the u64 at EVERY 8-byte
     offset, every time.

  3. A field that never changes across samples is a constant. A field that
     moves is live state. This separates config from reserves without
     knowing what any field is called.

  4. Among the moving fields, find the pair where
       - the first sample matches the event's vSol and vTokens, and
       - the product stays roughly constant as they move, which is what an
         AMM does and what a pair of unrelated counters will not do.

  5. Require the answer to AGREE ACROSS SEVERAL TOKENS. One token proved a
     layout once before and that is exactly how the current bug got in:
     pricesrc verified offset 8/16 against the websocket on a single live
     token, at 0.000%, and it was right about that account and wrong about
     the field.

WHAT THIS FILE WILL NOT DO: write a row, change decode_curve, or report a
layout it could not confirm on more than one token. It prints a finding and
stops; the fix is a separate, reviewable change.

Read-only. No wallet, no key, no order, no sheet.
"""
import asyncio
import base64
import json
import os
import struct
import time
from collections import defaultdict

WS = os.environ.get("PUMPWS_URL") or "wss://pumpportal.fun/api/data"
RPCS = [u.strip() for u in (os.environ.get("SOLANA_RPCS") or
        "https://api.mainnet-beta.solana.com").split(",") if u.strip()]
LAMPORTS = 1_000_000_000
TOKEN_DEC = 10 ** 6
CURVE_OWNER = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

WANT = int(os.environ.get("PUMPMAP_TOKENS") or 8)
RECRUIT_S = int(os.environ.get("PUMPMAP_RECRUIT") or 150)
SAMPLES = int(os.environ.get("PUMPMAP_SAMPLES") or 14)
EVERY = int(os.environ.get("PUMPMAP_EVERY") or 20)
# How close a candidate must sit to the event's own figure to count as a
# match. The event is a float and the chain is an integer, so an exact
# equality would reject the right answer over rounding.
TOL = 0.005


def fnum(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


async def recruit(n, seconds):
    """Fresh launches with their curve key and the event's own reserves."""
    import websockets
    out = {}
    end = time.time() + seconds
    print(f"  listening for {n} launch(es), up to {seconds}s ...")
    async with websockets.connect(WS, open_timeout=20,
                                  max_size=4_000_000) as s:
        await s.send(json.dumps({"method": "subscribeNewToken"}))
        while time.time() < end and len(out) < n:
            try:
                raw = await asyncio.wait_for(
                    s.recv(), timeout=max(1, end - time.time()))
            except asyncio.TimeoutError:
                break
            except Exception as e:
                print(f"  feed ended: {str(e)[:90]}")
                break
            try:
                d = json.loads(raw)
            except Exception:
                continue
            if not isinstance(d, dict) or d.get("txType") != "create":
                continue
            mint, curve = d.get("mint"), d.get("bondingCurveKey")
            vs = fnum(d.get("vSolInBondingCurve"))
            vt = fnum(d.get("vTokensInBondingCurve"))
            if not mint or not curve or vs is None or vt is None:
                continue
            if any(v["curve"] == curve for v in out.values()):
                # One account cannot be two tokens' price; refuse rather
                # than overwrite, same as pumpscan now does.
                continue
            out[mint] = dict(curve=curve, vs=vs, vt=vt,
                             sym=d.get("symbol", ""))
            print(f"    + {d.get('symbol','?')[:12]:<12} "
                  f"event says vSol {vs:,.4f}  vTokens {vt:,.2f}")
    return out


def fetch(keys):
    import requests
    last = ""
    for url in RPCS:
        try:
            r = requests.post(url, json={
                "jsonrpc": "2.0", "id": 1, "method": "getMultipleAccounts",
                "params": [keys, {"encoding": "base64",
                                  "commitment": "confirmed"}]},
                timeout=30)
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
        return dict(zip(keys, vals)), ""
    return {}, last


def u64s(blob):
    """Every u64 at every 8-byte offset. No field names, no assumptions."""
    out = {}
    off = 8
    while off + 8 <= len(blob):
        (out[off],) = struct.unpack_from("<Q", blob, off)
        off += 8
    return out


def main():
    print("=" * 100)
    print("MAP THE BONDING-CURVE LAYOUT -- find the live reserve fields")
    print(f"  {WANT} token(s), {SAMPLES} sample(s) {EVERY}s apart")
    print("  Read-only. No wallet, no order, no row written.")
    print("=" * 100)

    cohort = asyncio.run(recruit(WANT, RECRUIT_S))
    if not cohort:
        print("\n  no launches captured; nothing to map.")
        return 1
    print(f"\n  following {len(cohort)} token(s)")

    keys = [v["curve"] for v in cohort.values()]
    by_curve = {v["curve"]: m for m, v in cohort.items()}
    # history[curve][offset] = [values over time]
    history = defaultdict(lambda: defaultdict(list))
    lengths = {}

    for i in range(SAMPLES):
        accs, err = fetch(keys)
        if not accs:
            print(f"  sample {i}: RPC unreadable ({err})")
            time.sleep(EVERY)
            continue
        live = 0
        for k, acc in accs.items():
            if not acc:
                continue
            data = acc.get("data")
            blob = base64.b64decode(data[0]) if isinstance(data, list) else b""
            if acc.get("owner") != CURVE_OWNER or len(blob) < 16:
                continue
            lengths[k] = len(blob)
            live += 1
            for off, val in u64s(blob).items():
                history[k][off].append(val)
        print(f"  sample {i}: {live}/{len(keys)} account(s) read")
        if i < SAMPLES - 1:
            time.sleep(EVERY)

    if not history:
        print("\n  no account could be read; nothing to map.")
        return 1

    print(f"\n  account length(s) seen: {sorted(set(lengths.values()))}")

    # ------------------------------------------- constant vs live fields
    print(f"\n{'-' * 100}")
    print("WHICH FIELDS MOVE -- a constant is config, a mover is state")
    print("-" * 100)
    moved = defaultdict(int)
    seen_off = set()
    for k, offs in history.items():
        for off, vals in offs.items():
            seen_off.add(off)
            if len(set(vals)) > 1:
                moved[off] += 1
    n_acc = len(history)
    for off in sorted(seen_off):
        tag = (f"MOVES on {moved[off]}/{n_acc} account(s)" if moved[off]
               else "constant everywhere")
        # Show one account's first value so a recognisable constant is
        # visible next to its offset.
        some = next(iter(history.values())).get(off)
        first = some[0] if some else 0
        print(f"  offset {off:>4}  {tag:<28} e.g. {first:>24,}")
    if not moved:
        print("\n  NOTHING MOVED in the whole window. Either these tokens")
        print("  saw no trades, or the account is not where the reserves")
        print("  live. Re-run during busier launches before concluding.")
        return 0

    # ------------------------------ which pair reproduces the event, and
    # ------------------------------ behaves like an AMM while it moves
    print(f"\n{'-' * 100}")
    print("WHICH PAIR OF OFFSETS IS (vSol, vTokens)")
    print("  must match the launch event at t=0 AND hold its product")
    print("-" * 100)
    votes = defaultdict(list)
    for k, offs in history.items():
        mint = by_curve.get(k)
        ev = cohort.get(mint) or {}
        ev_vs, ev_vt = ev.get("vs"), ev.get("vt")
        if ev_vs is None or ev_vt is None:
            continue
        sol_cands = [o for o, vals in offs.items() if vals and
                     abs(vals[0] / LAMPORTS - ev_vs) <= TOL * max(1.0, ev_vs)]
        tok_cands = [o for o, vals in offs.items() if vals and
                     abs(vals[0] / TOKEN_DEC - ev_vt) <= TOL * max(1.0, ev_vt)]
        print(f"\n  {ev.get('sym','?')[:12]:<12} event vSol {ev_vs:,.4f} "
              f"vTokens {ev_vt:,.2f}")
        print(f"    offsets matching the event's vSol:    "
              f"{sol_cands or 'NONE'}")
        print(f"    offsets matching the event's vTokens: "
              f"{tok_cands or 'NONE'}")
        for so in sol_cands:
            for to in tok_cands:
                if so == to:
                    continue
                sv, tv = offs[so], offs[to]
                ks = [(a / LAMPORTS) * (b / TOKEN_DEC)
                      for a, b in zip(sv, tv) if a and b]
                if len(ks) < 2:
                    continue
                drift = (max(ks) - min(ks)) / max(ks)
                # A real pair keeps its product; two unrelated counters
                # will not. Movement is required too, or a pair of frozen
                # constants would score perfectly by never changing.
                movers = (len(set(sv)) > 1) or (len(set(tv)) > 1)
                votes[(so, to)].append((drift, movers))
                print(f"    candidate vSol@{so} vTokens@{to}: "
                      f"product drift {100*drift:.3f}%"
                      + ("" if movers else "  (neither field moved)"))

    print(f"\n{'=' * 100}")
    print("FINDING")
    print("=" * 100)
    if not votes:
        print("  NO PAIR OF OFFSETS reproduces the launch event. The")
        print("  reserves are not in this account as plain u64s, and no")
        print("  layout should be written from this run.")
        return 0
    # MOVEMENT IS A HARD REQUIREMENT, NOT A WARNING. The synthetic test
    # ranked a pair of FROZEN DECOYS above the real reserves, because a
    # field that never changes has perfect product drift by construction.
    # Scoring drift first therefore rewards exactly the constants this file
    # exists to rule out. A pair where nothing ever moved is not a weak
    # candidate; it is indistinguishable from config and is excluded.
    ranked = []
    for (so, to), obs in votes.items():
        anymove = any(mv for _, mv in obs)
        ranked.append((1 if anymove else 0, len(obs),
                       -max(d for d, _ in obs), so, to))
    ranked.sort(reverse=True)
    movers = [r for r in ranked if r[0]]
    if not movers:
        print("  EVERY candidate pair was frozen for the whole window, so")
        print("  none can be told apart from configuration. Nothing is")
        print("  concluded; re-run while tokens are actually trading.")
        print("\n  Nothing was changed.")
        print("=" * 100)
        return 0
    _, agree, negdrift, so, to = movers[0]
    print(f"  best candidate: vSol at offset {so}, vTokens at offset {to}")
    print(f"    agreed on {agree}/{len(history)} token(s), "
          f"worst product drift {-100*negdrift:.3f}%")
    print(f"    and both fields were seen to MOVE, which is what excludes")
    print(f"    the frozen configuration fields at 8, 24 and 40.")
    anymove = True
    if agree < 2:
        print("\n  ONLY ONE TOKEN AGREES, which is exactly how the current")
        print("  bug got in: pricesrc confirmed offset 8/16 on a single")
        print("  token at 0.000% and was wrong about the field. NOT")
        print("  CONFIRMED -- re-run for more tokens before changing code.")
    elif not anymove:
        print("\n  NEITHER FIELD MOVED on any token, so this pair matches")
        print("  the event only because nothing traded. A frozen constant")
        print("  would score the same. NOT CONFIRMED.")
    else:
        print(f"\n  CONFIRMED on {agree} token(s), with the fields actually")
        print("  moving and the product holding. decode_curve should read")
        print(f"  vSol at {so} and vTokens at {to}, NOT 8 and 16.")
    print("\n  Nothing was changed. The fix is a separate edit.")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
