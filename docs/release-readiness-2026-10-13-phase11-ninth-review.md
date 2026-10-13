# Phase 11 ninth-review verification

Status: Task 174 verified Tasks 167–173 on 2026-10-13 against the final source, revised the HTML reference, the design documents and AGENTS.md, and marked the spec delivered. The spec is [`tui-feedback-ninth-review.md`](tui-feedback-ninth-review.md); the preceding record is the [eighth review](release-readiness-2026-10-13-phase11-eighth-review.md). GNU/Linux remains unverified; everything ran on macOS (Darwin 25.3.0, Python 3.14.7, Textual 6.3.0).

Source: `7ad9373` (167), `d4b2af2` (168), `dc4b8b8` (169), `178b066` (170), `a8f9fb7`, `292c436` (171), `795d5a6` (172), `f8e940f`, `1cdd5e3`, `ba1a84b` (173), then Task 174's `e41976e` (HTML reference) and `20b19ff` (design documents and AGENTS.md), with this record, the README and spec links and the plan and todo ticks in the last commit.

## Request-by-request result

| Request | Commits | Result | How verified |
|---|---|---|---|
| `Tasks` heading, bold `SHIKIRU` | `7ad9373` | pass | Native Home before and after, colour and `NO_COLOR`. |
| Plugin reinstall hint | `d4b2af2` | pass | The real loaders called for a missing widget and agent plugin. |
| Stable, hardened tests | `dc4b8b8`, `178b066`, `a8f9fb7` | pass with limits | See test stability; limits (a) to (c). |
| Command history: arrows | `795d5a6` | pass | Full popup at four sizes, colour and `NO_COLOR`. |
| Command history: colour, no `returned` | `f8e940f`, `1cdd5e3`, `ba1a84b` | pass | Full popup and the palette's stored-command text. |
| AGENTS.md anti-pattern lines | `20b19ff` | pass with limit (a) | Three sentences appended to "Test value". |

## Native verification

A throwaway harness patched `DashboardApp.run_async` to `run_test` and then called `bootstrap.run_dashboard` itself, so the real wiring ran (real `DashboardServices`, samplers, `SystemClock`, `StateReader`, the operator queries) over throwaway git projects. `TMUX_TMPDIR` pointed at an empty scratch directory. The baseline was a `git worktree` of `0b42f70` put first on `sys.path`; the harness asserted the module path of each run. Every cell of the compositor output was read with its bold flag and foreground; the monochrome runs set `NO_COLOR=1 TERM=dumb`. No fake `docker` was needed.

1. **Titles.** Before: the header brand `SHIKIRU` was regular `#83bddd`, the queue heading `TASKS` and the filtered count `TASKS · Waiting · 1 of 1`. After: `SHIKIRU` is bold `#83bddd` (bold `#b3b3b3` under `NO_COLOR`), the heading is `Tasks` and the filtered count `Tasks · waiting · 1 of 1`, in both palettes.
2. **Reinstall hint.** Before: `no dashboard widget plugin named 'nope-widget' is registered or installed`. After: the same sentence for the widget and for `agent plugin 'nope-agent'`, each followed by `; a typo in the name gives this error too; if the name is right, reinstall from the checkout: uv tool install . --reinstall (editable: uv tool install --editable . --reinstall)`. The operator's uv tool was not reinstalled.
3. **Command history popup.** Twenty-nine stored commands: a Pending (`Stop`), an Applied (`Say`), a Rejected with an `error` and a Rejected without one, above 25 older Applied ones. Opened from Views in the mounted app at 120×40, 44×30, 30×30 and 80×8 (wide, compact, narrower than the 32-column title limit and tiny).
   - Before: plain rows with `; returned {...}`, the hint `p previous  n next`, Right left the page at 1.
   - After, colour: `#29` grey `#9aa5b5`, ` · ` grey, the status word bold (Pending `#dfb16c`, Applied `#90cba6`, Rejected `#e3a0a0`), the action white `#dce2e9`, the target bold white, the detail grey. The Rejected row with an `error` shows that text (`task T-404 does not exist`), the one without shows `engine rejected the command`; Pending shows `queued 10/13 19:18Z; engine currently stopped; original acknowledgement wait context unknown`. No `returned` anywhere. The page buttons read `← newer` and `→ older` with cyan `#83bddd` arrows (dimmed `#507186` while disabled); at 30 columns they are the bare arrows. Right from the initial focus (the filter) goes to `page 2 · rows 21-29 of 29`, Left returns, `p` and `n` change nothing, and with the filter expanded Right and Left leave the page at 1 at every size.
   - After, `NO_COLOR`: the same layout with luminance greys; the status words stay bold (`#b6b6b6`, `#bcbcbc`, `#aeaeae`), the id grey `#a4a4a4`, the target bold, the arrows `#b3b3b3`.
   - Palette stored commands (driven through `PaletteScreen._show_stored_command` on the mounted screen, not through a submitted form): applied reads `guidance queued for the selected role's next turn`; rejected reads `task T-404 does not exist` and `engine rejected the command`. Before, both appended `Returned result · {...}` or printed the raw JSON.
4. **Test stability.** From the commits and their plan notes, nothing was rerun. See below.

## Test stability evidence

| Task | Change | Evidence recorded by that task |
|---|---|---|
| 169 | `UI_WAIT_SECONDS = 30` replaces 8 module constants and 335 literal 2 s waits | 30 rounds of 10 parallel copies of three dashboard files: 0 failures in 900 runs. The baseline of 5 rounds also showed 0, so the 2 s flake only appeared under the full gate's load. |
| 170 | Named offenders fixed; `wait_until` treats a Textual `NoMatches` from its predicate as not yet | Under 8 `yes` hogs at `-n 10` the baseline failed 1 of 5 rounds (`test_palette_export_controls_and_full_result_remain_reachable`); the final tree passed 20 of 20 rounds. |
| 171 | CPU duty test measures 1.0 s and finds its burner by command line; the take-over test waits for the agent REPL | The CPU baseline did not reproduce (15 parallel copies under 8 hogs passed); the take-over race was seen once, not reproduced. The operator cut the verification to 3 baseline and 4 after rounds of 5 copies under 8 hogs: 0 failures. |

Task 174's own gates ran on the default ten workers with no retry, rerun plugin, skip or sleep added. Both were green on the first run.

## HTML reference

`docs/design/references/focus-steel-approved.html` SHA-256 `5267e34b164fe14ae54f86f1dfbaff3f775808d855db2f2f1d1e62abc3ac81d6`. Task 174 decoded the entity-encoded `srcdoc`, edited the app and re-encoded it with an exact round-trip check (re-encoding the unedited text reproduced the original bytes; the bytes outside the `srcdoc` are unchanged). Changes: `.shk-brand` weight 700; the heading `Tasks`; the history footer buttons `← newer` and `→ older` with the arrows in the `shk-key` cyan (dimmed while disabled; only the arrows in the compact and tiny widths); the keydown handler pages with ArrowLeft and ArrowRight whenever the Select is not open (before the editing check, so the focused Select still pages) and no longer reacts to `p` and `n`; the help line `History: ← newer · → older`; history rows redrawn as `#44 · Rejected · Answer · T-47 · task T-404 does not exist` with a grey id and separators, a bold status coloured like the Home summary, a white action, a bold target and a grey detail, with no raw result.

The Chrome check loaded the decoded app plus a measuring script in headless Google Chrome under a throwaway profile (deleted; the operator's Chrome was not touched). For wide, compact and tiny, colour and No color, it read computed colours and weights of the brand, the heading, a Rejected and an Applied row, the footer buttons, and the paging by ArrowRight, ArrowLeft, `p` and `n`:

- Brand bold 700 `rgb(131,189,221)`; heading `Tasks`; row spans match the native colours (`#9aa5b5`, `#e3a0a0` bold, `#90cba6` bold, `#dce2e9`, target bold); no `returned` in the popup.
- Footer `← newer` / `→ older` with cyan arrows; the words hidden at compact and tiny; the previous button disabled on page 1.
- ArrowRight goes to `Page 2 of 2`, ArrowLeft back, `p` and `n` do nothing.

## Design and operator documents

06 §5.5 states the arrow paging (active while the filter is collapsed, inert while it is expanded) and the row colours; the workspace specification says the same; the parity table now reads `Tasks`. 06 §6.1 already carried the title rule from Task 167, and the Focus specification and 06 already dropped the raw result in Task 173. The design README marks the ninth review delivered. AGENTS.md "Test value" gained three sentences.

## Numbers

| Measure | At `0b42f70` | Now |
|---|---|---|
| Collected cases | 1,605 | 1,607 |
| Test functions (`tests/`) | 950 | 951 |
| Lines in `tests/` | 60,423 | 60,569 |
| Lines in `tests/integration` | 47,849 | 47,983 |
| Lines in `tests/unit` | 4,305 | 4,305 |

The net two added cases come from the header brand table (colour and `NO_COLOR`, Task 167); the history arrow, colour and plugin-hint work changed existing tests in place.

| Run | Result |
|---|---|
| `uv run poe check` (default workers) | green: 1,586 passed, 14 skipped, 91.20 % coverage, 299 s |
| `TERM=dumb NO_COLOR=1 uv run poe check` | green: 1,586 passed, 14 skipped, 91.03 % coverage, 298 s |

## Remaining limits

- (a) The pause-then-assert sweep was descoped by the operator: 70 `pilot.pause()`-then-assert sites remain, most deliberate negative checks. AGENTS.md therefore words the last sentence for new tests and does not claim the suite is clean.
- (b) Task 171 verification was reduced (the baseline did not reproduce; four after rounds). The take-over fix is a process-readiness check, not a proven fix of the observed timeout.
- (c) The gate can still show occasional single flakes under extreme load; two of Task 174's runs did not.
- (d) Evidence export: `_reveal_result` leaves the form scrolled one row short because the reveal runs before the result layout settles. Found in Task 170, a real minor product defect, not fixed.
- (e) A Textual `NoMatches` inside a worker was seen twice in `test_home_attention_tracks_owned_held_and_engine_state_changes`; not reproduced. Probably a refresh worker querying during teardown; the product cause is unverified.
- (f) The command history page label is not recomputed when an open popup is resized (pre-existing).
- (g) The palette's stored-command text was driven through `_show_stored_command` on a mounted palette, not through a submitted-then-completed form.
- (h) The HTML reference's No color palette draws all statuses in one ink (bold keeps them apart), where the native `NO_COLOR` render uses three luminance greys; its history rows are an illustrative sample (no Pending row until Preview update), and Chrome could not demonstrate the open-Select inert case (`:open`), which the native run covered.
- (i) The two Haiku trailers from the seventh review (`6fd13e4`, `ca48b1a`) stay; the history was not rewritten.
- (j) `git stash@{0}` ("sweep") holds the abandoned sweep edits; the operator may drop it (the classifier denied the drop).

## Hygiene

The `0b42f70` worktree, the Chrome profile, the throwaway projects, the harness and its renders were deleted. No process, tmux socket or Chrome instance of mine remains. The operator's live session, `test-project`, default tmux server and Chrome were not touched.
