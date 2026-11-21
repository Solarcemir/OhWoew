# ohwow

Heads-up No-Limit Hold'em bot for the **IEEE uOttawa × LARUS Poker Bot Hackathon** (team `ohwow`).

The bot is purely algorithmic: no LLM calls, no network, no threads. It combines precomputed strategy tables,
Monte Carlo simulation and live opponent modeling to pick the most profitable action each hand,
and adapts to whoever it is playing instead of following a fixed script.

- Entry point: [`my_bots/ohwow/bot.py`](my_bots/ohwow/bot.py) (`decide(game_state)`, Python 3.10)
- Kit rules and prize list: [`docs/KIT_README.md`](docs/KIT_README.md)

## Video walkthrough

<video controls width="100%">
  <source src="docs/ohwow_explained.mp4" type="video/mp4">
</video>

English narration and subtitles: [`ohwow_explained.srt`](docs/ohwow_explained.srt).

## How it decides

| # | Component | What it does |
|---|-----------|--------------|
| 1 | **Precomputed hand-strength tables** | Before the tournament we simulate thousands of starting-hand matchups and store the results in a 169×169 matrix. The bot looks up how strong its cards are against many possible opponent hands without recalculating during the match. |
| 2 | **Short-stack strategy** | At 13 big blinds or fewer it switches to push-or-fold: generally all-in or fold. Precomputed Nash equilibrium tables say which hands to play at each stack size, adjusted for how likely the opponent is to call. |
| 3 | **Monte Carlo simulations** | After the flop it runs up to 3,000 simulations of opponent cards and future community cards to estimate equity (its probability of winning), even though it cannot see the opponent's hand or the next cards. |
| 4 | **Opponent hand estimation** | Opponents are not assumed to hold random cards. Possible hands are weighted by the opponent's actions: aggressive betting makes stronger hands more likely, tempered by how often that opponent bluffs. |
| 5 | **Expected-value optimization** | Each action and bet size is scored by its average winnings if the opponent folds or calls. The highest-EV option wins, so the bot bluffs players who fold too often and bets bigger for value against players who call too often. |
| 6 | **Opponent modeling** | Throughout the match it tracks how often each opponent opens, raises, goes all-in, folds, calls and bluffs, and updates those statistics as observations accumulate. |
| 7 | **Bet-pattern detection** | It records bet sizes together with the cards shown at showdown. If an opponent consistently uses certain sizes with strong hands or with bluffs, the bot learns the pattern and exploits it. |
| 8 | **Defending against aggression** | Against opponents that re-raise (3-bet) most of our opens, it treats their re-raise range as wide and calls or re-raises instead of folding. Postflop raises are assumed honest by default, but against opponents that raise our bets often they are read as including bluffs. |
| 9 | **Additional features** | A trap mode adjusts small bets against opponents that attack them, 7-2 is played aggressively for the 7-2 side prize, and error handling falls back to a valid action if anything fails. Median decision time is about 15 ms. |

## Overall

Precomputed strategies give fast, solid baseline play. Probability-based simulation turns that into
accurate equity estimates postflop. Opponent learning then shifts every decision toward whatever is
most profitable against the player across the table.

## Entry

- **Team name:** `ohwow`. **Entry folder:** [`my_bots/ohwow/`](my_bots/ohwow/), with `bot.py` and `data/`.
- `data/` (about 340 KB) holds the precomputed tables and is read only at import time, from the folder next to `bot.py` or from `BOT_DATA_DIR`:
  - `eqmat.npy`: the 169×169 preflop equity matrix.
  - `pushfold.json`: heads-up Nash push/fold strategies by stack depth, with and without antes.
  - `classes.json`: the names of the 169 starting-hand classes.
- `pokercore/` is the kit's code, unmodified.

## Validation and environment

The bot is built for the tournament environment and stays inside its rules:

- **Python 3.10.** Imports: the standard library (`json`, `os`, `random`, `time`, `collections`), `eval7` and `numpy`. No other libraries.
- **Rules:** no network, no file writes, no subprocesses, no threads, and no `eval`, `exec` or `pickle`.
- **Time:** in the kit's Docker sandbox (half a CPU core, 768 MB, no network, read-only filesystem), a decision takes about 15 ms on median and under 200 ms at worst, against the 2-second limit.
- **Errors:** every decision is wrapped so that any error falls back to a legal action (check, or fold when facing a bet).

From the repo root, with the kit's setup (`make install` in a Python 3.10 environment):

```
make validate BOT=my_bots/ohwow            # the organisers' checks: passes
python3 play.py my_bots/ohwow bots/caller   # a local match
make sandbox                                # build the tournament sandbox image (once)
python3 play.py my_bots/ohwow bots/caller --docker   # the same match inside the sandbox
```

`make validate BOT=my_bots/ohwow` reports `PASSED — my_bots/ohwow`, and the bot plays full matches in the
Docker sandbox with no timeouts or errors.
