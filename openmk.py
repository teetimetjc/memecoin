"""Every open market in the given series. One list call per series.

WHY THIS IS ITS OWN MODULE. Three files -- latecollect, depthchk and
t3shadow -- each called the markets endpoint like this:

    get("/trade-api/v2/markets", status="open", limit=200)

and every one of them silently collected NOTHING. Kalshi has thousands of
open markets at any moment. An unfiltered list call returns one arbitrary
page of 200 of them, and the fifteen-minute crypto markets were simply not
in that page, so the filters downstream were applied to a set that never
contained a single candidate. The jobs ran for 55 minutes, exited 0, and
printed "wrote 0 sample(s)" -- which reads exactly like a quiet market.

latecollect ran four times across two days that way. Every run reported
success. No data was collected at all, and the failure was reported
upstream as "accumulating hourly", which it never was.

favlive has always done it correctly -- one call per series_ticker -- and
the three collectors were written by copying the shape of the call without
the filter that makes it work.

THIS IS THE SAME BUG CLASS AS ALL THE OTHERS HERE: a read that returns
something plausible instead of failing. volume_fp, orderbook_fp, one page
of fills read as a whole history, one page of 200 settlements read as "all
time". Nothing errors; the number is just quietly about a different
question. So the fix lives in one place, and it SAYS when a series comes
back empty rather than folding that into a zero.
"""
import time

import collect15 as C

MAJORS = ("KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M")


def open_markets(series, verbose=False):
    """All open markets across `series`. One call each, filtered server-side.

    Returns a flat list. A series that errors or comes back empty is
    reported rather than silently contributing nothing, because "no markets
    in this series" and "the call failed" are different facts and only one
    of them is about the exchange.
    """
    out = []
    for s in series:
        d, err = C.get("/trade-api/v2/markets", series_ticker=s,
                       status="open", limit=200)
        if not d:
            print(f"  [openmk] {s}: {err}")
            continue
        mk = d.get("markets") or []
        if verbose and not mk:
            print(f"  [openmk] {s}: no open markets")
        out.extend(mk)
        time.sleep(0.05)
    return out
