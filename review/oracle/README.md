# Oracle: hanab.live's own reducer

`oracle.mjs` replays an export through hanab.live's game package (`packages/game`, GPL-3.0) and prints
the public state before every turn: counters, pace, stacks, discard piles, hands in slot order, the clue
log (touched **and** missed cards) and the log lines. `review/server/bundle.py` compares our engine
with it position by position.

```bash
bash review/setup.sh    # once: fetch hanabi-live@c1d970b (sparse) and bundle packages/game with esbuild
node review/oracle/oracle.mjs prototype/examples/export_78921.json
```

- **Built from source, not npm.** `@hanabi-live/game` 0.0.8 on npm is older than the repo (8.6k diff
  lines, including log wording), so `review/setup.sh` builds the commit `docs/representation.md` §4 cites.
- **Adapted from** `packages/client/test/loadGameJSON.ts`: options come from the export, player names
  are real, no fake "gameOver" is appended, and a snapshot is taken after every action.
- **Per-turn state only.** The reducer does not decide when a game ends (hanab.live's server does), so
  end conditions are not checked here.
- **Log wording is not the site's.** new.playhanabi.com runs an older version; see
  `review/server/bundle.py` `site_log`.
