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

---

## Champion / challenger (added 2026-09-14)

v6's CVD rule is the **champion**: frozen, logged, never edited. Improving on it
is not a matter of editing it -- an edited rule has no history, and its forward
test restarts at zero fires.

Instead, **challengers** are logged in parallel on the same rows. C1, C2 and C3
are frozen as of 2026-09-14:

| | fires when | mechanism |
|---|---|---|
| **C1** | CVD rule fires **and** Kalshi spread <= 1.0c | a tight book means the market is confident in its price, so flow against it is more likely genuine overreaction than book uncertainty |
| **C2** | CVD rule fires **and** market-order fraction >= 0.5 | market orders are the impatient ones; a window filled by resting limits is drift, and drift should not revert |
| **C3** | \|CVD\| >= 0.50 | if overreaction drives the edge, more extreme one-sidedness should revert harder -- tests whether the effect is monotone rather than an artifact of one threshold |

Every one comes from a mechanism, not from a search. That is the whole lesson of
R1-R5: those five were the survivors of 1,567 screened combinations, claimed
66-73%, and delivered 44/97 = 45.4%.

### Why the same tab

Champion and challenger fire on the **same row**, under the same market
conditions. A good hour helps both, so the difference between them isolates the
improvement. Split across tabs and that pairing is lost, and each rule is back
to being judged against zero -- which takes roughly three times the data.

### How promotion is decided

**Corrected 2026-09-14.** The first version used McNemar on the disagreements.
That was wrong for these challengers and could never have returned an answer:
C1, C2 and C3 are *filters* -- same side as the champion, on a subset of its
rows -- so wherever both fire they agree by construction. McNemar counts
disagreements, and there are none. It would have printed "no disagreements yet"
forever. 224 logged rows confirmed it: zero side disagreements, zero outcome
disagreements.

The answerable question for a filter is selection, not disagreement: of the rows
the champion bet, did the filter keep the better ones? So the rows it kept are
compared against the rows it passed on, by EV per bet, with a two-sample
(Welch) z:

    z = (EV_kept - EV_passed) / sqrt(var_kept/n_kept + var_passed/n_passed)

Both sides need at least 30 bets before it reports. Note this is **not** paired,
so market regime no longer cancels and it needs more data than a paired test
would -- that advantage only exists for challengers that can actually disagree
with the champion, such as one that takes a different side. A future challenger
of that kind should be scored with McNemar instead.

Promotion requires |z| >= the **Bonferroni** critical value for the number of
live challengers -- 2.39 at three, not 1.96. Three challengers get three chances
to win by luck; at 1.96 each, one of three clears under a pure null about 7% of
the time. Verified by simulation: 300 null runs of 400 paired rows produce 1
spurious "BEATS champion" (0.3%) with the correction, against ~7% without.

That bar is deliberately expensive. In the same simulation a challenger genuinely
running 70% against a 54% champion over 400 paired rows reached z=+2.09 and was
**not** promoted. Waiting is the cost of not repeating R1-R5.

### Rules of the loop

- Freeze the definition and the date before any data exists for it.
- Never edit a live challenger. Editing resets its clock to zero.
- Promotion requires beating the **champion**, not beating zero.
- Count the challengers. Adding a fourth raises the bar for all of them.
- Losers stay logged. Their continued failure is evidence.


### Correction, 2026-09-14 (second)

Two bugs, found by checking the first real export rather than trusting the code.

**The comparison pool spanned time the challengers did not exist.** A row logged
before a challenger was deployed has blank challenger columns -- on the sheet,
indistinguishable from a row where it declined to fire. So "passed on" swept in
395 champion bets from the three days before challengers existed, against 32
genuine ones. C1's z=+0.59 was comparing eleven hours to three days: a
comparison of time periods, not of rules. Every challenger figure is now
restricted to rows timestamped at or after that challenger's freeze time, and
`frozen` carries a full timestamp rather than a date so the cut is exact.

**C2 could never fire.** Its threshold required a market-order fraction >= 0.5.
Measured over 1,330 rows that fraction runs median 0.088, p75 0.134, and only 3
rows ever reached 0.5. I chose 0.5 by assuming what the number meant instead of
looking at it. C2 is retired at 0 fires, kept in the list so the error stays on
the record, and replaced by **C4** at >= 0.15 -- just above the measured p75,
chosen only so the rule fires often enough to test. No outcome was consulted in
setting it. Retired challengers no longer consume Bonferroni alpha.

The lesson is the same one R1-R5 taught, in a new place: a threshold picked from
an assumption about the data is as untested as a rule picked from a search.

### C5, added 2026-09-14 22:45 -- and why it is different

C1-C4 were frozen from a mechanism before their data existed. **C5 was not.**
It came from slicing 565 settled bets by entry price after seeing the result:

| entry | bets | hit % | breakeven | net | share of P&L |
|---|---|---|---|---|---|
| <40c | 92 | 44.6% | 32.4% | **+$347.26** | **114%** |
| 40-50c | 180 | 46.7% | 45.2% | +$5.43 | 2% |
| 50-60c | 195 | 61.0% | 54.0% | +$178.73 | 59% |
| >=60c | 98 | 52.0% | 65.8% | -$226.05 | -74% |

Remove the longshots and the other 473 bets run at -$0.09/bet. The split tests
z=+2.13 -- past 1.96, short of the 2.50 that four live challengers require, and
found by examining four buckets, which is four chances at a good-looking one.

This is the shape of R1-R5: retrospective slices claiming 66-73% that delivered
45.4%. C5 is therefore **logged, not believed**. A mechanism can be told for it
-- the market misprices tail moves that order flow anticipates -- but it was
written after the number, which is backwards.

Adding a fourth live challenger raises the Bonferroni bar for all of them, from
z >= 2.39 to z >= 2.50. That cost is real and is the reason not to add
challengers freely: each one makes every other one harder to promote.

### Clustering, 2026-09-15 -- every interval in this report was too narrow

Five coins bet in the same 15-minute window are not five independent bets.
Crypto moves together, the rule reads the same market-wide flow on each tape,
and 31 of 85 multi-coin windows put every coin on the SAME side. Over 771 bets,
windows where all five lost happened 10 times against 2.1 expected under
independence; implied average correlation ~0.58. Every big losing streak --
12 in a row, then 10 -- spans all five coins at once.

Treating bets as independent understated the spread by about 2x:

| | bet-level | clustered by window |
|---|---|---|
| champion EV interval | -$0.15 to +$1.46 | **-$1.05 to +$2.36** |
| effective sample | 771 bets | ~171 |
| more data needed | ~370 fires (~2 days) | ~4,450 bets (~3 weeks) |

**This changed a promotion.** C5 printed `z=+3.15 vs 2.50 -- BEATS champion` on
2026-09-15. Clustered, it is z=+2.17 and does not clear the bar. A promotion is
the one output here someone would act on, so it now gets the conservative
interval, as does every verdict.

Diversification across the five coins is therefore much weaker than the bet
count suggests: spreading the same stake over five coins cuts per-window risk
only 1.22x, against the 2.24x independence would give.
