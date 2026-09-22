"""Repair kalshi-crypto-bot's market reads against the current Kalshi API.

WHAT THE OVERNIGHT RUN ACTUALLY SHOWED. 1978 of 1978 ticks carried
mid=None, spread=None and ob_imbalance=None while tickers, close_times and
Coinbase spot arrived perfectly. The natural reading was "no credentials",
and it was wrong. probe_auth.py asked the endpoints directly, signed and
unsigned, and the two answers were byte-for-byte identical:

  /markets?series_ticker=...   yes_bid=None yes_ask=None   both ways
  /markets/{ticker}/orderbook  full depth returned          both ways

So the book is PUBLIC and complete, and no API key is needed to read it.
Two upstream changes broke the bot instead:

  THE BOOK MOVED. Kalshi now returns it under "orderbook_fp", with prices
  and sizes as STRINGS IN DOLLARS. The bot reads data["orderbook"]["yes"],
  which is absent, so every imbalance came back None. Our own path.py hit
  this exact thing and its docstring records the same fix -- the guess of
  "orderbook" with integer cents "wrote nothing but blanks for a full day".

  yes_bid / yes_ask ARE GONE from the markets list response. The bot builds
  mid and spread from those two fields, so both stayed None even though the
  prices were sitting in the book it had already fetched.

Neither is a credentials problem and neither needs a signing proxy. The fix
is to read the book that is already public.

WHY THIS PATCHES RATHER THAN FORKS. The clone stays pristine and auditable,
so what upstream actually ships can still be diffed. Every replacement below
is asserted: if upstream edits these lines the patch FAILS THE BUILD rather
than quietly matching nothing, because a no-op patch would restore exactly
the silent blank-data failure it was written to cure.
"""

import io
import sys

TARGET = "bots/kalshi-crypto-bot/paper_trader.py"

OLD_OB = '''            ob = data.get("orderbook", {})
            y_qty = sum(qty for _, qty in ob.get("yes", []))
            n_qty = sum(qty for _, qty in ob.get("no", []))
            total = y_qty + n_qty
            return {"asset": asset,
                    "ob_imbalance": round((y_qty - n_qty) / total, 4) if total > 0 else None}'''

NEW_OB = '''            # PATCHED: Kalshi serves the book as "orderbook_fp" with prices
            # and sizes as strings in DOLLARS. The original read
            # data["orderbook"]["yes"], which no longer exists, so every
            # imbalance was None. Legacy shapes are still accepted below in
            # case the endpoint serves them again; those are integer cents,
            # so the scale is tracked rather than assumed.
            fp = data.get("orderbook_fp")
            legacy = data.get("orderbook") or {}
            if fp:
                ys, ns, scale = fp.get("yes_dollars") or [], fp.get("no_dollars") or [], 1.0
            else:
                ys, ns, scale = legacy.get("yes") or [], legacy.get("no") or [], 0.01

            def _levels(rows):
                out = []
                for row in rows or []:
                    try:
                        out.append((float(row[0]) * scale, float(row[1])))
                    except (TypeError, ValueError, IndexError):
                        continue
                return out

            yl, nl = _levels(ys), _levels(ns)
            y_qty = sum(q for _, q in yl)
            n_qty = sum(q for _, q in nl)
            total = y_qty + n_qty
            # A YES bid at p is the same resting order as a NO ask at 1-p;
            # Kalshi runs ONE book. So the best YES ask is 1 minus the best
            # NO bid, not anything on the yes side.
            mid = spread = None
            if yl and nl:
                yb = max(p for p, _ in yl)
                ya = 1.0 - max(p for p, _ in nl)
                if 0.0 < yb < ya < 1.0:
                    mid = round((yb + ya) / 2.0, 4)
                    spread = round(ya - yb, 4)
            return {"asset": asset, "mid": mid, "spread": spread,
                    "ob_imbalance": round((y_qty - n_qty) / total, 4) if total > 0 else None}'''

OLD_MERGE = '''            markets[a]["ob_imbalance"] = ob.get("ob_imbalance")'''

NEW_MERGE = '''            markets[a]["ob_imbalance"] = ob.get("ob_imbalance")
            # PATCHED: /markets no longer returns yes_bid / yes_ask, so
            # poll_market leaves mid and spread None. The book already
            # fetched above carries both. Only filled in when missing, so
            # if upstream starts serving those fields again they win.
            if markets[a].get("mid") is None and ob.get("mid") is not None:
                markets[a]["mid"] = ob.get("mid")
                markets[a]["spread"] = ob.get("spread")'''


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else TARGET
    with io.open(path, encoding="utf-8") as f:
        src = f.read()

    if "PATCHED:" in src:
        print("already patched; nothing to do")
        return 0

    edits = [("orderbook_fp parsing", OLD_OB, NEW_OB),
             ("mid/spread from the book", OLD_MERGE, NEW_MERGE)]
    for label, old, new in edits:
        n = src.count(old)
        if n != 1:
            # Loud, not quiet. A patch that matches nothing would hand back
            # the exact silent blank-data failure it exists to fix.
            print(f"PATCH FAILED: '{label}' matched {n} times, expected 1.")
            print("Upstream has changed. Re-read paper_trader.py before")
            print("running this bot again -- do NOT proceed with blank data.")
            return 1
        src = src.replace(old, new)
        print(f"  applied: {label}")

    with io.open(path, "w", encoding="utf-8") as f:
        f.write(src)

    compile(src, path, "exec")          # syntax, before the bot is launched
    print(f"patched {path} OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
