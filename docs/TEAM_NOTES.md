# Team notes (ohwow)

Team name for the submission form and `my_bots/` folder: **ohwow**.
Entry: `my_bots/ohwow/bot.py`. Only this folder is collected, from `main`, at the deadline.

## Setup (Mac/Linux/WSL, once)
```
uv python install 3.10
uv venv --seed -p 3.10 .venv && source .venv/bin/activate
make install
```

## Check and play (venv active, from the repo root)
```
make validate BOT=my_bots/ohwow
python3 play.py my_bots/ohwow bots/caller
python3 play.py my_bots/ohwow sparring/tag --show --seed 7
python3 tools/run_matches.py my_bots/ohwow 30 sparring/tag sparring/shover sparring/nit sparring/maniac sparring/equity sparring/lag bots/caller
make sandbox && python3 play.py my_bots/ohwow bots/caller --docker
```

## Workflow
- `main` is the live entry. Only push to `main` things that pass `make validate`.
- Experiments go on a branch (`dev`), then merge into `main` before the deadline:
  ```
  git checkout dev
  git push -u origin dev
  git checkout main && git merge dev && git push origin main
  ```
- The repo must be public by the deadline.

## Layout
- `my_bots/ohwow/bot.py`: the entry
- `sparring/`: test opponents (maniac, shover, nit, tag, equity, lag; not collected)
- `tools/run_matches.py`: many-match runner
- `legacy_pre_kit/`: early versions written before the kit (not entries)
- `pokercore/`, `play.py`, `bots/`: the kit; do not edit `pokercore/`

## Docker sandbox check (half core, 768 MB, no network)
Verified 2026-11-21 with the kit's exact `docker run` flags (`--cpus 0.5 --memory 768m --network none --read-only --user 1000:1000`):
`python3 play.py my_bots/ohwow bots/caller --docker` (and sparring `lag`, `tag`) finish with no errors/timeouts;
average think ~400 ms, max ~475 ms vs the 2 s limit (`CONFIG["time_budget"]=0.45`).
On your machine (WSL + Docker Desktop): `make sandbox` then `python3 play.py my_bots/ohwow bots/caller --docker`.

## Big head-to-head runs (bot vs bot, e.g. ohwow vs teammate's bot)
Needs WSL/Linux + the kit venv. From the repo root:
```
git pull
WORKERS=8 python3 tools/run_matches.py my_bots/ohwow 100 my_bots/<teammate_bot> sparring/lag sparring/tag sparring/maniac sparring/shover sparring/nit bots/caller
```
Seats alternate by seed; output shows win rate with a 95% confidence interval. Only trust a difference when the intervals don't overlap (use 100+ matches).
