import os
import smtplib
from email.message import EmailMessage

import yfinance as yf

WATCHLIST = ["AAPL", "MSFT", "NVDA", "AMZN", "TSLA", "META", "SPY"]
LOOKBACK = 20          # days for support / resistance
SEND_IF_NOTHING = False  # set True to get a daily 'no signals' email


def check(ticker):
    df = yf.download(ticker, period="1y", auto_adjust=True, progress=False)
    if df.empty or len(df) < 202:
        return [f"{ticker}: not enough data"]
    df.columns = df.columns.get_level_values(0)

    df["res"] = df["Close"].rolling(LOOKBACK).max().shift(1)
    df["sup"] = df["Close"].rolling(LOOKBACK).min().shift(1)
    df["vol_avg"] = df["Volume"].rolling(LOOKBACK).mean().shift(1)
    df["ma50"] = df["Close"].rolling(50).mean()
    df["ma200"] = df["Close"].rolling(200).mean()

    t, y = df.iloc[-1], df.iloc[-2]          # today, yesterday
    close = float(t["Close"])
    vol_x = float(t["Volume"] / t["vol_avg"]) if t["vol_avg"] else 0
    trend = "uptrend" if close > t["ma50"] else "downtrend"
    date = df.index[-1].date()
    alerts = []

    if close > t["res"] and y["Close"] <= y["res"]:
        alerts.append(f"BREAKOUT  {ticker} closed ${close:,.2f} above resistance ${t['res']:,.2f} "
                      f"| volume {vol_x:.1f}x avg | {trend} | {date}")
    if close < t["sup"] and y["Close"] >= y["sup"]:
        alerts.append(f"BREAKDOWN {ticker} closed ${close:,.2f} below support ${t['sup']:,.2f} "
                      f"| volume {vol_x:.1f}x avg | {trend} | {date}")
    for ma in ("ma50", "ma200"):
        days = ma[2:]
        if close > t[ma] and y["Close"] <= y[ma]:
            alerts.append(f"CROSS UP  {ticker} closed ${close:,.2f} above its {days}-day average ${t[ma]:,.2f} | {date}")
        if close < t[ma] and y["Close"] >= y[ma]:
            alerts.append(f"CROSS DOWN {ticker} closed ${close:,.2f} below its {days}-day average ${t[ma]:,.2f} | {date}")
    return alerts


def send_email(subject, body):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ["GMAIL_USER"]
    msg["To"] = os.environ["ALERT_TO"]
    msg.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(os.environ["GMAIL_USER"], os.environ["GMAIL_APP_PASSWORD"])
        s.send_message(msg)


if __name__ == "__main__":
    all_alerts = []
    for tk in WATCHLIST:
        try:
            all_alerts += check(tk)
        except Exception as e:
            all_alerts.append(f"{tk}: error {e}")

    print("\n".join(all_alerts) or "No signals today.")

    if all_alerts:
        send_email(f"Stock scanner: {len(all_alerts)} signal(s)", "\n\n".join(all_alerts))
    elif SEND_IF_NOTHING:
        send_email("Stock scanner: no signals today", "Nothing crossed today.")
