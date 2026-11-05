"""Daily stock scanner: emails you when a watchlist stock triggers a signal.

Signals (each fires only on the day it happens):
  - Breakout / breakdown of 20-day resistance / support (needs above-average volume)
  - 50-day and 200-day moving average crosses
  - Golden cross / death cross (50-day vs 200-day average)
  - RSI drops below 30 (oversold) or rises above 70 (overbought)
  - New 52-week high / low
  - Institutional buying footprints (see inst_buying_alerts.py):
      FOLLOW-THRU day in SPY/QQQ and ACCUMULATION days in watchlist stocks
Educational tool only, not financial advice.
"""
import os
import smtplib
import sys
from datetime import datetime
from email.message import EmailMessage
from zoneinfo import ZoneInfo

import yfinance as yf

import inst_buying_alerts as inst

# ---------- Settings you can change ----------
WATCHLIST = ["AAPL", "MSFT", "NVDA", "AMZN", "TSLA", "META", "SPY",
             "RDDT", "GOOGL", "INTC", "CRWD", "PANW", "WCBR"]
LOOKBACK = 20              # days for support / resistance
MIN_BREAK_VOLUME = 1.2     # breakouts/breakdowns need volume >= this x average
RSI_PERIOD = 14
RSI_LOW, RSI_HIGH = 30, 70
ATR_PERIOD = 14
ATR_STOP_MULT = 2          # suggested stop = close - 2 x ATR on breakouts
SEND_IF_NOTHING = False    # set True to get a daily "no signals" email
# ---------------------------------------------


def rsi(close, period=RSI_PERIOD):
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss
    return 100 - 100 / (1 + rs)


def atr(df, period=ATR_PERIOD):
    prev = df["Close"].shift(1)
    tr = (df["High"] - df["Low"]).combine((df["High"] - prev).abs(), max).combine((df["Low"] - prev).abs(), max)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def crossed_above(t_a, t_b, y_a, y_b):
    return t_a > t_b and y_a <= y_b


def crossed_below(t_a, t_b, y_a, y_b):
    return t_a < t_b and y_a >= y_b


NY = ZoneInfo("America/New_York")
OPEN_RUN_WINDOW_MIN = 55   # the "open" run only scans within this many minutes after 9:30 ET

# Rough share of a normal day's volume that has traded N minutes after the open
# (volume is heavy at the open and close, lighter at midday).
_VOLUME_CURVE = [(0, 0.0), (30, 0.13), (60, 0.22), (120, 0.35), (180, 0.45),
                 (240, 0.55), (300, 0.66), (360, 0.80), (390, 1.0)]


def minutes_since_open(now=None):
    now = (now or datetime.now(NY)).astimezone(NY)
    return (now - now.replace(hour=9, minute=30, second=0, microsecond=0)).total_seconds() / 60


def day_fraction_done(now=None):
    """Share of today's volume that has normally traded so far (1.0 outside market hours)."""
    m = minutes_since_open(now)
    if m <= 0 or m >= 390:
        return 1.0
    for (m0, f0), (m1, f1) in zip(_VOLUME_CURVE, _VOLUME_CURVE[1:]):
        if m <= m1:
            return max(0.03, f0 + (f1 - f0) * (m - m0) / (m1 - m0))
    return 1.0


def run_label(now=None):
    m = minutes_since_open(now)
    if m < 0:
        return "pre-market"
    if m < 120:
        return "market open"
    if m < 390:
        return "midday"
    return "after close"


def download(ticker):
    df = yf.download(ticker, period="2y", auto_adjust=True, progress=False)
    if hasattr(df.columns, "levels"):
        df.columns = df.columns.get_level_values(0)
    # During market hours today's volume is partial: project it to a full day
    # so the volume filters still mean something.
    frac = day_fraction_done()
    if not df.empty and frac < 1.0 and df.index[-1].date() == datetime.now(NY).date():
        df.loc[df.index[-1], "Volume"] = df["Volume"].iloc[-1] / frac
    return df


def check(ticker, spy=None):
    df = download(ticker)
    if df.empty or len(df) < 260:
        return [f"{ticker}: not enough data"]
    raw = df[["Open", "High", "Low", "Close", "Volume"]].copy()

    c = df["Close"]
    df["res"] = c.rolling(LOOKBACK).max().shift(1)
    df["sup"] = c.rolling(LOOKBACK).min().shift(1)
    df["hi52"] = c.rolling(252).max().shift(1)
    df["lo52"] = c.rolling(252).min().shift(1)
    df["vol_avg"] = df["Volume"].rolling(LOOKBACK).mean().shift(1)
    df["ma50"] = c.rolling(50).mean()
    df["ma200"] = c.rolling(200).mean()
    df["rsi"] = rsi(c)
    df["atr"] = atr(df)

    t, y = df.iloc[-1], df.iloc[-2]          # today, yesterday
    close = float(t["Close"])
    vol_x = float(t["Volume"] / t["vol_avg"]) if t["vol_avg"] else 0.0
    trend = "uptrend" if close > t["ma50"] else "downtrend"
    info = f"vol {vol_x:.1f}x avg | RSI {t['rsi']:.0f} | {trend} | {df.index[-1].date()}"
    alerts = []

    # 1. Support / resistance (volume filter)
    if crossed_above(close, t["res"], y["Close"], y["res"]) and vol_x >= MIN_BREAK_VOLUME:
        stop = close - ATR_STOP_MULT * float(t["atr"])
        alerts.append(f"BREAKOUT   {ticker} ${close:,.2f} above resistance ${t['res']:,.2f} "
                      f"| suggested stop ${stop:,.2f} | {info}")
    if crossed_below(close, t["sup"], y["Close"], y["sup"]) and vol_x >= MIN_BREAK_VOLUME:
        alerts.append(f"BREAKDOWN  {ticker} ${close:,.2f} below support ${t['sup']:,.2f} | {info}")

    # 2. Price vs moving averages
    for ma, days in (("ma50", 50), ("ma200", 200)):
        if crossed_above(close, t[ma], y["Close"], y[ma]):
            alerts.append(f"CROSS UP   {ticker} ${close:,.2f} above {days}-day avg ${t[ma]:,.2f} | {info}")
        if crossed_below(close, t[ma], y["Close"], y[ma]):
            alerts.append(f"CROSS DOWN {ticker} ${close:,.2f} below {days}-day avg ${t[ma]:,.2f} | {info}")

    # 3. Golden / death cross
    if crossed_above(t["ma50"], t["ma200"], y["ma50"], y["ma200"]):
        alerts.append(f"GOLDEN CROSS {ticker}: 50-day avg moved above 200-day avg | {info}")
    if crossed_below(t["ma50"], t["ma200"], y["ma50"], y["ma200"]):
        alerts.append(f"DEATH CROSS  {ticker}: 50-day avg moved below 200-day avg | {info}")

    # 4. RSI
    if crossed_below(t["rsi"], RSI_LOW, y["rsi"], RSI_LOW):
        alerts.append(f"RSI OVERSOLD   {ticker} RSI fell to {t['rsi']:.0f} (below {RSI_LOW}) at ${close:,.2f} | {info}")
    if crossed_above(t["rsi"], RSI_HIGH, y["rsi"], RSI_HIGH):
        alerts.append(f"RSI OVERBOUGHT {ticker} RSI rose to {t['rsi']:.0f} (above {RSI_HIGH}) at ${close:,.2f} | {info}")

    # 5. 52-week high / low
    if crossed_above(close, t["hi52"], y["Close"], y["hi52"]):
        alerts.append(f"52-WEEK HIGH {ticker} closed at ${close:,.2f}, a new 1-year high | {info}")
    if crossed_below(close, t["lo52"], y["Close"], y["lo52"]):
        alerts.append(f"52-WEEK LOW  {ticker} closed at ${close:,.2f}, a new 1-year low | {info}")

    # 6. Institutional buying footprint (index tickers are handled in market_check)
    #    Reclaims are skipped because CROSS UP above already covers them.
    if ticker not in inst.INDEXES:
        alerts += inst.scan_ticker(raw, ticker, spy, include_reclaims=False)

    return alerts


def market_check():
    """Follow-through day signals plus background context for SPY and QQQ."""
    signals, context, spy = [], [], None
    for idx in inst.INDEXES:
        try:
            raw = download(idx)[["Open", "High", "Low", "Close", "Volume"]].dropna()
            if idx == "SPY":
                spy = raw
            s, ctx = inst.scan_index(raw, idx, include_reclaims=False)
            signals += s
            context += ctx
        except Exception as e:
            context.append(f"{idx}: market check error {e}")
    return signals, context, spy


def send_email(subject, body):
    sender = os.environ["GMAIL_USER"].strip()
    password = os.environ["GMAIL_APP_PASSWORD"].replace(" ", "").strip()
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = os.environ["ALERT_TO"].strip()
    msg.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(sender, password)
        s.send_message(msg)


if __name__ == "__main__":
    # Two "open" runs are scheduled (summer and winter times); only the one that
    # lands just after 9:30 New York time actually scans.
    if os.environ.get("RUN_SLOT") == "open" and not 0 <= minutes_since_open() <= OPEN_RUN_WINDOW_MIN:
        print(f"Skipping open run: {minutes_since_open():.0f} min from the 9:30 ET open.")
        sys.exit(0)

    label = run_label()
    all_alerts, market_context, spy = market_check()
    for tk in WATCHLIST:
        try:
            all_alerts += check(tk, spy)
        except Exception as e:
            all_alerts.append(f"{tk}: error {e}")

    print("\n".join(all_alerts) or "No signals today.")
    print("\n".join(market_context))

    # Market context rides along with real signals but never triggers an email by itself
    backdrop = ("\n\nMarket backdrop:\n" + "\n".join(market_context)) if market_context else ""
    note = ""
    if label in ("market open", "midday"):
        note = ("\n\nNote: scanned during market hours. Prices are live and volume is projected "
                "to a full day, so signals can change by the close.")
    footer = "\n\n---\nEducational alerts only, not financial advice."
    if all_alerts:
        send_email(f"Stock scanner ({label}): {len(all_alerts)} signal(s)",
                   "\n\n".join(all_alerts) + backdrop + note + footer)
    elif SEND_IF_NOTHING:
        send_email(f"Stock scanner ({label}): no signals", "Nothing triggered." + footer)
