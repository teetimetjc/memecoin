"""Is the overnight window actually thinner, or is that just a story?

The decision to stop betting 11pm-7am CDT was justified with "overnight books
are thin, so orders sweep further and pay worse". That is a mechanism, which
is better than a bare correlation, but it was never measured -- it was
asserted because it sounds right.

This checks it against the M15 sheet, by hour of market close:

    SPREAD at T-9 (ask minus bid) -- the direct cost of crossing the book,
    and the best proxy available here for how far an order would sweep.
    VOLUME on the market.
    QUOTED DEPTH, if the collector populated the size columns at all.
    HOW OFTEN A FAVOURITE EVEN FORMS -- the share of markets reaching the
    90-96c band on either side.

WHAT IT CANNOT SEE: actual fills. The sheet records quotes, not executions,
and the overnight damage showed up as fills 23-33c below quote. A wide spread
is consistent with that and would support the story; it would not prove it.

If the overnight spread is NOT wider, the thin-book explanation is wrong and
the window result is probably just 46 bets of noise with one bad cluster in
them -- which would be worth knowing before building anything else on it.

Read-only.
"""
import collections, statistics as st, sys, time
import predictor as P
import score as S

MAJORS=set(S.MAJORS); ENTRY=S.ENTRY
BLOCKS=[(0,4,"00-04 UTC  7pm-11pm CT"),(4,8,"04-08 UTC  11pm-3am CT"),
        (8,12,"08-12 UTC  3am-7am CT"),(12,16,"12-16 UTC  7am-11am CT"),
        (16,20,"16-20 UTC  11am-3pm CT"),(20,24,"20-24 UTC  3pm-7pm CT")]

def main():
    sh=P._get_client().open_by_key(P.SPREADSHEET_ID)
    rows=sh.worksheet(S.TAB).get_all_values()
    h={k:i for i,k in enumerate(rows[0])}
    def c(r,k):
        i=h.get(k); return r[i] if i is not None and i<len(r) else ""
    have_sz = any(k in h for k in (f"bidsz{ENTRY}", f"asksz{ENTRY}"))
    M=[]
    for r in rows[1:]:
        if not r or not r[0]: continue
        if str(c(r,"Series")) not in MAJORS: continue
        t=S.ts(c(r,"Close Time"))
        if t is None: continue
        b,a=S._f(c(r,f"bid{ENTRY}")),S._f(c(r,f"ask{ENTRY}"))
        if b is None or a is None or not (0<b<=a<1): continue
        M.append(dict(h=time.gmtime(t).tm_hour, spread=100.0*(a-b), ask=a, bid=b,
                      vol=S._f(c(r,"Volume")),
                      bsz=S._f(c(r,f"bidsz{ENTRY}")) if have_sz else None,
                      asz=S._f(c(r,f"asksz{ENTRY}")) if have_sz else None))
    print(f"  {len(M):,} majors with a two-sided T-{ENTRY} quote")
    print(f"  size columns present in sheet: {have_sz}; "
          f"populated: {sum(1 for m in M if m['bsz'] is not None):,}")
    print()
    print(f"  {'block':<24} {'n':>5} {'spread':>9} {'median':>8} {'p90':>7} "
          f"{'volume':>9} {'favourite forms':>16}")
    for lo,hi,lab in BLOCKS:
        g=[m for m in M if lo<=m['h']<hi]
        if not g: continue
        sp=sorted(x['spread'] for x in g)
        vols=[x['vol'] for x in g if x['vol'] is not None]
        fav=sum(1 for x in g if 0.90<=x['ask']<0.96 or 0.90<=(1-x['bid'])<0.96)
        print(f"  {lab:<24} {len(g):>5,} {st.mean(sp):>8.2f}c "
              f"{sp[len(sp)//2]:>7.2f}c {sp[int(.9*len(sp))]:>6.2f}c "
              f"{(st.mean(vols) if vols else float('nan')):>9,.0f} "
              f"{100.0*fav/len(g):>15.1f}%")
    print()
    night=[m for m in M if 4<=m['h']<12]; day=[m for m in M if not (4<=m['h']<12)]
    for lab,g in (("OVERNIGHT 11pm-7am",night),("DAYTIME 7am-11pm",day)):
        sp=sorted(x['spread'] for x in g)
        vols=[x['vol'] for x in g if x['vol'] is not None]
        print(f"  {lab:<22} n={len(g):>5,}  mean spread {st.mean(sp):.2f}c  "
              f"median {sp[len(sp)//2]:.2f}c  p90 {sp[int(.9*len(sp))]:.2f}c  "
              f"mean volume {(st.mean(vols) if vols else float('nan')):,.0f}")
    ns=[x['spread'] for x in night]; ds=[x['spread'] for x in day]
    diff=st.mean(ns)-st.mean(ds)
    se=(st.pstdev(ns)**2/len(ns)+st.pstdev(ds)**2/len(ds))**0.5
    print(f"\n  overnight spread minus daytime: {diff:+.2f}c  (t={diff/se:+.1f})")
    print()
    if diff > 0.2:
        print("  WIDER OVERNIGHT. Consistent with the thin-book story: a wider")
        print("  spread means an order crossing it sweeps further. Consistent,")
        print("  not proof -- the sheet holds quotes, never fills.")
    else:
        print("  NOT WIDER OVERNIGHT. The thin-book explanation does not hold,")
        print("  and the window result is more likely 46 bets of noise around")
        print("  one bad cluster. Worth knowing before building on it.")
    return 0

if __name__=="__main__": sys.exit(main())
