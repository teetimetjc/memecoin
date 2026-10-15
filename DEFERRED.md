# Deferred, by the operator's decision

## Audit items 4 and 7 — NOT DONE, deliberately (2026-10-15)

Both change live behaviour, so they were held back when the instrumentation
(items 1,2,3,5,6) went in. The operator's words: "Remind me of 4 and 7 some
other time and we may do it. Just not yet."

### 4. The `last_price` fallback in `current_entry()`  — `live.py:~249`

```python
px = cents("yes_ask_dollars") or cents("last_price_dollars")
```

A last-traded price is not an executable buy price. It is whatever someone
paid at some earlier moment, which in a thin 15-minute book can be far from
what you can actually buy at now. This feeds the NOTYET retry path, so a
market that was not listed on the first attempt can have its limit derived
from a stale trade print rather than a live ask.

Why it is not urgent: it only fires when `yes_ask_dollars` is missing, and
the main decision path uses `fresh_side_cents`, which has no such fallback.

Live impact if changed: a few retries would skip instead of betting.

### 7. Fees excluded from the funding reservation — `control.check_window`

`cost = contracts * limit_c / 100` omits the entry fee, so the window check
reserves slightly less than the orders will actually cost. At a $4 stake the
shortfall is about 2-3c per bet; it only matters near a balance floor.

Live impact if changed: reserves marginally more, so a window could be
declined for funding slightly earlier than it is today.

### What to look at before deciding

The new Live Bets columns (`Presubmit ¢`, `Quote age s`) will show how often
the retry path actually runs and whether those bets behave differently. That
is the evidence that should settle item 4, rather than the argument above.
