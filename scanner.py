"""Daily stock scanner: emails you when a watchlist stock triggers a signal.

Signals (each fires only on the day it happens):
  - Breakout / breakdown of 20-day resistance / support (needs above-average volume)
  - 50-day and 200-day moving average crosses
  - Golden cross / death cross (50-day vs 200-day average)
  - RSI drops below 30 (oversold) or rises above 70 (overbought)
  - New 52-week high / low
Educational tool only, not financial advice.
"""
import os
import smtplib
from email.message import EmailMessage

import yfinance as yf

# ---------- Settings you can change ----------
WATCHLIST = ["AAPL", "MSFT", "NVDA", "AMZN", "TSLA", "META", "SPY"]
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


def check(ticker):
    df = yf.download(ticker, period="2y", auto_adjust=True, progress=False)
    if df.empty or len(df) < 260:
        return [f"{ticker}: not enough data"]
    if hasattr(df.columns, "levels"):
        df.columns = df.columns.get_level_values(0)

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

    return alerts


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
    all_alerts = []
    for tk in WATCHLIST:
        try:
            all_alerts += check(tk)
        except Exception as e:
            all_alerts.append(f"{tk}: error {e}")

    print("\n".join(all_alerts) or "No signals today.")

    footer = "\n\n---\nEducational alerts only, not financial advice."
    if all_alerts:
        send_email(f"Stock scanner: {len(all_alerts)} signal(s)", "\n\n".join(all_alerts) + footer)
    elif SEND_IF_NOTHING:
        send_email("Stock scanner: no signals today", "Nothing triggered today." + footer)
