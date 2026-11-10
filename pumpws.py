"""Does the pumpportal websocket give us launch-time pump.fun data?

WHY THIS MATTERS MORE THAN IT LOOKS. pump.fun's own HTTP API answers 403
with a Cloudflare challenge from a datacenter, so the launchpad cannot be
read directly. DexScreener can be read, but it only lists tokens that
already have a DEX pool -- which is to say, after the part that matters.
Every design decision in a scanner follows from which of those two feeds
exists.

A websocket is worth trying because it is a different path and frequently
not behind the same challenge.

WHAT IS CHECKED, in order, because a failure at any step ends it:
  1. does the socket open at all;
  2. is a subscription accepted;
  3. do messages arrive, and how fast;
  4. what are the KEYS of a message, verbatim.

Step 4 is the point. The last two days produced three separate bugs from
guessing field names, so nothing gets built on this feed until it has said
what it carries. Nothing here is interpreted -- a sample of raw records is
printed and that is all.

LISTENS FOR A FIXED WINDOW AND EXITS. No order, no key, no wallet, no
persistent process.
"""
import asyncio
import json
import os
import sys
import time
from collections import Counter

URL = os.environ.get("PUMPWS_URL") or "wss://pumpportal.fun/api/data"
SECONDS = int(os.environ.get("PUMPWS_SECONDS") or 60)
SAMPLES = int(os.environ.get("PUMPWS_SAMPLES") or 3)
# The documented subscriptions worth having. Each is tried; any that is
# refused is reported rather than retried differently.
SUBS = [s.strip() for s in
        (os.environ.get("PUMPWS_SUBS")
         or "subscribeNewToken,subscribeMigration").split(",") if s.strip()]


async def run():
    try:
        import websockets
    except ImportError:
        print("  the 'websockets' package is not installed")
        return 1

    print(f"  connecting to {URL}")
    t0 = time.time()
    try:
        async with websockets.connect(URL, open_timeout=20,
                                      max_size=4_000_000) as ws:
            print(f"  CONNECTED in {time.time()-t0:.2f}s")
            for m in SUBS:
                await ws.send(json.dumps({"method": m}))
                print(f"  sent: {{'method': '{m}'}}")

            kinds = Counter()
            shown = Counter()
            n = 0
            deadline = time.time() + SECONDS
            print(f"\n  listening {SECONDS}s ...\n")
            while time.time() < deadline:
                try:
                    raw = await asyncio.wait_for(
                        ws.recv(), timeout=max(1.0, deadline - time.time()))
                except asyncio.TimeoutError:
                    break
                except Exception as e:
                    print(f"  recv failed: {str(e)[:140]}")
                    break
                n += 1
                try:
                    d = json.loads(raw)
                except Exception:
                    print(f"  non-JSON frame: {str(raw)[:160]}")
                    continue
                if not isinstance(d, dict):
                    print(f"  non-object frame: {str(d)[:160]}")
                    continue

                # Classify without assuming a schema: use whichever field
                # looks like a discriminator, else the key signature.
                kind = (d.get("txType") or d.get("method")
                        or d.get("type") or d.get("message")
                        or "|".join(sorted(d)[:6]))
                kinds[kind] += 1
                if shown[kind] < SAMPLES:
                    shown[kind] += 1
                    print(f"  --- message #{n}  kind={kind!r}  "
                          f"({len(d)} keys)")
                    for k in sorted(d):
                        v = json.dumps(d[k], default=str)
                        print(f"      {k:<26} {v[:78]}")
                    print()

            dur = SECONDS if time.time() >= deadline else time.time() - t0
            print(f"  {n} message(s) in {dur:.0f}s "
                  f"({n/max(dur,1):.2f}/s)")
            if kinds:
                print("\n  message kinds seen:")
                for k, c in kinds.most_common(12):
                    print(f"    {c:>6}  {k}")
            return 0 if n else 2
    except Exception as e:
        print(f"  CONNECT FAILED: {type(e).__name__}: {str(e)[:200]}")
        return 1


def main():
    print("=" * 100)
    print("PUMPPORTAL WEBSOCKET PROBE -- is launch-time pump.fun data "
          "reachable?")
    print("  pump.fun's HTTP API answers 403 behind Cloudflare from a "
          "datacenter. This tries the other path.")
    print("  No credentials, no wallet, no order. Listens briefly and exits.")
    print("=" * 100 + "\n")
    rc = asyncio.run(run())
    print("\n" + "=" * 100)
    if rc == 0:
        print("  THE FEED WORKS. The scanner can be built on launch-time data")
        print("  rather than on DexScreener's post-pool view. The keys printed")
        print("  above are what it will be written against -- not guessed.")
        print()
        print("  STILL UNANSWERED, and it decides whether any of this is")
        print("  tradeable: a GitHub runner is thousands of miles and several")
        print("  hundred milliseconds from the chain, and the bots that profit")
        print("  from launches are co-located. Receiving the event early is")
        print("  not the same as being able to act on it.")
    elif rc == 2:
        print("  CONNECTED BUT SILENT. The socket opened and the subscription")
        print("  was sent, and nothing arrived in the window. Either the")
        print("  method name is wrong or the feed is idle; both are worth")
        print("  distinguishing before building, so try a longer window.")
    else:
        print("  NOT REACHABLE. Launch-time data is out, and the scanner gets")
        print("  built on DexScreener -- which only sees tokens that already")
        print("  have a pool, i.e. after the launch.")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
