"""Which tickers are worth adding to the monitor.

This does NOT rank anything as an investment. It answers a narrower and purely
mechanical question: if you add ticker X to the alert list, how many
notifications will it produce, at what threshold, and how many of them tell you
something you would not already have learned from the alerts you get on QQQ.

Three things decide that:

  frequency   the threshold that yields a usable rate. Too low and you mute it,
              too high and it never speaks.
  redundancy  the share of X's alert days that are also QQQ alert days. A
              ticker that only ever drops when the whole index drops adds
              noise, not information.
  depth       how the alert reads today, so the numbers are not abstract.

Alert days are measured on the SESSION LOW against the previous close, because
that is what monitor.py now judges on.

  python candidates.py
"""

import os
import sys

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

BASE = "QQQ"
BASE_THRESHOLD = 2.0
TARGET_ALERTS = 12.0  # roughly one a month
GRID = [round(x * 0.5, 1) for x in range(3, 25)]  # 1.5% .. 12.0%
CANDIDATES = [
    "QQQ", "SPY", "IWM",
    "NVDA", "MSFT", "AAPL", "GOOGL", "AMZN", "META", "TSLA", "AVGO",
    "GLD", "XLE", "TLT",
]


def touch_series(df):
    """Percent of the session low below the previous close, per session."""
    prev = df["Close"].shift(1)
    return ((df["Low"] / prev - 1.0) * 100.0).dropna()


def main():
    years = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    raw = yf.download(
        CANDIDATES, period=f"{years}y", interval="1d",
        group_by="ticker", auto_adjust=False, progress=False, threads=True,
    )

    touches, frames = {}, {}
    for sym in CANDIDATES:
        try:
            df = raw[sym].dropna(subset=["Close"])
        except KeyError:
            print(f"  ({sym}: sin datos)")
            continue
        if len(df) < 250:
            print(f"  ({sym}: historial corto, {len(df)} sesiones)")
            continue
        frames[sym] = df
        touches[sym] = touch_series(df)

    span = len(touches[BASE]) / 252.0
    base_days = set(touches[BASE][touches[BASE] <= -BASE_THRESHOLD].index)
    print(f"Ventana: {span:.1f} anios, {len(touches[BASE])} sesiones, "
          f"hasta {frames[BASE].index[-1].date()}")
    print(f"Referencia: {BASE} tocando -{BASE_THRESHOLD}% -> {len(base_days)} dias "
          f"({len(base_days) / span:.1f}/anio)\n")

    rows = []
    for sym, t in touches.items():
        # Threshold whose alert rate lands closest to the target.
        best = min(GRID, key=lambda g: abs(int((t <= -g).sum()) / span - TARGET_ALERTS))
        days = set(t[t <= -best].index)
        rate = len(days) / span
        overlap = len(days & base_days) / len(days) * 100.0 if days else float("nan")

        ret = frames[sym]["Close"].pct_change().dropna()
        base_ret = frames[BASE]["Close"].pct_change().dropna()
        joined = ret.align(base_ret, join="inner")
        corr = joined[0].corr(joined[1]) * 100.0
        vol = ret.std() * (252 ** 0.5) * 100.0

        close = frames[sym]["Close"]
        peak = float(close.iloc[-61:-1].max())
        dd = (float(close.iloc[-1]) / peak - 1.0) * 100.0

        rows.append((sym, vol, best, rate, overlap, corr, dd,
                     int((t <= -2.0).sum()) / span))

    rows.sort(key=lambda r: r[4])  # least redundant first

    print(f"{'sym':<7}{'vol an.':>9}{'umbral':>8}{'alertas/a':>11}"
          f"{'ya en QQQ':>11}{'corr QQQ':>10}{'vs max60':>10}{'-2%/anio':>10}")
    print("-" * 76)
    for sym, vol, best, rate, overlap, corr, dd, r2 in rows:
        mark = "  <- base" if sym == BASE else ""
        print(f"{sym:<7}{vol:>8.1f}%{-best:>7.1f}%{rate:>11.1f}"
              f"{overlap:>10.0f}%{corr:>9.0f}%{dd:>9.1f}%{r2:>10.1f}{mark}")

    print("\nColumnas:")
    print("  umbral     el que produce ~1 alerta al mes en ese papel")
    print("  alertas/a  cuantas al anio a ese umbral")
    print("  ya en QQQ  de esas alertas, cuantas caen en un dia que QQQ ya "
          "toco -2%.")
    print("             Alto = te avisa de lo que ya sabrias. Bajo = senal propia.")
    print("  vs max60   distancia al maximo de las ultimas 60 sesiones, hoy")
    print("  -2%/anio   dias por anio que toca -2%, para comparar todos con la "
          "misma vara")


if __name__ == "__main__":
    main()
