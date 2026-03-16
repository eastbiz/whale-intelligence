name: Whale Intelligence Scanner

on:
  schedule:
    # All times in UTC. Currently DST = ET is UTC-4, PT is UTC-7
    # 9:45 AM ET = 13:45 UTC (DST) — after open settles
    - cron: '45 13 * * 1-5'
    # 12:30 PM ET = 16:30 UTC (DST) — midday flow
    - cron: '30 16 * * 1-5'
    # 2:45 PM ET = 18:45 UTC (DST) — 75 min before close, time to act
    - cron: '45 18 * * 1-5'
  workflow_dispatch:

jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Python 3.11
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: pip install requests

      - name: Run scanner
        env:
          UW_API_KEY:          ${{ secrets.UW_API_KEY }}
          TELEGRAM_BOT_TOKEN:  ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID:    ${{ secrets.TELEGRAM_CHAT_ID }}
          ANTHROPIC_API_KEY:   ${{ secrets.ANTHROPIC_API_KEY }}
          IBKR_FLEX_TOKEN:     ${{ secrets.IBKR_FLEX_TOKEN }}
          IBKR_FLEX_QUERY_ID:  ${{ secrets.IBKR_FLEX_QUERY_ID }}
        run: python whale_scanner.py
