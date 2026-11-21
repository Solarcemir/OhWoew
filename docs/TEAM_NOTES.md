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
python3 tools/run_matches.py my_bots/ohwow 30 sparring/tag sparring/shover sparring/nit sparring/maniac bots/caller
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
- `sparring/`: simple opponents for local testing (not collected)
- `tools/run_matches.py`: many-match runner
- `legacy_pre_kit/`: early versions written before the kit (not entries)
- `pokercore/`, `play.py`, `bots/`: the kit; do not edit `pokercore/`
