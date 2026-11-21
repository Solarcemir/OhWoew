# luis-bot branch

Luis's bot and testing tools, to compare against `ohwow`. Nothing here is the entry: only `my_bots/ohwow/` on `main` is collected.

## What's here
- `my_bots/luis_bot/`: the bot (`bot.py` plus `data/`, about 340 KB of precomputed tables).
- `sparring/`: 8 more test opponents.
  - `naive`, `montecarlo`, `researched`, `exploiter`, `meta`, `gto`: each written by an AI agent from a typical student prompt ("make me a poker bot", "use Monte Carlo and pot odds", "make it GTO", and so on). They stand in for what most teams will submit.
  - `our_solid` and `our_v1`: older versions of luis_bot.
- `tools/arena.py`: round-robin duplicate arena in the tournament format. Each pairing plays the same seed twice with the seats swapped; winning both matches is a pairing win. It prints W-D-L, match win % with a 95% interval, and standings.
- `tools/analyze.py`: reads `play.py --log` files and shows where a bot wins and loses chips (by street and ending), plus its worst hands with each decision's debug.
- `tools/build_tables.py`: builds `luis_bot/data/`.
  - `eqmat.npy`: 169x169 preflop equity matrix.
  - `pushfold.json`: heads-up Nash push/fold, solved by fictitious play, with and without antes.

## How luis_bot plays
1. **Preflop deep.**
   - SB opens about 80% of hands to 2.5bb, and 100% against opponents that fold a lot.
   - BB defends or 3-bets by equity against the opponent's measured opening range, not against a random hand.
2. **Short stack (13bb or less).** Push or fold from the Nash tables, adjusted by how often this opponent actually calls shoves. Shove calls use the opponent's learned shove frequency, so an always-shover gets called wide.
3. **Postflop.** Monte Carlo equity (about 3000 samples, 0.35 s cap) against a weighted range.
   - An opponent's bets down-weight its "nothing" hands, by its showdown bluff rate or its bet frequency, whichever is larger.
   - Its calls down-weight air.
4. **Bet sizing by EV.** Each choice among check, 1/3, 2/3, pot and 1.5x pot is scored as
   `EV = fold% * pot + (1 - fold%) * (equity_when_called * final_pot - bet)`.
   - `fold%` is learned per size bucket, and separately for second barrels (after the opponent already called), which fold much less.
   - The result: it bluffs players that fold, value-bets stations big, and doesn't bluff them.
5. **Reactive trap.** If the opponent raises our small bets 30% of the time or more, small bets switch to our strong hands.

## Running it (Windows, no WSL needed)
```
py -3.10 -m venv .venv
.venv\Scripts\python -m pip install eval7==0.1.11 -r requirements.txt   # 0.1.11 has a prebuilt Windows wheel
set BOT_ENV_ALLOW=SYSTEMROOT,WINDIR      # Windows only: the kit strips these and Python can't start without them
.venv\Scripts\python play.py my_bots/luis_bot my_bots/ohwow --seed 3
.venv\Scripts\python tools/arena.py my_bots/luis_bot my_bots/ohwow --pairs 40 --workers 10
```
- `tools/arena.py` sets `BOT_ENV_ALLOW` itself on Windows.
- Keep `--workers` to 10 or fewer with `ohwow` in the mix. Each `ohwow` match keeps a core busy (0.45 s budget per decision). Too many workers starve it, which weakens it, and 25 workers froze Luis's PC.
- The validator needs `PYTHONUTF8=1` on a Windows console.

On Mac, Linux or WSL, use the normal kit setup and drop the `BOT_ENV_ALLOW` line.

## Current luis_bot = v4
v4 is v1 plus three changes:
- **All-in counting.** It counts the hands in which the opponent goes all-in, and learns how often it shoves first and over our open. A habitual shover's all-ins are treated as a wide range.
- **More simulations.** Postflop Monte Carlo goes from 3,000 to 8,000 samples (0.35 s cap). In the Docker sandbox, decisions take 47 ms median and 192 ms at most, with 0 errors.
- **Wider raises over limps.** It raises opponents that limp often (capped range) more widely, and backs off if they limp-reraise.

The over-fold defence (v3, `USE_OVERFOLD`) is in the code but off: it did not help.

Same seeds (9090), same 10 opponents, 30 duplicate pairs each (600 matches per version):

| opponent | v1 | v4 |
|---|---|---|
| overall | 62% ±4% | **64% ±4%** |
| gto | 48% | 48% |
| f_shover | 57% | 73% |
| f_lag | 55% | 63% |
| supreme | 58% | 63% |
| montecarlo | 68% | 73% |
| exploiter | 57% | 53% |
| f_tag | 90% | 83% |

v4 beats v1 head to head 52% ±13%. `gto` (balanced, never adapts) is still about 50%, and most of those pairings end in draws.

## Big comparison of v1, v2 and ohwow (12 duplicate pairs per pairing, seed 4242, vs 12 rivals and each other)
`sparring/our_v2` adds two changes to v1: a bet-frequency weighting and learned shove ranges. They did not help.

| bot | match win % overall | vs ohwow |
|---|---|---|
| our_v1 (= luis_bot) | 68% ±5% | 62% ±19% |
| our_v2 | 64% ±5% | 54% ±20% |
| ohwow | 59% ±5% | - |

The hardest opponent is `sparring/gto`: v1 wins only 46% ±20% of matches against it.

## Test-only bots (never entries)
- `sparring/supreme`: knows the whole board before it is dealt, but not the opponent's cards. It rebuilds the deck from `BOT_MATCH_SEED`, which `tools/arena.py` sets.
  - It loses to v1 (v1 wins 72%) and to v2 (66%), because its betting is transparent.
  - It ties ohwow (50%), beats gto (gto wins 28%) and beats f_lag (f_lag wins 6%).
- `sparring/claude_live`: a seat played live by Claude through `tools/relay.py`. Start `relay.py <dir>`, run play.py with `--time-limit-ms 600000`, and answer each decision with `relay_ctl.py <dir> answer '{...}'`.
  - In 18 real hands, Claude was +2,404 against luis_bot.
  - Read: luis_bot leads most flops and over-bluffs a player that starts calling.

## Earlier result (luis_bot vs ohwow, 40 duplicate pairs, seed 777)
- ohwow 8 pairing wins, 28 draws, luis_bot 4. In matches, ohwow 44, luis_bot 36: ohwow leads, but the difference is within noise at 80 matches.
- The main leak found in luis_bot was folding the turn too often to ohwow's double barrels. Fixed since (the bet-frequency weighting), together with an always-shover leak. A re-test is running.
- In the Docker sandbox (half core, eval7 0.1.7), luis_bot takes about 11 ms median per decision and 97 ms at most, with 0 errors.
