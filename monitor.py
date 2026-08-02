"""Price-drop monitor.

Checks one or more tickers and sends a push notification when either of two
things happens:

  1. daily drop      - price is N% below the PREVIOUS SESSION CLOSE.
                       This includes the overnight gap, which is the whole
                       point: an alert measured from today's open would miss
                       a fall that happened while the market was shut.
  2. drawdown        - price is N% below the highest close of the last K
                       sessions. This is the "how far from the top are we"
                       signal, which is usually more informative than the
                       move of a single day.

Rule 1 fires at most once per session. Rule 2 is latched: it fires on the
transition into the drawdown, not every time it is checked, and re-arms only
after the price recovers past a hysteresis band.

Run it with --dry-run to see the numbers without notifying anything.
"""

import argparse
import json
import os
import sys
from datetime import datetime
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


def measure(symbol, peak_days):
    """Return the current picture for one symbol, or None if there is no
    usable history."""
    hist = yf.Ticker(symbol).history(
        period=f"{peak_days + 40}d", interval="1d", auto_adjust=False
    )
    if len(hist) < 3:
        return None

    last, prev = hist.iloc[-1], hist.iloc[-2]
    session_date = hist.index[-1].date()

    price = float(last["Close"])
    open_px = float(last["Open"])
    prev_close = float(prev["Close"])

    # Peak over the trailing window, excluding the session in progress so a
    # a new high today cannot silently reset the drawdown to zero.
    window = hist["Close"].iloc[-(peak_days + 1):-1]
    peak_close = float(window.max())
    peak_date = window.idxmax().date()

    return {
        "symbol": symbol,
        "session": session_date.isoformat(),
        "is_today": session_date == datetime.now(NY).date(),
        "price": price,
        "open": open_px,
        "prev_close": prev_close,
        # The three ways of saying "it fell", which are NOT the same number.
        "vs_prev_close": (price / prev_close - 1.0) * 100.0,
        "vs_open": (price / open_px - 1.0) * 100.0,
        "gap": (open_px / prev_close - 1.0) * 100.0,
        "peak_close": peak_close,
        "peak_date": peak_date.isoformat(),
        "from_peak": (price / peak_close - 1.0) * 100.0,
    }


def notify(topic, title, body, priority, tags):
    """Push through ntfy.sh. No account, no token: the topic name IS the
    address, so pick an unguessable one."""
    if not topic:
        print("[no NTFY_TOPIC set, notification not sent]")
        return False
    resp = requests.post(
        f"https://ntfy.sh/{topic}",
        data=body.encode("utf-8"),
        headers={
            "Title": title,
            "Priority": priority,
            "Tags": tags,
        },
        timeout=20,
    )
    resp.raise_for_status()
    return True


def evaluate(m, daily_drop, drawdown, rearm, state, force):
    """Return the list of (rule, title, body, priority, tags) to send."""
    key = lambda rule: f"{m['symbol']}:{rule}"  # noqa: E731
    fired = []

    # Rule 1: once per session.
    if m["vs_prev_close"] <= -daily_drop:
        if force or state.get(key("daily_drop")) != m["session"]:
            fired.append((
                key("daily_drop"),
                m["session"],
                f"{m['symbol']} {m['vs_prev_close']:+.2f}% hoy",
                f"{m['price']:.2f} (cierre previo {m['prev_close']:.2f})\n"
                f"gap de apertura {m['gap']:+.2f}%, intradia {m['vs_open']:+.2f}%\n"
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
            f"hoy {m['vs_prev_close']:+.2f}%",
            "high",
            "warning",
        ))
    elif active and m["from_peak"] > -(drawdown - rearm):
        # Recovered past the hysteresis band: re-arm silently.
        state[key("drawdown_active")] = False

    return fired


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbols", default=env("SYMBOLS", "QQQ"))
    p.add_argument("--daily-drop", type=float, default=float(env("DAILY_DROP", "2.0")),
                   help="percent below previous close that triggers rule 1")
    p.add_argument("--drawdown", type=float, default=float(env("DRAWDOWN", "8.0")),
                   help="percent below the trailing peak that triggers rule 2")
    p.add_argument("--peak-days", type=int, default=int(env("PEAK_DAYS", "60")),
                   help="length of the trailing window for the peak")
    p.add_argument("--rearm", type=float, default=float(env("REARM", "2.0")),
                   help="recovery needed above the drawdown line before rule 2 can fire again")
    p.add_argument("--dry-run", action="store_true", help="print, do not notify, do not save state")
    p.add_argument("--force", action="store_true", help="ignore dedup and notify anyway")
    args = p.parse_args()

    topic = env("NTFY_TOPIC", "")
    state = load_state()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    sent = 0

    for symbol in symbols:
        m = measure(symbol, args.peak_days)
        if m is None:
            print(f"{symbol}: no data")
            continue

        stale = "" if m["is_today"] else "  [market closed, last session]"
        print(
            f"{m['symbol']} {m['session']}{stale}\n"
            f"  price          {m['price']:10.2f}\n"
            f"  vs prev close  {m['vs_prev_close']:+10.2f}%   (gap {m['gap']:+.2f}%"
            f" + intraday {m['vs_open']:+.2f}%)\n"
            f"  from {args.peak_days}d peak  {m['from_peak']:+10.2f}%   "
            f"(peak {m['peak_close']:.2f} on {m['peak_date']})"
        )

        for state_key, state_value, title, body, priority, tags in evaluate(
            m, args.daily_drop, args.drawdown, args.rearm, state, args.force
        ):
            print(f"  ALERT -> {title}")
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

    print(f"\n{sent} notification(s) sent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
