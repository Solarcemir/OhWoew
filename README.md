# ohwow

Heads-up No-Limit Hold'em bot for the **IEEE uOttawa × LARUS Poker Bot Hackathon** (team `ohwow`).

The bot is purely algorithmic: no LLM calls, no network, no threads. It combines precomputed strategy tables,
Monte Carlo simulation and live opponent modeling to pick the most profitable action each hand,
and adapts to whoever it is playing instead of following a fixed script.

- Entry point: [`my_bots/ohwow/bot.py`](my_bots/ohwow/bot.py) (`decide(game_state)`, Python 3.10)
- Kit rules and prize list: [`docs/KIT_README.md`](docs/KIT_README.md)
- Team notes and exact commands: [`docs/TEAM_NOTES.md`](docs/TEAM_NOTES.md)

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
| 8 | **Additional features** | A trap mode adjusts small bets against aggressive opponents, a special 7-2 strategy covers the side challenge, and error handling falls back to a valid action if anything fails. Median decision time is about 15 ms. |

## Overall

Precomputed strategies give fast, solid baseline play. Probability-based simulation turns that into
accurate equity estimates postflop. Opponent learning then shifts every decision toward whatever is
most profitable against the player across the table.

## Running it

From the repo root, with the kit's tooling:

```
make validate BOT=my_bots/ohwow
```

The move limit is 2 seconds per decision; the bot uses a small fraction of that.
