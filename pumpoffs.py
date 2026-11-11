"""Are the bonding-curve byte offsets right? Read the raw account and check.

WHY THIS EXISTS. 812 reserve readings sit above anything a bonding curve can
hold -- median 929 SOL, up to 33,219 -- against a curve that starts at 30
and graduates near 85. The audit eliminated the alternatives one at a time:

  the mints are real pump.fun launches      (516 of 516 checkable started
                                             at ~30 SOL / ~1.07bn tokens)
  no account is shared between mints        (26 accounts, one mint each)
  the arithmetic is self-consistent         (every derived figure recomputes)

Which leaves the decode itself. decode_curve reads five u64s from offset 8
and claims "layout verified by pricesrc against the websocket -- 0.000%
apart". That verification was real, but it was done on ONE live token, so it
proves the layout for the account it checked and nothing more.

THE TEST, and it is a comparison rather than an assumption. Two sets of
curve accounts are fetched raw:

    GOOD  accounts whose recorded vSol was 29-32, so the current decode
          produced something a curve can actually hold
    BAD   accounts whose recorded vSol was above 115

If both sets have the SAME account length and the same field pattern, the
offsets are right and the high values are real state. If the BAD set has a
different length, pump.fun has more than one account layout and reading all
of them with one struct is the bug -- and the create feed's `is_mayhem_mode`
flag says there IS more than one kind of launch.

NOTHING IS ASSUMED ABOUT THE LAYOUT. Every u64 at every 8-byte offset is
printed for each account, so the real field positions can be read off rather
than guessed, and each is shown both raw and divided by LAMPORTS so a
plausible reserve is visible at a glance.

THE INDEPENDENT CHECK is the curve invariant. An AMM bonding curve holds
virtual_sol * virtual_tokens roughly constant as people trade, and pump.fun
starts every curve at 30 SOL against ~1.073bn tokens, so that product is
about 3.2e10 in whole units for every honest account. A pair of offsets that
yields that product is the real pair; one that does not is reading
something else. This is checkable per account, without trusting any figure
already on the sheet.

Read-only. No wallet, no order, no row written. Reads a public RPC and
nothing else.
"""
import base64
import collections
import json
import os
import struct

LAMPORTS = 1_000_000_000
TOKEN_DEC = 10 ** 6
CURVE_OWNER = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
RPCS = [u.strip() for u in (os.environ.get("SOLANA_RPCS") or
        "https://api.mainnet-beta.solana.com").split(",") if u.strip()]
# The product every honest pump.fun curve starts at: 30 SOL * 1.073bn.
K_EXPECT = 30.0 * 1.073e9
HOW_MANY = int(os.environ.get("PUMPOFFS_N") or 6)


def client():
    import gspread
    from google.oauth2.service_account import Credentials
    raw = os.environ.get("GOOGLE_CREDENTIALS")
    if not raw:
        raise SystemExit("  no GOOGLE_CREDENTIALS")
    creds = Credentials.from_service_account_info(
        json.loads(raw),
        scopes=["https://www.googleapis.com/auth/spreadsheets"])
    sid = os.environ.get("MEME_SHEET_ID")
    if not sid:
        import predictor as P
        sid = P.SPREADSHEET_ID
    return gspread.authorize(creds).open_by_key(sid)


def f(v):
    try:
        x = float(str(v).strip())
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def fetch(keys):
    """Raw base64 account data for up to 100 keys. One call."""
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
            last = f"{type(e).__name__}: {str(e)[:80]}"
            continue
        if not r.ok:
            last = f"HTTP {r.status_code} {r.text[:120]}"
            continue
        j = r.json() or {}
        if "error" in j:
            last = f"rpc error {str(j['error'])[:120]}"
            continue
        vals = ((j.get("result") or {}).get("value")) or []
        # A SHORT LIST WOULD MIS-PAIR EVERY KEY. getMultipleAccounts returns
        # one slot per key, nulls included, so a different length means the
        # response cannot be zipped and must not be.
        if len(vals) != len(keys):
            last = f"length {len(vals)} != {len(keys)}"
            continue
        return dict(zip(keys, vals)), ""
    return {}, last


def dump(label, key, acc):
    print(f"\n  {label}  {key}")
    if acc is None:
        print("    account does not exist (closed, or never did)")
        return None
    data = acc.get("data")
    blob = base64.b64decode(data[0]) if isinstance(data, list) else b""
    owner = acc.get("owner")
    print(f"    owner  {owner}"
          + ("" if owner == CURVE_OWNER else "   <- NOT the pump.fun program"))
    print(f"    length {len(blob)} byte(s)   lamports {acc.get('lamports')}")
    if len(blob) < 16:
        print("    too short to hold any reserve field")
        return len(blob)
    print(f"    {'offset':>7}{'raw u64':>24}{'/1e9 (SOL?)':>16}"
          f"{'/1e6 (tokens?)':>18}")
    n = (len(blob) - 8) // 8
    for i in range(min(n, 8)):
        off = 8 + i * 8
        (val,) = struct.unpack_from("<Q", blob, off)
        print(f"    {off:>7}{val:>24,}{val / LAMPORTS:>16,.4f}"
              f"{val / TOKEN_DEC:>18,.2f}")
    # The current decoder's own reading, for comparison.
    if len(blob) >= 49:
        vt, vs, rt, rs, sup = struct.unpack_from("<QQQQQ", blob, 8)
        print(f"    decode_curve now reads  vSol {vs / LAMPORTS:,.4f}   "
              f"vTokens {vt / TOKEN_DEC:,.2f}")
        print(f"                            supply {sup / TOKEN_DEC:,.2f}   "
              f"complete {bool(blob[48])}")
        # THE INVARIANT, computed rather than asserted.
        k = (vs / LAMPORTS) * (vt / TOKEN_DEC)
        print(f"    vSol * vTokens = {k:,.3e}   "
              f"(an honest curve is ~{K_EXPECT:,.3e})")
        if k and 0.2 <= k / K_EXPECT <= 5.0:
            print("    -> the invariant HOLDS, so this pair of offsets is "
                  "reading the real reserves")
        else:
            print("    -> the invariant FAILS, so these two offsets are NOT "
                  "the reserve pair")
            # Search every pair of offsets for one that does satisfy it.
            best = None
            for a in range(8, min(len(blob) - 7, 80), 8):
                for b in range(8, min(len(blob) - 7, 80), 8):
                    if a == b:
                        continue
                    (sv,) = struct.unpack_from("<Q", blob, a)
                    (tv,) = struct.unpack_from("<Q", blob, b)
                    kk = (sv / LAMPORTS) * (tv / TOKEN_DEC)
                    if not kk:
                        continue
                    score = abs(kk / K_EXPECT - 1.0)
                    if best is None or score < best[0]:
                        best = (score, a, b, sv / LAMPORTS, tv / TOKEN_DEC)
            if best and best[0] < 4.0:
                # THE INVARIANT IS A PRODUCT, SO IT IS SYMMETRIC and cannot
                # say which offset is SOL and which is tokens -- the search
                # found the pair in whatever order it happened to try them,
                # and the synthetic test proved it will label them backwards.
                # Magnitude settles it: 30 SOL is 3e10 raw lamports while
                # 1.073bn tokens is ~1.07e15 raw, four orders apart.
                (_, oa, ob) = best[:3]
                (ra,) = struct.unpack_from("<Q", blob, oa)
                (rb,) = struct.unpack_from("<Q", blob, ob)
                sol_off, tok_off = (oa, ob) if ra < rb else (ob, oa)
                (rs_,) = struct.unpack_from("<Q", blob, sol_off)
                (rt_,) = struct.unpack_from("<Q", blob, tok_off)
                print(f"    -> offset {sol_off} holds SOL "
                      f"({rs_ / LAMPORTS:,.4f}) and offset {tok_off} holds "
                      f"tokens ({rt_ / TOKEN_DEC:,.2f});")
                print(f"       that pair satisfies the invariant. Assigned "
                      f"by magnitude, not by search order.")
            else:
                print("    -> NO pair of offsets in this account satisfies "
                      "the invariant, so it is not a bonding curve at all")
    return len(blob)


def main():
    print("=" * 100)
    print("BONDING-CURVE BYTE OFFSETS -- read the raw account and compare")
    print("  Accounts that decode plausibly, against accounts that do not.")
    print("=" * 100)

    sh = client()
    rows = sh.worksheet("Pump Prices").get_all_values()
    if len(rows) < 2:
        raise SystemExit("  no price rows")
    h = {k: i for i, k in enumerate(rows[0])}
    if "Curve" not in h:
        raise SystemExit("  the 'Curve' column does not exist on this tab; "
                         "no reading can be traced to an account")

    def g(r, k):
        i = h.get(k)
        return r[i] if i is not None and i < len(r) else ""

    good, bad = {}, {}
    for r in rows[1:]:
        cv, vs = g(r, "Curve"), f(g(r, "vSol"))
        if not cv or vs is None:
            continue
        if 29.0 <= vs <= 32.0 and cv not in good:
            good[cv] = vs
        elif vs > 115.0 and cv not in bad:
            bad[cv] = vs

    print(f"\n  {len(good)} account(s) that decoded plausibly, "
          f"{len(bad)} that did not")
    if not bad:
        print("  nothing decoded impossibly on rows that carry an account "
              "address, so there is nothing to compare.")
        return 0

    gk = list(good)[:HOW_MANY]
    bk = list(bad)[:HOW_MANY]
    accs, err = fetch(gk + bk)
    if not accs:
        print(f"\n  the RPC could not be read: {err}")
        return 1

    lens = collections.defaultdict(list)
    print(f"\n{'-' * 100}")
    print("ACCOUNTS WHOSE CURRENT READING IS PLAUSIBLE (vSol 29-32)")
    print("-" * 100)
    for k in gk:
        n = dump(f"GOOD (recorded {good[k]:,.3f} SOL)", k, accs.get(k))
        if n is not None:
            lens["good"].append(n)

    print(f"\n{'-' * 100}")
    print("ACCOUNTS WHOSE CURRENT READING IS IMPOSSIBLE (vSol > 115)")
    print("-" * 100)
    for k in bk:
        n = dump(f"BAD  (recorded {bad[k]:,.3f} SOL)", k, accs.get(k))
        if n is not None:
            lens["bad"].append(n)

    print(f"\n{'=' * 100}")
    print("VERDICT")
    print("=" * 100)
    sg, sb = set(lens["good"]), set(lens["bad"])
    print(f"  plausible accounts are {sorted(sg) or '-'} byte(s) long")
    print(f"  impossible accounts are {sorted(sb) or '-'} byte(s) long")
    if sg and sb and sg != sb:
        print("\n  DIFFERENT LENGTHS. pump.fun has more than one curve")
        print("  account layout and decode_curve reads all of them with one")
        print("  struct. That is the defect, and it is fixable: branch on")
        print("  the account length rather than assuming a single shape.")
    elif sg and sb:
        print("\n  SAME LENGTH, so it is not two layouts. Read the per-offset")
        print("  tables above: if a different pair of offsets satisfies the")
        print("  curve invariant, the field positions are simply wrong; if")
        print("  no pair does, these accounts are not bonding curves and the")
        print("  bug is upstream in which address gets followed.")
    else:
        print("\n  NOT ENOUGH ACCOUNTS CAME BACK to compare. This says")
        print("  nothing about the layout; it says the RPC returned too few.")
    print("\n  Nothing was changed. This only reads.")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
