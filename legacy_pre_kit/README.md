# legacy_pre_kit

Early versions of the bot, written **before** the hackathon kit was available.
They assume a different game (multi-way tables, chip-add raise amounts, their own
state dict), so they do not run in the kit as they are and are NOT entries.
Kept for reference: the equity engine, opponent model and the test simulator
(`sim.py`) are the parts being ported to the kit's `decide(game_state)` format.

- `poker_bot_v1/`: first full version
- `poker_bot_v2/`: adds crash/timeout safety and pot control

Run either one from inside its folder: `python3 poker_bot.py` (self-test) or
`python3 sim.py tourney --opp tag+nit+maniac+callstation+random --n 20 --budget 0.005`.
