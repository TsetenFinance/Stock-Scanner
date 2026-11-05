# Stock-Scanner

Runs every weekday after the US market closes (GitHub Actions, `.github/workflows/scan.yml`)
and emails any signals for the watchlist in `scanner.py`.

## Files
- `scanner.py` - main scanner: breakouts/breakdowns, moving-average crosses, RSI, 52-week highs/lows.
- `inst_buying_alerts.py` - signs that big institutions are buying again:
  - **FOLLOW-THRU** - SPY/QQQ up 1.25%+ on higher volume, 4-10 days after a market low.
  - **ACCUMULATION** - a stock up 2%+ on 1.5x average volume, closing near the day's high.
  - **Market backdrop** - rally-attempt day count and 25-day accumulation vs distribution days
    for SPY/QQQ. Included in the email, but never triggers an email on its own.

Run manually: GitHub -> Actions -> Stock scanner -> Run workflow.

Educational tool only, not financial advice.
