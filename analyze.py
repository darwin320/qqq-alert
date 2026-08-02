"""Threshold calibration.

Answers the only two questions that matter before turning an alert on:

  - how many notifications per year does each threshold actually produce?
    (a threshold that fires every week gets muted and stops working)
  - does the overnight gap matter enough to change how the drop is measured?

It replays the real daily history and counts, instead of guessing.

  python analyze.py --symbol QQQ --years 5 --highlight 2026-07-29
"""

import argparse
import os
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))


def _use_local_ca_bundle():
    if os.name != "nt":
        return
    path = os.path.join(HERE, "tools", "ca-bundle-local.pem")
    if os.path.exists(path):
        for var in ("CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE"):
            os.environ.setdefault(var, path)


_use_local_ca_bundle()

import yfinance as yf  # noqa: E402


def build(symbol, years):
    h = yf.Ticker(symbol).history(period=f"{years}y", interval="1d", auto_adjust=False)
    h = h[["Open", "High", "Low", "Close"]].copy()
    h["prev_close"] = h["Close"].shift(1)
    h["vs_prev_close"] = (h["Close"] / h["prev_close"] - 1.0) * 100.0
    h["vs_open"] = (h["Close"] / h["Open"] - 1.0) * 100.0
    h["gap"] = (h["Open"] / h["prev_close"] - 1.0) * 100.0
    return h.dropna()


def section(title):
    print(f"\n{title}\n" + "-" * len(title))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbol", default="QQQ")
    p.add_argument("--years", type=int, default=5)
    p.add_argument("--peak-days", type=int, default=60)
    p.add_argument("--highlight", default=None, help="YYYY-MM-DD to inspect in detail")
    args = p.parse_args()

    h = build(args.symbol, args.years)
    n = len(h)
    span_years = n / 252.0
    print(f"{args.symbol}: {n} sessions, {h.index[0].date()} to {h.index[-1].date()}"
          f" ({span_years:.1f} years)")

    section("Rule 1: daily drop, alerts per year by threshold")
    print(f"{'threshold':>10} {'vs prev close':>28} {'vs open (misses the gap)':>28}")
    for t in (1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0):
        a = int((h["vs_prev_close"] <= -t).sum())
        b = int((h["vs_open"] <= -t).sum())
        print(f"{-t:>9.1f}% {a:>6} days = {a / span_years:>6.1f}/yr    "
              f"{b:>6} days = {b / span_years:>6.1f}/yr")

    section("How much the two measures disagree")
    both = (h["vs_prev_close"] <= -2.0) & (h["vs_open"] <= -2.0)
    only_prev = (h["vs_prev_close"] <= -2.0) & (h["vs_open"] > -2.0)
    only_open = (h["vs_prev_close"] > -2.0) & (h["vs_open"] <= -2.0)
    print(f"at a -2.0% threshold:")
    print(f"  caught by both measures          {int(both.sum()):>4}")
    print(f"  only by 'vs previous close'      {int(only_prev.sum()):>4}  "
          f"(missed if you measure from the open)")
    print(f"  only by 'vs open'                {int(only_open.sum()):>4}  "
          f"(intraday fall after an up gap)")
    worst = h.nsmallest(10, "vs_prev_close")
    print("\n  ten worst sessions, split into gap and intraday:")
    print(f"  {'date':<12}{'total':>9}{'gap':>9}{'intraday':>10}{'gap share':>11}")
    for idx, row in worst.iterrows():
        share = abs(row["gap"]) / (abs(row["gap"]) + abs(row["vs_open"])) * 100.0
        print(f"  {idx.date()!s:<12}{row['vs_prev_close']:>8.2f}%{row['gap']:>8.2f}%"
              f"{row['vs_open']:>9.2f}%{share:>10.0f}%")

    section(f"Rule 2: drawdown from the {args.peak_days}-session peak")
    peak = h["Close"].rolling(args.peak_days).max().shift(1)
    dd = (h["Close"] / peak - 1.0) * 100.0
    dd = dd.dropna()
    dd_years = len(dd) / 252.0
    print(f"{'threshold':>10} {'days below':>12} {'episodes':>10} {'alerts/yr':>11}")
    for t in (5.0, 8.0, 10.0, 12.0, 15.0, 20.0):
        below = dd <= -t
        # Count entries into the state, which is what a latched alert sends.
        episodes = int((below & ~below.shift(1, fill_value=False)).sum())
        print(f"{-t:>9.1f}% {int(below.sum()):>12} {episodes:>10} "
              f"{episodes / dd_years:>10.1f}")
    print(f"\n  current drawdown: {dd.iloc[-1]:+.2f}%")

    if args.highlight:
        section(f"Detail for {args.highlight}")
        target = datetime.strptime(args.highlight, "%Y-%m-%d").date()
        match = h[h.index.date == target]
        if match.empty:
            print("  no session on that date")
        else:
            r = match.iloc[0]
            print(f"  previous close {r['prev_close']:.2f}")
            print(f"  open           {r['Open']:.2f}   gap {r['gap']:+.2f}%")
            print(f"  low            {r['Low']:.2f}")
            print(f"  close          {r['Close']:.2f}")
            print(f"  total move     {r['vs_prev_close']:+.2f}%"
                  f"   (gap {r['gap']:+.2f}% + intraday {r['vs_open']:+.2f}%)")
            d = float((r["Close"] / peak.loc[match.index[0]] - 1.0) * 100.0)
            print(f"  vs {args.peak_days}d peak    {d:+.2f}%")
            for t in (2.0, 3.0):
                hit = "YES" if r["vs_prev_close"] <= -t else "no"
                print(f"  would a -{t:.0f}% daily alert have fired? {hit}")


if __name__ == "__main__":
    main()
