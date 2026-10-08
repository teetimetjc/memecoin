"""Delete the tabs belonging to closed strategies. One time, by name, on purpose.

The sheet reached 91% of its ten-million-cell cap while M15 kept growing at
about 70,000 cells a day, which gave roughly fifteen days before writes would
start failing -- and a failing write would have ended two frozen specs that
cannot be restarted, since their value comes from having been declared before
the data existed.

THIS IS IRREVERSIBLE AND IT IS NOT CLEVER. There is no pattern matching, no
"delete anything unused", no heuristic. Every tab is named in DOOMED below,
one line each, with the test it belonged to and how that test died. A tab not
on the list is not touched, and a tab on PROTECTED is refused even if someone
later adds it to DOOMED by mistake -- the two lists are checked against each
other before anything is deleted.

RUN THE COLLECTORS DOWN FIRST. slowcollect, bots and arb write four of these
tabs every hour, so deleting without disabling them first just means they come
back. The workflows were disabled in the same change that added this file; if
one is still scheduled, its tab reappears within the hour and the space is not
recovered.

WHAT IS LOST. SLOWBOOK and Predictions hold the raw evidence behind two closed
findings. The conclusions survive in specs/ and in commit messages, but the
data behind them will not, so neither can be re-examined from scratch. The
owner was told this before approving, and took a copy of the sheet first.

Requires the literal argument `--yes-delete-them` so it cannot run by accident.
"""

import sys

import predictor as P

# Tab -> (what it was for, how it ended). Nothing is deleted that is not here.
DOOMED = {
    "SLOWBOOK": ("slow-market resting",
                 "fill quality real (+1.3c/contract), round trip -$0.53 to -$0.83"),
    "Predictions": ("v6 predictor bot", "retired, superseded by favlive"),
    "H60": ("hourly ladder arbitrage",
            "44 crossings were a sampling artifact; arb.py found 0"),
    "Kapelame Paper": ("third-party paper bot", "superseded by favlive"),
    "IND": ("indicator / multi-horizon study",
            "no split cleared break-even; regenerable by ind_backfill.py"),
    "ARB": ("live arbitrage watcher",
            "100 rows, all pre-fix and already excluded as invalid"),
    "EdgeHunter Paper": ("paper bot", "superseded by favlive"),
    "Challengers": ("v6 challenger models", "retired"),
    "Path": ("path study (cg1/cg7/cg9)", "closed"),
    "Dry Run": ("v6 dry-run log", "retired"),
    "Mid Window": ("mid-window entry study", "closed"),
    "Entry Decay": ("entry decay study", "closed"),
    "Settled": ("settlement cache", "v6-era; refetchable by settle_fetch.py"),
    "Fills": ("old fill log",
              "superseded; its Cost column was the limit-price bug"),
    "Report": ("v6 report", "retired"),
}

# Never deleted, whatever else says. M15H cannot be re-fetched: Kalshi's
# history window has moved past it. M15 is what three live tests score from.
PROTECTED = {"M15", "M15H", "Live Bets", "Control"}


def main():
    if "--yes-delete-them" not in sys.argv:
        print("Refusing: this deletes spreadsheet tabs permanently.")
        print("Re-run with --yes-delete-them if that is what you want.")
        return 2

    overlap = DOOMED.keys() & PROTECTED
    if overlap:
        print(f"ABORT: {sorted(overlap)} is on both lists. Nothing deleted.")
        return 1

    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    present = {ws.title: ws for ws in sh.worksheets()}

    print("=" * 94)
    print("PURGE — deleting tabs for closed strategies")
    print("=" * 94)
    before = sum(ws.row_count * ws.col_count for ws in present.values())
    print(f"  {len(present)} tabs, {before:,} grid cells "
          f"({100.0*before/10_000_000:.0f}% of the cap)\n")

    freed = 0
    gone, missing, failed = [], [], []
    for name, (what, how) in DOOMED.items():
        ws = present.get(name)
        if ws is None:
            missing.append(name)
            print(f"  -- {name:<20} already absent")
            continue
        if name in PROTECTED:                 # belt and braces
            print(f"  !! {name:<20} PROTECTED — skipped")
            continue
        cells = ws.row_count * ws.col_count
        try:
            sh.del_worksheet(ws)
        except Exception as e:
            failed.append(name)
            print(f"  !! {name:<20} FAILED: {str(e)[:60]}")
            continue
        freed += cells
        gone.append(name)
        print(f"  ok {name:<20} {cells:>9,} cells   {what} — {how}")

    after = before - freed
    print(f"\n  deleted {len(gone)}, already absent {len(missing)}, "
          f"failed {len(failed)}")
    print(f"  freed {freed:,} cells")
    print(f"  now {after:,} cells = {100.0*after/10_000_000:.0f}% of the cap")

    left = {t: w for t, w in present.items() if t not in gone}
    print("\n  what remains:")
    for t, w in sorted(left.items(), key=lambda kv: -kv[1].row_count * kv[1].col_count):
        mark = "  (protected)" if t in PROTECTED else ""
        print(f"    {t:<20} {w.row_count*w.col_count:>9,} cells{mark}")

    print("\n  M15 grows about 70,000 cells a day, so this buys roughly")
    print(f"  {(10_000_000-after)//70_000} days. Long-term storage needs")
    print("  archival or a database, not a bigger spreadsheet.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
