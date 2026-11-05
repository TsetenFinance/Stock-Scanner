"""
Institutional buying alerts - add-on for your stock scanner.

Hedge fund trades are not public in real time, so this watches the footprints
big buyers leave in price and volume:

  FOLLOW-THRU    Market index (SPY/QQQ) rally day 4-10 after a low, up >=1.25%
                 on higher volume than the day before. The classic "institutions
                 are back" signal for the overall market.
  ACCUMULATION   Stock up strongly on >=1.5x average volume and closing near the
                 day's high (buyers kept paying up into the close).
  RECLAIM        Price closes back above its 50- or 200-day average on heavy
                 volume after being below it.
  ACC/DIST       25-day count of accumulation vs distribution days for the
                 index; a rising net number means big money is buying again.

Usage:
  pip install yfinance pandas
  python inst_buying_alerts.py                    # default watchlist
  python inst_buying_alerts.py NVDA TSLA GOOGL    # your own tickers

scanner.py imports this file, so these alerts arrive in the same daily email.
It calls scan_ticker(df, ticker, spy_df) with a DataFrame that has Open, High,
Low, Close, Volume columns (oldest row first), and scan_index() for SPY/QQQ.
"""

import sys
import pandas as pd

INDEXES = ["SPY", "QQQ"]
DEFAULT_WATCHLIST = ["NVDA", "TSLA", "GOOGL", "CRWD", "PANW", "WCBR"]

# --- thresholds (tune these) ---------------------------------------------
VOL_AVG_DAYS = 50
ACC_MIN_GAIN = 0.02          # stock must be up >= 2% on the day
ACC_MIN_VOL = 1.5            # volume >= 1.5x 50-day average
ACC_MIN_CLOSE_LOC = 0.70     # close in top 30% of the day's range
FTD_MIN_GAIN = 0.0125        # index up >= 1.25%
FTD_DAY_MIN, FTD_DAY_MAX = 4, 10
FTD_LOOKBACK = 30            # look for the market low within the last 30 days
RECLAIM_MIN_VOL = 1.3
AD_WINDOW = 25
AD_MIN_MOVE = 0.002          # index day counts if it moves >= 0.2%


def _prep(df):
    df = df.copy()
    df["pct"] = df["Close"].pct_change()
    df["vol_avg"] = df["Volume"].rolling(VOL_AVG_DAYS).mean().shift(1)
    df["vol_x"] = df["Volume"] / df["vol_avg"]
    rng = (df["High"] - df["Low"]).replace(0, float("nan"))
    df["close_loc"] = (df["Close"] - df["Low"]) / rng
    df["ma50"] = df["Close"].rolling(50).mean()
    df["ma200"] = df["Close"].rolling(200).mean()
    return df


def _date(df, i=-1):
    d = df.index[i]
    return d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)


def follow_through_day(df):
    """Return (is_ftd_today, rally_day_number). df must be prepped."""
    recent = df.iloc[-FTD_LOOKBACK:]
    low_pos = recent["Low"].values.argmin()
    low_val = recent["Low"].iloc[low_pos]
    after = recent.iloc[low_pos + 1:]
    if after.empty or (after["Low"] < low_val).any():
        return False, 0
    # Day 1 of the rally attempt = first up close after the low
    up = after.index[after["pct"] > 0]
    if len(up) == 0:
        return False, 0
    start = recent.index.get_loc(up[0])
    day_num = len(recent) - start          # today is day N of the attempt
    today, prev = df.iloc[-1], df.iloc[-2]
    is_ftd = (FTD_DAY_MIN <= day_num <= FTD_DAY_MAX
              and today["pct"] >= FTD_MIN_GAIN
              and today["Volume"] > prev["Volume"])
    return is_ftd, day_num


def acc_dist_count(df):
    w = df.iloc[-AD_WINDOW:]
    higher_vol = w["Volume"] > df["Volume"].shift(1).loc[w.index]
    acc = int(((w["pct"] >= AD_MIN_MOVE) & higher_vol).sum())
    dist = int(((w["pct"] <= -AD_MIN_MOVE) & higher_vol).sum())
    return acc, dist


def scan_index(raw, ticker, include_reclaims=True):
    """Return (signals, context). Signals are real alerts; context lines
    (rally-attempt day, acc/dist count) are background info that should not
    trigger an email on their own."""
    df = _prep(raw)
    t = df.iloc[-1]
    signals, context = [], []
    tail = f"vol {t.vol_x:.1f}x avg | {_date(df)}"
    is_ftd, day = follow_through_day(df)
    if is_ftd:
        signals.append(f"FOLLOW-THRU  {ticker} ${t.Close:.2f} up {t.pct:+.1%} on rally day {day}, "
                       f"volume above yesterday - institutions may be buying again | {tail}")
    elif day:
        context.append(f"RALLY TRY    {ticker} day {day} of rally attempt "
                       f"(follow-through possible days {FTD_DAY_MIN}-{FTD_DAY_MAX}) | {tail}")
    acc, dist = acc_dist_count(df)
    context.append(f"ACC/DIST     {ticker} last {AD_WINDOW} days: {acc} accumulation vs "
                   f"{dist} distribution (net {acc - dist:+d}) | {_date(df)}")
    if include_reclaims:
        signals += _reclaims(df, ticker, tail)
    return signals, context


def _reclaims(df, ticker, tail):
    out = []
    t, y = df.iloc[-1], df.iloc[-2]
    for col, name in (("ma50", "50-day"), ("ma200", "200-day")):
        if pd.notna(t[col]) and y.Close < y[col] and t.Close > t[col] and t.vol_x >= RECLAIM_MIN_VOL:
            out.append(f"RECLAIM      {ticker} ${t.Close:.2f} back above {name} avg ${t[col]:.2f} | {tail}")
    return out


def scan_ticker(raw, ticker, spy_raw=None, include_reclaims=True):
    df = _prep(raw)
    t = df.iloc[-1]
    rs = ""
    if spy_raw is not None and len(spy_raw) > 5 and len(df) > 5:
        stock5 = df["Close"].iloc[-1] / df["Close"].iloc[-6] - 1
        spy5 = spy_raw["Close"].iloc[-1] / spy_raw["Close"].iloc[-6] - 1
        rs = f" | 5d vs SPY {stock5 - spy5:+.1%}"
    tail = f"vol {t.vol_x:.1f}x avg{rs} | {_date(df)}"
    alerts = []
    if t.pct >= ACC_MIN_GAIN and t.vol_x >= ACC_MIN_VOL and t.close_loc >= ACC_MIN_CLOSE_LOC:
        alerts.append(f"ACCUMULATION {ticker} ${t.Close:.2f} up {t.pct:+.1%}, closed in top "
                      f"{(1 - t.close_loc):.0%} of day's range | {tail}")
    if include_reclaims:
        alerts += _reclaims(df, ticker, tail)
    return alerts


def _download(ticker):
    import yfinance as yf
    df = yf.download(ticker, period="1y", interval="1d", auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.dropna()


def main(tickers):
    spy = None
    for idx in INDEXES:
        d = _download(idx)
        if idx == "SPY":
            spy = d
        signals, context = scan_index(d, idx)
        for a in signals + context:
            print(a)
    for tk in tickers:
        for a in scan_ticker(_download(tk), tk, spy):
            print(a)


if __name__ == "__main__":
    main(sys.argv[1:] or DEFAULT_WATCHLIST)
