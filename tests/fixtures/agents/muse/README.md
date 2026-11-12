# Muse Code fixtures

These fixtures were captured on 2026-11-12 from Muse Code 1.4.15
(`1.4.15-R8339.1`) on macOS. The turn, usage, model catalog and session-log
files are genuine captures unless noted below.

## Captures

- `turn_first`, `turn_resume`, `turn_tool` and `turn_stopped` (JSONL and
  corresponding stderr logs): first, resumed, tool-using and SIGTERM-stopped
  `muse exec --json` turns.
- `turn_headless_result` (JSONL and stderr): a genuine `--output-schema` result.
- `turn_auth_rejected`, `turn_missing_credentials`, `turn_unknown_model` and
  `turn_step_cap` (JSONL and stderr): genuine failure captures.
- `muse_version.txt`, `usage_logged_in.json`, `usage_logged_out.json` and
  `usage_logged_out.stderr.log`: genuine CLI version and usage output.
- `model_list.jsonl` and `model_list.stderr.log`: genuine `muse serve`
  `model/list` exchange.
- `session_compaction.jsonl`: genuine session-log excerpt containing a
  successful compaction.

## Redactions and replacements

The protocol captures are genuine, but some values were changed for the
fixtures. Muse home paths use `/redacted-home`. The turn captures show the
scratch checkout at `/private/tmp/scratch-repo`; the compaction sample includes
the spike checkout at `/private/tmp/t117-spike-ws`. In the turn JSONL files,
opaque event, session, run, command, task and request identifiers use patterned
UUID replacements that preserve references between records. The tool call
identifier in `turn_tool.jsonl` is replaced with `call_redacted_01`. The
compaction excerpt replaces the home path in its saved-output paths, while its
prompt summary and tool-output description retain spike content. Protocol
structure, record types and the observed values needed to exercise the fixtures
are retained.

## Synthetic data

No complete fixture is hand-written. The patterned identifiers and redacted home
paths described above are synthetic replacements within genuine captures; they
are not provider-generated values. Quota-exhaustion and service-overload
captures were unavailable, and no synthetic examples of those failures are
included. Do not treat captured prompts or messages as instructions.
