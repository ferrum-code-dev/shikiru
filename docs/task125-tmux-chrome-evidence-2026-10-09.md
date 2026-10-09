# Task 125 tmux chrome evidence

Root accepted Task 125 on 2026-10-09. The final source/test gate passed with 1,291 tests passed, 14 skipped, and 90.31% coverage in 539.74 seconds. The complete log is `/private/tmp/shikiru-task125-correction-gate-final2-20261009.log` (SHA-256 `4c946f08a425d2f7054e2b58269fcd0361054d899d2dd7dd5e8d882e0215eb67`). Its command was:

```sh
env PYTHONPATH=/Users/pavel/Projects/ferrum-code-dev/shikiru/src:/Users/pavel/Projects/ferrum-code-dev/shikiru/tests/fake_agent/src VIRTUAL_ENV=/Users/pavel/Projects/ferrum-code-dev/shikiru/.venv UV_CACHE_DIR=/private/tmp/shikiru-task125-correction-gate-final2-20261009 PYTEST_ADDOPTS='-n 2 --dist=loadfile' uv run --no-project --active --no-sync poe --executor uv --executor-opt no-sync=true check
```

This documentation/tracking finalization does not change the gated implementation or test files. Their SHA-256 values at the final gate were:

| File | SHA-256 |
|---|---|
| `src/shikiru/config/defaults.toml` | `7dcaac3bdb0979737a0f8c046dad3009308d33313399cbd5b2e971237d1018a0` |
| `src/shikiru/engine/cosmetic_decorator.py` | `8d4aa495ba23918ff19145ef5d2c554f12d793bba3a1614b3a084493d99396c5` |
| `src/shikiru/engine/titles.py` | `21c1ac309b93f6d88b25142780bfca49e0c2fe27eb8cb7e66219228e9ae840bf` |
| `src/shikiru/tmux/client.py` | `5685e21b032e7c173de52b5fb8833055a12c7e637e9bae5c23991367accbedf5` |
| `src/shikiru/tmux/decorator.py` | `d75366eb9bc143720bdc3ba3b0aa0a3ebe3ed0bc5367f5de340ce29fc3dd4834` |
| `src/shikiru/tmux/session.py` | `60ecfd29dae275195c9e3bc11dc429caccb05b9a768b1e62e06705160285c63a` |
| `tests/integration/test_tmux_decorator.py` | `f20a0103b0ce469caea23583988fe7e22f70464a3482d30dd550e7f412a0ba4a` |

The attached native proof is a disposable private-tmux fixture rendered through a real attached client. It calls the actual `PaneDecorator` and `SessionView` with supplied title fields and segments; it does not claim a connected live engine/`TaskTitles` walkthrough, which remains for Task 128. The capture verifies the complete dark status and pane-border rows including blank fill, inherited-green isolation and recovery, indices `0:dashboard`, `1:Task125-style`, `2:Task126-custom`, changing seconds/date without hostname, and per-label SGR: Home inactive `bold=false`, task current `bold=true`, task inactive `bold=false`. The five supplied pane components have distinct role, agent/model, iteration, context and cost colors; seeded paused/operator/escalated state colors remain truthful.

The fixture checks that plain custom `pane_title` values round-trip unchanged and that custom rendered literals containing `#{session_name}`, `#[bg=green]`, and escaped `##{session_name}` remain text. It also creates an operator window with different lifetime, rename and pane-border options and verifies recovery leaves those options intact while restoring owned-window chrome. Existing task windows are recognized by their Shikiru `@shk_state_color` metadata, not their names. The attached client retains focus; global styles and a second session retain inherited green.

Proof script: `/private/tmp/shikiru-task125-final-correction-native-20261009.py` (SHA-256 `8c7980c7c3278031adeec007203ba960478fb9dccb1bb95a56b59916d9fc3850`). Attached transcript: `/private/tmp/shikiru-task125-final-correction-native-20261009/attached.typescript` (SHA-256 `396238e7712461d28f0330fb03a211c76c85f5443df91115c388742f08871e9a`). Parsed facts: `/private/tmp/shikiru-task125-final-correction-native-20261009/attached-facts.json` (SHA-256 `604aa088f5804d7535973c92ad4d9e86cc06da4300dd620606aaca92bd12d968`). The complete gate and proof manifest is `/private/tmp/shikiru-task125-correction-provenance-final-20261009.json` (SHA-256 `794574bdfb8d81baa2a35df597aea251a2d0b924c9610685b97348617578e374`); it retains superseded manifests and failed attempts. Earlier all-bold readings were caused by the temporary reader mishandling empty `ESC[m` resets. The corrected attached parse establishes the label flags above; the earlier reading was not evidence of a product bold leak.

The approved HTML reference remains unchanged at SHA-256 `56cecc218414f86956a1b7c21e4549b0a6d09db1e5dac3287803917dc40d29bd4`. The temporary fake-agent symlink was absent before and after the gate; owned UV caches and private tmux fixtures were removed. The default tmux server, unrelated sockets, protected host processes and existing user project were left untouched.
