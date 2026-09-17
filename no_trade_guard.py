"""Fail the build if anything in this repo could place a Kalshi order.

"It doesn't spend money" is only true until someone changes a file. This makes
it a checked property instead of a promise: the predictor workflow runs this
before collection, so if an order path ever appears -- added by me, by a future
session, by a copy-paste from Kalshi's docs -- the run stops and says so rather
than quietly trading.

TO GO LIVE ON PURPOSE, a human sets ALLOW_LIVE_TRADING=1 in the workflow. That
is the thumbs-up: one deliberate edit, in a file that needs a commit, rather
than an accident. Nothing in the code can set it for you.

What counts as an order path:
  - any HTTP write verb (post/put/patch/delete) aimed at the Kalshi host
  - any reference to Kalshi's order endpoints or order-placing helpers

Deliberately NOT flagged:
  - GET /portfolio/orders, which READS the order list (kalshi_probe uses it to
    tell a trade-scoped key from a read-only one)
  - requests.post to Pushover, which sends a phone notification
"""

import os
import pathlib
import re
import sys

# Files to read. Anything the predictor workflow could execute.
SCAN = ["predictor.py", "dryrun.py", "control.py", "live.py", "manual_bet.py",
        "local_check.py",
        "decay.py", "kalshi_probe.py", "dashboard_data.py"]

WRITE_VERB = re.compile(r"requests\.(post|put|patch|delete)\s*\(", re.I)
ORDER_WORD = re.compile(
    r"create_order|place_order|batch_orders|/portfolio/orders/|"
    r"\"POST\"\s*,\s*[\"']/trade-api/v2/portfolio",
    re.I,
)
KALSHI_HINT = re.compile(r"KALSHI_BASE|kalshi\.com", re.I)


def scan(path):
    """Return a list of (lineno, line, why) for anything that could trade."""
    hits = []
    try:
        lines = pathlib.Path(path).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return hits

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue                      # a comment cannot place an order

        if WRITE_VERB.search(line):
            # A write verb is only a problem if it is pointed at Kalshi. The
            # call spans lines, so look at the few that follow for the host.
            window = "\n".join(lines[i - 1:i + 4])
            if KALSHI_HINT.search(window):
                hits.append((i, stripped, "HTTP write aimed at Kalshi"))

        if ORDER_WORD.search(line):
            hits.append((i, stripped, "order-placement endpoint or helper"))

    return hits


def main():
    if os.environ.get("ALLOW_LIVE_TRADING", "").strip() == "1":
        print("no_trade_guard: ALLOW_LIVE_TRADING=1 -- live trading permitted "
              "by explicit configuration. Guard not enforced.")
        return 0

    found = {}
    for path in SCAN:
        hits = scan(path)
        if hits:
            found[path] = hits

    if not found:
        print(f"no_trade_guard: OK -- no order path in {len(SCAN)} scanned "
              f"files. Nothing here can spend money.")
        return 0

    print("=" * 62)
    print("no_trade_guard: BLOCKED -- something here can place an order.")
    print("=" * 62)
    for path, hits in found.items():
        for lineno, line, why in hits:
            print(f"  {path}:{lineno}  {why}")
            print(f"      {line[:100]}")
    print()
    print("Collection has been stopped on purpose. If this is intended, set")
    print("ALLOW_LIVE_TRADING=1 in the workflow. If it is not, revert it.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
