# ohwow: heads-up No-Limit Hold'em bot

Team **ohwow** · IEEE uOttawa × LARUS Poker Bot Hackathon (Nov 21)
Entry: [`my_bots/ohwow/bot.py`](my_bots/ohwow/bot.py) (one file, Python 3.10, standard library + `eval7`).
The kit's original README, rules and prize list are in [`docs/KIT_README.md`](docs/KIT_README.md).
Team notes and exact commands: [`docs/TEAM_NOTES.md`](docs/TEAM_NOTES.md).

The bot is purely algorithmic: no network, no LLM calls, no file writes, no threads.
It passes `make validate BOT=my_bots/ohwow` and uses about 0.1 to 0.4 s of the 2 s move limit.

## How it plays

Every decision runs the same loop: read the opponent, estimate equity, pick the action
with the best expected value, add controlled randomness.

1. **Equity by Monte Carlo** (`equity`). Deals the missing cards many times with `eval7`
   and counts wins. The opponent's hand is drawn from a range narrowed by how they have bet,
   not from random hands.
2. **Reading bets** (`_tightness`). Bigger bets mean stronger ranges, but less so against
   loose-aggressive opponents. What opponents actually showed down is blended in, and earlier
   streets of the same hand still count on the river.
3. **Opponent model** (`OpponentModel`). Built from the kit's `match_action_log` every hand:
   how often they fold, raise, and play hands; how they react to *our* bets by street,
   raise depth and bet size (this finds patterns such as "always folds to river bets");
   and what their bets turned out to be at showdown.
4. **Betting by expected value.** Checked to us: compares checking with 1/3, 2/3 and pot bets
   (and a shove when stacks are shallow), counting the chance the opponent folds as learned for
   that exact spot. Facing a bet: compares calling with raising, using pot odds plus a margin.
5. **Heads-up preflop.** The button opens most hands, the big blind defends wide. At 12 big
   blinds or less it uses a shove-or-fold chart: it shoves a larger share of hands the shorter
   the stack gets.
6. **Exploits.** Against an opponent that shoves almost every hand it folds until it holds a
   premium hand and then calls. Against very loose-aggressive opponents it calls down lighter.
   Bluffs are capped unless it has measured that the opponent reliably folds.
7. **Unpredictability.** Mixed frequencies, bet sizes jittered by about 10%, small noise on
   its estimates, occasional slow-plays, and an overall aggression level that drifts every
   25 hands so an adaptive opponent cannot lock onto it.
8. **Stack depth, position, pot control.** Bets that leave too little behind become shoves,
   huge bluffs are suppressed on deep stacks, medium-strong hands keep the pot small, and the
   button bets and bluffs more than the big blind.
9. **Safety.** The bot cannot crash or return an illegal action. On any error it falls back
   to a fast hand-strength decision, and if even that fails it never folds for free.

## Rules compliance

| Kit rule | How the bot follows it |
|---|---|
| 2 s per decision | equity loop stops at 0.45 s (`CONFIG["time_budget"]`) |
| Half a CPU core, 768 MB | single-threaded, small memory footprint |
| Allowed libraries only | standard library and `eval7` (falls back to pure Python without it) |
| No file writes, network, processes, threads | none used; the validator passes |
| `random` is seeded by the runner | the bot only uses the module-level `random` |
| One entry at `my_bots/<team>/` | `my_bots/ohwow/bot.py` |

## Results so far (local, against my own sparring bots)

Full freezeout matches played with the kit's `play.py`, seats alternating. Small samples:
treat every number as about ±15 percentage points.

| Opponent (`sparring/`) | Matches won |
|---|---|
| calling station (`bots/caller`) | 27 / 30 |
| tight-aggressive threshold bot | 22 / 30 |
| nit | 7 / 8 |
| maniac (raises 80%) | 26 / 30 |
| equity-threshold bot | 9 / 12 |
| loose-aggressive bot | 8 / 14 |
| all-in every hand | 20 / 30 (was 17 / 30 before the shover exploit) |
| previous version on `main` | 11 / 16 for the current `dev` bot |

Known weak spots: heavy aggression from loose-aggressive bots in big pots, and variance
when it value-bets medium hands into calling stations.

## Run it (Mac/Linux/WSL, venv with Python 3.10 active)

```
make install
make validate BOT=my_bots/ohwow
python3 play.py my_bots/ohwow bots/caller
python3 play.py my_bots/ohwow sparring/tag --show --seed 7
python3 tools/run_matches.py my_bots/ohwow 30 sparring/tag sparring/shover sparring/nit sparring/maniac sparring/equity sparring/lag bots/caller
make sandbox && python3 play.py my_bots/ohwow bots/caller --docker
```

## Repo layout

```
my_bots/ohwow/bot.py     the entry (only this folder is collected, from main)
sparring/                test opponents: maniac, shover, nit, tag, equity, lag
tools/run_matches.py     plays many matches through play.py
legacy_pre_kit/          early versions written before the kit (not entries)
docs/                    team notes and the kit's original README
pokercore/, play.py, bots/, Makefile    the kit (pokercore/ is never edited)
```

## Branches

`main` is the submitted entry and must always pass `make validate`. Work happens on `dev`
and is merged into `main` before the deadline.
