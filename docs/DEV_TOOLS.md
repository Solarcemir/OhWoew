# dev-tools branch

Development and testing material for the `ohwow` entry. The entry itself lives on `main` in `my_bots/ohwow/`.

- `sparring/our_*`: every version of our bot we built and tested. The entry on `main` is `our_v17`.
- `sparring/naive`, `montecarlo`, `researched`, `exploiter`, `meta`, `gto`: practice opponents in common styles.
- `tools/arena.py`: duplicate round-robin arena in the tournament format.
  - `--paired`: candidates share one random stream, for A/B tests.
  - `--docker`: runs every bot in the sandbox.
  - `--json`: writes the results for `tourney_mc.py`.
- `tools/analyze.py`: chip flow by street and ending from `play.py --log` files.
- `tools/build_tables.py`: builds `data/` (preflop equity matrix and Nash push/fold tables).
- `tools/tournament.py`, `tools/tourney_mc.py`: Monte Carlo of the event format (one duplicate pair per pairing, top 4, knockout).
- `tools/prizes.py`: the measured side prizes, from logs.
- `tools/play_web.py` + `tools/web/index.html`: play heads-up against the bot in your browser
  (`python tools/play_web.py`, or `play_vs_ohwow.bat` on Windows). It uses the kit's engine and table in the
  tournament format, and the browser never receives the bot's hole cards before a showdown.
