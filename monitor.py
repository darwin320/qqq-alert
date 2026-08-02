"""Price-drop monitor.

Checks one or more tickers and sends a push notification when either of two
things happens:

  1. daily drop      - price is N% below the last COMPLETED regular session
                       close. Measuring against that close instead of against
                       today's open is what makes the overnight gap part of
                       the number: an alert measured from the open ignores
                       everything that happened while the market was shut, and
                       on 5 years of QQQ that is half the events (see
                       analyze.py).
  2. drawdown        - price is N% below the highest close of the last K
                       sessions. This is the "how far from the top are we"
                       signal, which is usually more informative than the
                       move of a single day.

Rule 1 fires at most once per session. Rule 2 is latched: it fires on the
transition into the drawdown, not every time it is checked, and re-arms only
after the price recovers past a hysteresis band.

By default the current price comes from extended-hours data, so a gap that
forms before the opening bell is caught while it is forming rather than at
09:30. Pass --no-extended to look only at the regular session.

Thresholds can be set per symbol, because the same percentage is a very
different event on different tickers: -2% in a day happens 17.7 times a year
on QQQ and 8.0 times a year on SPY.

    QQQ                 both thresholds from the defaults
    QQQ:2.0             daily drop 2.0%
    QQQ:2.0:8.0         daily drop 2.0%, drawdown 8.0%
    QQQ:2.0:8.0,SPY:1.5 several symbols, each with its own

Run with --dry-run to see the numbers without notifying anything.
"""

import argparse
import json
import os
import sys
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))


def _use_local_ca_bundle():
    """Windows only: AV products (Norton here) intercept TLS with their own
    root, which is in the Windows cert store but not in the CA bundle shipped
    with certifi/curl_cffi. tools/make_ca_bundle.ps1 writes a merged bundle;
    use it when present. Never generated or committed for Linux/CI, which has
    no interception and must keep using the stock bundle."""
    if os.name != "nt":
        return
    path = os.path.join(HERE, "tools", "ca-bundle-local.pem")
    if os.path.exists(path):
        for var in ("CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE"):
            os.environ.setdefault(var, path)


_use_local_ca_bundle()

import requests  # noqa: E402
import yfinance as yf  # noqa: E402

NY = ZoneInfo("America/New_York")
STATE_FILE = os.path.join(HERE, "state.json")
MARKET_OPEN = dtime(9, 30)
MARKET_CLOSE = dtime(16, 0)


def env(name, default):
    value = os.environ.get(name, "").strip()
    return value if value else default


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
        fh.write("\n")


def parse_specs(raw, default_drop, default_drawdown):
    """'QQQ:2.0:8.0,SPY' -> [(symbol, daily_drop, drawdown), ...]"""
    specs = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split(":")
        symbol = parts[0].strip().upper()
        drop = float(parts[1]) if len(parts) > 1 and parts[1].strip() else default_drop
        draw = float(parts[2]) if len(parts) > 2 and parts[2].strip() else default_drawdown
        specs.append((symbol, drop, draw))
    return specs


def intraday_bars(ticker):
    """Minute bars for the last few days including pre- and post-market."""
    try:
        bars = ticker.history(period="5d", interval="1m", prepost=True, auto_adjust=False)
    except Exception as exc:  # network hiccup: fall back to the daily bar
        print(f"  (extended-hours lookup failed: {exc})")
        return None
    return None if bars.empty else bars


def measure(symbol, peak_days, extended=True):
    """Return the current picture for one symbol, or None if there is no
    usable history."""
    ticker = yf.Ticker(symbol)
    hist = ticker.history(period=f"{peak_days + 40}d", interval="1d", auto_adjust=False)
    if len(hist) < 3:
        return None

    daily_date = hist.index[-1].date()
    price = float(hist["Close"].iloc[-1])
    session_date = daily_date
    phase = "regular close"
    as_of = None
    session_low = None

    if extended:
        bars = intraday_bars(ticker)
        if bars is not None and bars.index[-1].date() >= daily_date:
            as_of = bars.index[-1]
            price = float(bars["Close"].iloc[-1])
            session_date = as_of.date()
            clock = as_of.time()
            if clock < MARKET_OPEN:
                phase = "pre-market"
            elif clock >= MARKET_CLOSE:
                phase = "after-hours"
            else:
                phase = "regular session"
            # Lowest print of the session so far, extended hours included. A
            # dip that recovers between two 30-minute checks is invisible to
            # the spot price but shows up here: in 2026 QQQ touched -2% on 24
            # days and only closed there on 6.
            #
            # Yahoo's extended-hours minute bars report Volume 0 and their Low
            # is not trustworthy: on 2026-07-31 17:32 one printed Low 667.36
            # with its own Close at 684.98 and both neighbours near 684.8, a
            # phantom -2.4% that would have fired a false alert. The Close of
            # those bars does track. So trust Low only where volume proves a
            # trade happened, and fall back to Close everywhere else.
            today_bars = bars[bars.index.date == session_date]
            if not today_bars.empty:
                traded = today_bars[today_bars["Volume"] > 0]
                lows = [float(today_bars["Close"].min())]
                if not traded.empty:
                    lows.append(float(traded["Low"].min()))
                session_low = min(lows)

    # A tick from a session the daily bars do not have yet means the regular
    # session has not opened: the reference close is the last daily bar, and
    # there is no open to split the move against.
    pre_open = session_date > daily_date
    if pre_open:
        prev_close = float(hist["Close"].iloc[-1])
        open_px = None
        window = hist["Close"].iloc[-peak_days:]
    else:
        prev_close = float(hist["Close"].iloc[-2])
        open_px = float(hist["Open"].iloc[-1])
        # Exclude the session in progress so a new high today cannot silently
        # reset the drawdown to zero.
        window = hist["Close"].iloc[-(peak_days + 1):-1]

    peak_close = float(window.max())
    peak_date = window.idxmax().date()
    vs_prev_close = (price / prev_close - 1.0) * 100.0

    return {
        "symbol": symbol,
        "session": session_date.isoformat(),
        "phase": phase,
        "as_of": as_of.isoformat() if as_of is not None else None,
        "is_today": session_date == datetime.now(NY).date(),
        "price": price,
        "prev_close": prev_close,
        # The whole move so far, gap included. This is the number rule 1 uses.
        "vs_prev_close": vs_prev_close,
        # Split into overnight and intraday, for context in the message. Before
        # the opening bell the entire move is overnight by definition.
        "gap": vs_prev_close if pre_open else (open_px / prev_close - 1.0) * 100.0,
        "intraday": None if pre_open else (price / open_px - 1.0) * 100.0,
        "peak_close": peak_close,
        "peak_date": peak_date.isoformat(),
        "from_peak": (price / peak_close - 1.0) * 100.0,
        "session_low": session_low,
        "low_vs_prev": (
            None if session_low is None else (session_low / prev_close - 1.0) * 100.0
        ),
    }


def notify(topic, title, body, priority, tags):
    """Push through ntfy.sh. No account, no token: the topic name IS the
    address, so pick an unguessable one."""
    if not topic:
        print("  [no NTFY_TOPIC set, notification not sent]")
        return False
    resp = requests.post(
        f"https://ntfy.sh/{topic}",
        data=body.encode("utf-8"),
        headers={"Title": title, "Priority": priority, "Tags": tags},
        timeout=20,
    )
    resp.raise_for_status()
    return True


def split_text(m):
    if m["intraday"] is None:
        return f"todo fuera de sesion (gap {m['gap']:+.2f}%)"
    return f"gap {m['gap']:+.2f}% + intradia {m['intraday']:+.2f}%"


def worst_move(m, use_low):
    """The drop rule 1 is judged on: the spot price, or the deepest print of
    the session when low mode is on."""
    if use_low and m["low_vs_prev"] is not None:
        return min(m["vs_prev_close"], m["low_vs_prev"])
    return m["vs_prev_close"]


def evaluate(m, daily_drop, drawdown, rearm, state, force, use_low=False):
    """Return the list of (state_key, state_value, title, body, priority, tags)."""
    key = lambda rule: f"{m['symbol']}:{rule}"  # noqa: E731
    fired = []

    # Rule 1: once per session.
    move = worst_move(m, use_low)
    if move <= -daily_drop:
        if force or state.get(key("daily_drop")) != m["session"]:
            recovered = ""
            if move < m["vs_prev_close"] - 0.05:
                recovered = f"\nahora va en {m['vs_prev_close']:+.2f}%"
            fired.append((
                key("daily_drop"),
                m["session"],
                f"{m['symbol']} {move:+.2f}% ({m['phase']})",
                f"{m['price']:.2f} contra cierre previo {m['prev_close']:.2f}\n"
                f"{split_text(m)}{recovered}\n"
                f"{m['from_peak']:+.1f}% desde el max de {m['peak_date']}",
                "default",
                "chart_with_downwards_trend",
            ))

    # Rule 2: latched, so a long drawdown does not notify every 30 minutes.
    active = bool(state.get(key("drawdown_active")))
    if m["from_peak"] <= -drawdown and (force or not active):
        fired.append((
            key("drawdown_active"),
            True,
            f"{m['symbol']} {m['from_peak']:.1f}% bajo su maximo",
            f"{m['price']:.2f} contra {m['peak_close']:.2f} del {m['peak_date']}\n"
            f"hoy {m['vs_prev_close']:+.2f}% ({m['phase']})",
            "high",
            "warning",
        ))
    elif active and m["from_peak"] > -(drawdown - rearm):
        # Recovered past the hysteresis band: re-arm silently.
        state[key("drawdown_active")] = False

    return fired


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--symbols", default=env("SYMBOLS", "QQQ"),
                   help="SYM[:daily_drop[:drawdown]] separated by commas")
    p.add_argument("--daily-drop", type=float, default=float(env("DAILY_DROP", "2.0")),
                   help="default percent below the previous close for rule 1")
    p.add_argument("--drawdown", type=float, default=float(env("DRAWDOWN", "8.0")),
                   help="default percent below the trailing peak for rule 2")
    p.add_argument("--peak-days", type=int, default=int(env("PEAK_DAYS", "60")),
                   help="length of the trailing window for the peak")
    p.add_argument("--rearm", type=float, default=float(env("REARM", "2.0")),
                   help="recovery above the drawdown line before rule 2 can fire again")
    p.add_argument("--no-extended", dest="extended", action="store_false",
                   help="ignore pre- and post-market trading")
    p.add_argument("--no-use-low", dest="use_low", action="store_false",
                   help="judge rule 1 on the spot price only, ignoring dips that "
                        "already recovered")
    p.add_argument("--dry-run", action="store_true",
                   help="print, do not notify, do not save state")
    p.add_argument("--force", action="store_true", help="ignore dedup and notify anyway")
    p.set_defaults(
        extended=env("EXTENDED", "1") not in ("0", "false", "no"),
        use_low=env("USE_LOW", "1") not in ("0", "false", "no"),
    )
    args = p.parse_args()

    topic = env("NTFY_TOPIC", "")
    state = load_state()
    sent = 0

    for symbol, daily_drop, drawdown in parse_specs(
        args.symbols, args.daily_drop, args.drawdown
    ):
        m = measure(symbol, args.peak_days, args.extended)
        if m is None:
            print(f"{symbol}: no data")
            continue

        stamp = m["as_of"] or m["session"]
        stale = "" if m["is_today"] else "   [ultima sesion disponible]"
        low_line = ""
        if m["low_vs_prev"] is not None:
            flag = " <- se juzga por aqui" if args.use_low else " (informativo)"
            low_line = (
                f"\n  minimo sesion     {m['low_vs_prev']:+10.2f}%   "
                f"({m['session_low']:.2f}){flag}"
            )
        print(
            f"{m['symbol']}  {stamp}  {m['phase']}{stale}\n"
            f"  precio            {m['price']:10.2f}\n"
            f"  vs cierre previo  {m['vs_prev_close']:+10.2f}%   ({split_text(m)})"
            f"   umbral {-daily_drop:.1f}%{low_line}\n"
            f"  vs max {args.peak_days}d       {m['from_peak']:+10.2f}%   "
            f"(max {m['peak_close']:.2f} del {m['peak_date']})   umbral {-drawdown:.1f}%"
        )

        for state_key, state_value, title, body, priority, tags in evaluate(
            m, daily_drop, drawdown, args.rearm, state, args.force, args.use_low
        ):
            print(f"  ALERTA -> {title}")
            if args.dry_run:
                continue
            if notify(topic, title, body, priority, tags):
                sent += 1
            state[state_key] = state_value

    if not args.dry_run:
        # Bumping this once a day keeps the repo active, which stops GitHub
        # from auto-disabling the schedule after 60 days of no commits.
        state["last_run_date"] = datetime.now(NY).date().isoformat()
        save_state(state)

    print(f"\n{sent} notificacion(es) enviada(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
