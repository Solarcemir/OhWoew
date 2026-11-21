# Poker Bot v1 (No-Limit Texas Hold'em, pure Python)

A purely algorithmic bot for a bot-vs-bot hackathon tournament. Python standard
library only: **no dependencies, no network calls, no LLM calls.**

The bot's brain is one function, `decide(state)`, in `poker_bot.py`. `sim.py` is a
self-contained poker engine for testing the bot before you have the hackathon repo.

```
poker_bot_v1/
├── poker_bot.py   the bot: decide(), equity, opponent model
├── sim.py         test harness: poker engine + opponent bots + cash/tournament modes
└── README.md      this file
```

---

## 1. Quick start

Requires Python 3.8 or newer. Run everything from inside this folder:

```
cd poker_bot_v1
python3 poker_bot.py
```

Expected: `evaluator + preflop table OK`, a few demo lines, and `fuzz OK: 300 random states`.
This is the bot's self-test.

Benchmark against every built-in opponent type (heads-up, about 70 seconds):

```
python3 sim.py cash --opp all --hands 500 --budget 0.005
```

Tournament, 6 players, bot against a mixed field (about 40 seconds):

```
python3 sim.py tourney --opp tag+nit+maniac+callstation+random --n 100 --budget 0.005
```

One 4-handed cash table:

```
python3 sim.py cash --opp tag+maniac+callstation --hands 400 --budget 0.005
```

Options for `sim.py`:

| Flag | Meaning |
|---|---|
| `cash` / `tourney` | game mode (first argument) |
| `--opp` | `all`, a comma list (`nit,tag` runs separate heads-up matches), or `a+b+c` (one table) |
| `--hands` | cash hands per matchup (default 300) |
| `--n` | number of tournaments (default 20) |
| `--budget` | seconds the bot may spend on equity simulation per decision (default 0.01 in the simulator) |
| `--stack` | cash stack in big blinds (default 100) |
| `--seed` | random seed |

Opponent names: `callstation`, `nit`, `maniac`, `allin`, `random`, `tag`, `weaktight`.

---

## 2. Plugging into the hackathon repo (the part to do at the event)

Two stub functions at the bottom of `poker_bot.py` must be filled in, because the
repo's game-state and action types are unknown until you see it:

```python
def normalize(raw):          # repo state object  ->  plain dict (section 4)
def to_repo_action(kind, amount):   # ('raise', 120)  ->  repo's action object
```

Then the repo's bot class calls:

```python
from poker_bot import decide_for_repo, MODEL

class MyBot(RepoBotBase):            # whatever the repo calls its base class
    def decide(self, raw_state):     # whatever the repo calls the hook
        return decide_for_repo(raw_state)
```

**Feed the opponent model** (this is where most of the edge comes from). Call these
from whatever callbacks or action history the repo exposes:

```python
MODEL.me = "<our player id>"
MODEL.observe_hand_start(list_of_player_ids_in_hand)
MODEL.observe_action(player, street, kind, to_call, pot, amount, bb)
MODEL.observe_showdown(player, hole_cards, board)
```

- `street`: 0 preflop, 1 flop, 2 turn, 3 river.
- `kind`: `'fold'`, `'check'`, `'call'`, `'raise'`.
- `to_call`: chips that player had to call when acting. `pot`: pot before their action
  (including the bet they faced). `amount`: chips they added.

If the repo does not expose opponents' actions, the bot still plays (bet-size reading
and neutral priors) but cannot exploit anyone.

**Checklist for the first 15 minutes at the event:**

1. Poker variant: standard 52-card No-Limit Hold'em? If not, see "Limitations".
2. Per-move time limit? Is it per move or per match? Lower `time_budget` (section 7).
3. Is "raise amount" "raise **to**" or "raise **by**"? The bot returns total chips added this action.
4. Which opponent bots ship in the repo? Add them to `OPPONENTS` in `sim.py` and re-run the benchmarks.
5. Can you see opponents' actions and stacks, the button position, showdown cards?
6. Are third-party packages allowed? (The bot needs none.)

---

## 3. Features and how each is coded

Everything below lives in `poker_bot.py`. Section headers in the file match this order.

### 3.1 Cards and hand evaluation

- **`parse_card`** accepts `'As'`, `'10h'`, `'A♠'`, `(14,'s')` and returns `(rank_int, suit)`, with ace = 14.
- **`evaluate(cards)`** takes 5 to 7 cards and returns a tuple `(category, tiebreakers...)`.
  Category runs 0 (high card) to 8 (straight flush), so Python's tuple comparison picks the
  winner. It counts ranks and suits, finds the best flush (including a straight flush by
  checking straights inside the flush suit) and handles the A-2-3-4-5 straight.

### 3.2 Hand-strength estimates

- **`PREFLOP_PCT`** (built by `_build_preflop_table`): all 169 starting-hand classes scored
  with the Chen formula, sorted weakest to strongest, weighted by number of combinations (6
  for pairs, 4 suited, 12 offsuit). Each class maps to a percentile: 1.0 is AA, near 0 is 72o.
  `preflop_pct(hole)` looks it up.
- **`_made_pct(hand, board)`**: rough percentile of a made hand on a board. Pairs are graded
  by whether the pair is ours, and whether it is top, second or lower. Higher categories
  map to fixed values. Flush draws get a floor of 0.6, because betting ranges contain draws.

### 3.3 Equity by Monte Carlo (`equity`)

- Removes every visible card from the deck, then repeatedly deals the missing board cards and
  opponent hands, and counts wins (ties count half).
- Runs until it hits `budget` seconds or `max_iter` (3000), with at least 60 iterations.
- **Range-narrowed opponents:** each opponent hand is drawn from the deck and accepted with
  probability `_accept(pct, tight)`, where `pct` is its strength and `tight` (0 to 0.9) says
  how narrow their range is. Up to 12 tries per opponent. If none are accepted, the strongest
  one tried is kept. With `tight = 0` this is plain random-hand equity.

### 3.4 Reading opponents' bets (range narrowing)

Computed at the top of `decide`:

1. **`_tightness`**: heuristic. Preflop it scales with the raise size in big blinds; postflop
   with bet size relative to the pot. Loose-aggressive opponents' bets count for less;
   tight-passive opponents' for more.
2. **Showdown evidence**: `MODEL.learned_tight` measures how strong this opponent's bets
   actually were when cards were revealed, and blends that over the heuristic (weight grows
   with samples, up to 0.85).
3. **Earlier streets**: `MODEL.hand_tight` remembers how much each player has narrowed their
   range this hand, so flop and turn bets still count on the river. This is how it deals with
   hidden hands: every action adds information about the range.
4. **Multiway**: tightness is multiplied by 0.7 with more than one opponent.

### 3.5 Opponent model (`Profile`, `OpponentModel`, global `MODEL`)

Per opponent id, plus a table-wide fallback when ids are unknown:

| Stat | How it is kept |
|---|---|
| Fold-to-bet rate | `folded / faced`, smoothed with 8 pseudo-observations at 40% |
| Aggression | bets / (bets + calls), prior 45% |
| VPIP (how many hands they play) | preflop calls/raises, prior 35% |
| **Response to our bets** | `vs_me[(street, raise_depth, size_bucket)] = [folds, times]` |
| **What their bets mean** | `shown[size_bucket] = [sum of hand strength, count]` from showdowns |

The `vs_me` table is the pattern detector. A bot that always folds to river bets is
recognised after a few samples, because the per-situation estimate uses a small prior
(`K_CTX = 3`). Estimates shrink bucket -> street+depth -> general fold rate (`ctx_fold`), so a
pattern seen at one size carries over to similar sizes.

- **Size buckets** (`_bucket`): small (under 0.4 pot), medium, large (0.8 pot or more).
- **Raise depth**: folding to an open says nothing about folding to a 5-bet, so statistics are
  never pooled across depths. (This fixed a real bug found in simulation: the bot shoved
  trash into a nit's 4-bets.)

### 3.6 Betting logic inside `decide`

The order of checks:

1. **Short stack push/fold** (stack of 10 big blinds or less): shove or call if equity beats a
   threshold, otherwise fold or check.
2. **Preflop, unopened or limped**: open-raise when hand percentile clears a threshold. The
   threshold moves with the opponents' fold rate, our aggression level, number of opponents,
   and seat. Occasional "steal" raises with hands that would otherwise not open. Open size is
   3 big blinds, or 2.3 when the stack is 30 big blinds or less.
3. **Checked to us**: compares checking against bets of 1/3, 2/3 and full pot (plus a shove
   when shallow). For each size, EV = chance everyone folds x pot + chance of a call x
   (our equity when called x final pot minus our bet). Fold chance comes from what we
   learned about this opponent. The best size is bet with a probability that rises with its
   EV gain.
4. **Facing a bet**: compares calling against raising to two sizes (0.7x and 1.2x), valued
   the same way, and otherwise calls or folds by pot odds plus a margin (see 3.8).

### 3.7 Bluffing and unpredictability

- **Bluff frequency is capped** (about 40% to 60%, scaled by our aggression) unless the
  opponent has been measured to fold reliably in this exact spot (8 or more samples and a
  fold rate of 0.8 or more); then bluffs go to about 95%.
- **Slow-play**: with a near-certain win (equity over 0.85, heads-up, before the river) it
  checks 20% of the time.
- **River bluffs** with very weak hands at a low frequency, so value bets still get paid.
- **Decision noise**: Gaussian noise (sd 0.02) added to equity.
- **Size jitter**: bet sizes vary 0.9x to 1.15x.
- **Style drift** (`STYLE["agg"]`): our overall aggression level is re-rolled between 1.05 and
  1.30 every 25 hands (`observe_hand_start`), so an adaptive opponent cannot lock onto one
  profile. It feeds `k`, which scales thresholds throughout `decide`.

### 3.8 Pot odds and calling discipline

`pot_odds = to_call / (pot + to_call)`; the bot calls when `equity > pot_odds + margin`.
The margin starts at 0.03 and adjusts:

- Tight-passive bettor: +0.04 (respect their bets).
- Loose-aggressive bettor: -0.02 (call down lighter).
- In position postflop: lower. Out of position: higher.
- Deep stacks and a big share of the stack at risk: up to +0.08.

### 3.9 Position (seat)

`state["position"]` runs from 0.0 (first to act after the flop) to 1.0 (button). Internally
`ip = 2 * position - 1`. Late seats: lower open threshold, more steals, higher bet/bluff
probability, lighter calls. Early seats: the opposite. Default 0.5 (neutral) if the repo does
not provide it.

### 3.10 Stack depth (`eff`, `spr`, `chips`)

- `eff`: the most chips that can change hands, `min(our stack, biggest opponent stack in the hand)`.
  `spr = eff / pot` (stack-to-pot ratio).
- **Commitment rounding** in `chips()`: a bet that covers `eff`, or leaves less than 30% of the
  resulting pot behind, becomes an all-in.
- **Shallow (spr 1.5 or less)**: with equity over 0.5, shoves 65% of the time.
- **Shove as a sizing option** when the stack is between 1x and 2.5x the pot.
- **Deep (spr over 4)**: huge bluffs (50% or more of effective stack) are suppressed to 15%
  unless the opponent reliably folds.

### 3.11 Adapter stubs

`normalize`, `to_repo_action`, `decide_for_repo` at the bottom of the file (section 2).

### 3.12 The simulator (`sim.py`)

- **Engine** (`Hand`): blinds, betting rounds, min-raise rules, all-ins and side pots, showdown.
  Asserts that chips are conserved after every hand.
- **Opponent archetypes**: callstation (never folds), nit (only strong hands), maniac (raises
  80%), allin (shoves every hand), random, tag (tight-aggressive thresholds), weaktight
  (small bets, folds to big ones). Each is a function `f(state) -> (kind, amount)` using the
  same state dict as the bot.
- **Cash mode**: stacks reset every hand. Heads-up it plays each deal twice with seats
  swapped ("duplicate"), which removes most card luck. Reports bb/100 with a 95% margin and
  flags results that are not statistically significant.
- **Tournament mode**: escalating blinds every 15 hands, reports win rate and average finish
  against the chance baseline.
- It feeds `MODEL.observe_*` the same way the real event must, and passes `position`,
  `effective_stack`, `raises` and `pf_aggressor` in the state.

---

## 4. The state dict `decide()` expects

| Key | Required | Meaning |
|---|---|---|
| `hole` | yes | our 2 cards |
| `board` | yes | 0 to 5 community cards |
| `pot` | yes | total pot, **including** the bet we are facing |
| `to_call` | yes | chips we must add to call (0 = we can check) |
| `stack` | yes | our remaining chips |
| `big_blind` | yes | big blind size |
| `opponents` | yes | number of opponents still in the hand |
| `min_raise` | no | minimum chips we must add to make a legal raise |
| `opponent_ids` | no | ids of opponents in the hand (enables per-player reads) |
| `pf_aggressor` | no | True if we raised preflop (enables continuation bets) |
| `position` | no | 0.0 worst seat to 1.0 button (default 0.5) |
| `effective_stack` | no | see 3.10 (default = our stack) |
| `raises` | no | raises so far this street; estimated from `to_call` if absent |
| `time_budget` | no | seconds for equity simulation (default 0.15) |

Returns `(kind, amount)`: `kind` is `'fold'`, `'check'`, `'call'` or `'raise'`. `amount` is
the **total chips we add** this action (call part included), and only matters for `'raise'`.
An amount equal to our stack means all-in.

---

## 5. Tuning knobs (where to change things)

| What | Where | Effect |
|---|---|---|
| Time per decision | `state["time_budget"]`, default `0.15` in `decide`; `budget` default in `equity` | accuracy vs. speed |
| Aggression range | `_reroll_style`: `random.uniform(1.05, 1.30)` | higher = more raises and bluffs |
| Bluff cap | `0.35 + 0.15 * k` and `0.30 + 0.15 * k` in `decide` | max bluff frequency vs. unknown opponents |
| "Reliable folder" rule | `nc >= 8 and pf >= 0.8` in `decide` | when bluffs go to about 95% |
| Opening range | `th = ...` in the unopened-preflop block | wider or tighter opens |
| Short-stack threshold | `stack <= 10 * bb` | push/fold zone |
| Opponent-stat priors | `Profile.PRIOR` (8), `Profile.K_CTX` (3) | how fast reads form |
| Range narrowing strength | `_accept`, `_tightness` | how much bets shrink the opponent's range |

---

## 6. What the tests showed (honest summary)

All numbers are against **opponents I invented**, not the real field.

- Clear edge over callstation, maniac, all-in and random bots (hundreds of bb/100).
- Small edge over the nit and weak-tight bots.
- **About break-even against the tight-aggressive threshold bot.** That is the likely weak
  spot against decent real bots.
- 6-player tournaments: about 50% wins against a mixed field and about 33% against a tougher
  field (chance would be 17%).
- The position and stack-depth additions showed no measurable change in those tests.
  The simulations were too small to detect an effect either way. They are kept because they
  follow standard poker theory.
- Bug found by simulation and fixed: shoving trash into a nit's 4-bets (fold statistics are
  now tracked per raise depth).

---

## 7. Limitations and TODO

- **No hard time-limit protection.** The equity loop stops at its budget, but a slow evaluation
  could overshoot. No fast fallback exists yet if time is nearly gone.
- **Assumes standard 52-card No-Limit Hold'em.** A different variant (limit betting, short
  deck, wild cards) means changing `FULL_DECK`, `evaluate`, and the sizing in `decide`.
- **No tournament-specific play** (payout structure, protecting a stack near the money).
- **Equity is simulated, not exact**; preflop equity is not precomputed.
- **`_made_pct` is a rough heuristic**, not an exact range model.
- **Thresholds are untuned guesses.** The most valuable next step is simulating against the
  repo's actual bots and tuning the knobs in section 5.
- Opponent reads only work if `MODEL.observe_*` is fed (section 2).
