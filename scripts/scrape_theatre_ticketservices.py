name: Scrape theatre listings

on:
  schedule:
    # Theatre listings can change any day, same reasoning as music.
    - cron: "30 6 * * *"
  workflow_dispatch: {}

permissions:
  contents: write

concurrency:
  group: scrape-theatre
  cancel-in-progress: false

jobs:
  scrape:
    runs-on: ubuntu-latest
    steps:
      - name: Check out repo
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run more.com theatre scraper
        run: python scripts/scrape_theatre_more.py

      # NOTE: leave this step commented out until
      # scrape_theatre_ticketservices.py has been verified locally
      # against real markup (see its STATUS docstring) — don't let an
      # unverified scraper run unattended on a schedule.
      # - name: Run ticketservices.gr theatre scraper
      #   run: python scripts/scrape_theatre_ticketservices.py

      - name: Commit updated data
        run: |
          git config user.name "athens-tonight-bot"
          git config user.email "actions@github.com"
          git add data/theatre_more.json
          git diff --quiet --cached || git commit -m "Update theatre listings"
          git pull --rebase origin main
          git push
