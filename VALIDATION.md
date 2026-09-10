# Validation

How to tell whether a change to the predictor actually worked.

Every logged row carries a **Model Version**. Accuracy figures are never pooled
across versions — a v1 number and a v2 number describe different programs.

Run the report:

```
python predictor.py --report              # current version
python predictor.py --report --version v1 # an older one
```

## Why the version stamp exists

The v1 baseline was measured at 48.8% over 2,521 predictions (Sep 1–6 2026).
That number described a model running on six working indicators, not eight:

- `calc_stoch_rsi` returned `None` on every call. Its window was one element
  shorter than `calc_rsi` needs, so the function fell through its own guard.
  The Stoch RSI column is populated **0 times in 2,540 rows**.
- `calc_vol_spike` compared the in-progress candle against completed ones, so
  it measured how far into the current minute the script happened to run.
  Median reading 0.10; 87.4% of values below 1.0. A synthetic 2.6× volume
  spike registered as 0.074.

Those legs carried 0.20 of the 1.00 weight vector — one silent, one inverted.
Fixing them changes composite output, so v1 and v2 accuracy are not comparable
and must not be averaged together.

## The thresholds, fixed in advance

Set before any v2 data existed, so a verdict cannot be talked into existence
afterward. They live in `predictor.py` as `MIN_SAMPLE` and `BREAKEVEN_MARGIN`.

| Gate | Value | Why |
| --- | --- | --- |
| Minimum priced sample | 300 rows | At n=55 the 95% CI on a 60% reading spans 46.8%–73.2% — wide enough to contain breakeven. 300 narrows it to roughly ±5.7pp. |
| Bar to clear | entry-implied breakeven + 1.0pp | A binary at *p* cents needs *p*% accuracy to break even. The margin covers fees and slippage. |
| P&L | must be positive | Accuracy above the bar with negative P&L means the wins came at worse prices than the losses. |

**Accuracy alone cannot pass anything.** A 60% strategy paying 65¢ loses money;
a 48% strategy paying 40¢ makes it. This is why `EMA-Only Entry ¢` is logged
next to `EMA-Only Correct?` — the pair is the unit of evidence, not either
column alone.

## What a v2 run should show

Checked automatically by `--report`:

1. **Stoch RSI populated** on >90% of rows. If still 0, the fix didn't take.
2. **Vol Spike median near 1.0** (was 0.10). Outside 0.5–2.0 means it's still
   sampling a partial candle.
3. **Kalshi priced on >90% of rows.** At v1's 2.2% coverage no profitability
   question can be answered at all — this is the gate that matters most.

Then, for the composite and the EMA-only rule independently: accuracy, average
entry price, realised P&L net of Kalshi's `0.07 × C × P × (1−P)` fee, and a
PASS / FAIL / TOO EARLY verdict.

## v3 / v4: what is actually being tested

v2 settled the question v1 could not. Over 309 priced rows, a logistic fit of

    actual_up ~ kalshi_price + composite

put the Kalshi price at z=+3.52 and the composite at z=+0.53, with a
likelihood-ratio p of 0.594. The composite adds nothing once the price is
known, and Kalshi's prices are well calibrated across the range (41c -> 38.5%
actual, 59c -> 56.2%, 70c -> 75.9%). Accuracy tracked the entry price in every
bucket and in all five coins.

That closes off indicator work. Any model built from public price and volume
history is reproducing information the market already holds, so reweighting or
adding indicators of the same kind cannot produce an edge.

v3 therefore stops trying to out-predict the price and asks whether the price
is briefly wrong instead. Kalshi is sampled twice per window: once immediately
at the boundary, while the new market is thin, and again at
`KALSHI_LATE_DELAY` seconds once liquidity has arrived. v1 and v2 slept 30s
before their only sample, so neither ever observed the opening window.

`--report` compares the two by Brier score and, on rows where they disagree by
3c or more, by which side settlement actually favoured:

- **Early worse than late** -> the opening quote is stale and the drift between
  them is tradeable.
- **Equal** -> there is no opening inefficiency, and this line of attack is
  finished. That is a real result and worth having.

v3 was superseded after 10 rows. Its early sweep could fall back to the
previous window's market, which quotes near 0 or 100 as it settles: one row
logged an early quote of 97c against a late quote of 46c for what should have
been one contract. Every such row would have entered the comparison as a large
fake early error and produced a confident "stale" verdict built on mismatched
contracts. v4 collects the same hypothesis correctly -- the early sweep refuses
closed markets, the late sweep pins to the contract the early sweep saw, and
rows whose tickers disagree are excluded and counted rather than averaged in.
The v3 rows are not usable and are kept only as a record.

**Known limit.** The early sweep lands 17-22 seconds into the window, because
that is how long a GitHub Actions runner takes to boot, install dependencies
and start. v4 therefore tests whether the ~20s quote is stale relative to the
~70s one -- not whether the opening tick is. A "no gap" result rules out
staleness across that span, not staleness at the open, which a cron-triggered
runner cannot observe at all. Acting on any gap that is found would likewise
need a persistent process holding a websocket.

v3 also aligns the prediction window to the Kalshi 15-min boundary. v2
timestamped rows at :16 for a market running :15-:30, so its accuracy column
and Kalshi's settlement were scoring slightly different windows.

## The open question

The EMA-divergence rule (EMA BEAR → UP, EMA BULL → DOWN, no call on FLAT) scored
188/320 = 58.8%, z = +3.13 on v1 data. Two caveats keep it unvalidated:

- **In-sample.** Found by testing ~8 indicators against the same dataset, which
  inflates whichever one wins.
- **Unstable day to day**: 60.5%, 58.5%, 59.2%, 47.4%, 44.0%, 83.8%. Two of six
  days sat below breakeven and one outlier carries much of the average.

It did not replicate. Over v2's 61 graded rows it came in at 26/61 = 42.6%,
95% CI 30.2%-55.0% -- an interval that excludes the 58.8% v1 claimed (z=-2.55
against that estimate). Logging it in parallel with alerts off is what made
that visible before any money was on it. A cautionary example of why:
on the 55 v1 rows that did carry prices, the composite showed 60% accuracy and
+$202 P&L — but those rows turned out to be 13 time slots across two evenings,
and *every* prediction in those same slots ran 61.5%. The apparent edge was a
good Tuesday and Wednesday night, not a property of the model.


## v5: a preregistered forward test

v4 closed the microstructure question. Over 897 rows the opening quote was no
worse than the 60-second quote: on the 389 rows where they disagreed by 3c or
more the early side was right 75 times against the late side's 73 (McNemar
z=+0.16), Brier scores were tied, and the median spread was 1.0c from the
moment the book opened. The composite settled at 47.5% against a 47.2c average
entry, which is breakeven.

A search for profitable subgroups was then run properly, on a chronological
split. 1,567 rules over RSI, Bollinger position, order-book imbalance, VWAP
deviation, EMA, MACD, coin and session were screened on the older 70% of
history. The best hit 68-73%. On the held-out newer 30% they fell an average
of 20.5 percentage points; one went from 72.1% to 22.2%. That is what
searching noise looks like, and it is why no rule discovered by slicing past
data should ever be traded on the strength of that slice.

29 rules cleared 58% on both halves. That number is *lower* than the 50-90 such
survivors chance alone would produce from 1,567 screens, so it is not evidence
of signal. It is a candidate list, and the only honest way to test a candidate
list is forward.

All versions share the one `Predictions` tab. The **Model Version** column is
what separates the generations, and every analysis filters on it, so a second
sheet bought nothing and split the history. A brief "Predictions v5" tab existed
on 2026-09-09 and was abandoned.

Merging is selective. RSI, BB Position, OB Imbalance, VWAP Dev %, EMA Signal,
MACD Signal, Symbol, Timestamp and Actual Change % are calculated identically in
every version and pool cleanly -- 3,815 graded rows as of 2026-09-09. Four
columns look poolable and are not: Vol Spike Ratio is 100% populated in v1 but
was measuring a partial candle (median 0.10 against 1.5 later); Composite Score,
Confidence and Direction were computed in v1 with the Stoch leg dead and the Vol
leg inverted, and from v4 they are computed ~30s later in the window; Stoch RSI
is empty for all 2,564 v1 rows; K Up% reaches only 3% coverage in v1. Populated
is not the same as comparable.

The five rules in `PREREGISTERED_RULES` were fixed on 2026-09-09, before any v5
row existed, along with the accuracy each claimed. They are logged and scored
independently every window. **Do not add, remove or reword a rule while v5 is
collecting** -- editing the list turns the forward test back into a search and
destroys the only property that makes the result meaningful.

They fire on about 9.8% of predictions, roughly 47 signals a day.

Expect them to land near 50%. If one holds near its claimed accuracy over
several hundred fires, that is the first result in this project that would
justify money.


## v6: inputs the candles do not contain

The preregistered rules failed decisively. R1 claimed 73% and returned 55.6%,
R2 72% -> 22.2%, R3 71% -> 25.0%, R4 69% -> 50.0%, R5 66% -> 43.8%. Combined
that is 26/71 = 36.6%, z=-2.25 against a coin flip, for -$320 over 71 bets.
They fire on overbought conditions, which the market already prices at 52-67c,
so several needed 60%+ merely to break even.

That is the fifth negative in a row, and they all share one cause: every input
tried so far is computed from public OHLC candles, which is the same material
the Kalshi price is built from. A logistic fit of actual_up on price plus the
indicators has put the indicator terms at z~0 every time it has been run.

v6 therefore adds two inputs that are not derivable from candles at all.

**Order flow.** Kraken's public trade tape marks the taker side of every trade,
so aggressive buying can be separated from aggressive selling. Candles record
where price finished; the tape records who moved it. A drift upward on passive
limit fills and a move lifted by market buyers produce the same candle and
different tape. Logged as buy volume, sell volume, CVD ratio ((buy-sell)/total,
+1 all buying, -1 all selling), trade count, market-order fraction and the
window actually covered.

**Perpetual open interest and funding.** Kraken's futures venue publishes both
without auth. Change in open interest distinguishes positions being opened from
positions being closed, which no spot candle expresses. Contract symbols are
resolved against the live ticker list at runtime rather than hardcoded, and the
first run logs which matched.

**Collection only.** No rules, no alerts, nothing acting on these columns until
the same test that killed the composite has been run on them:

    actual_up ~ kalshi_price + order_flow + oi_change

If the order flow term lands at z~0 like every previous input, that is a clean
answer and this line of attack is finished. Nothing gets a strategy built on it
before that test runs.
