#!/bin/bash

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UV_BIN="$(command -v uv)"
TMUX_BIN="$(command -v tmux)"
GIT_BIN="$(command -v git)"
PYTHON_BIN="$(command -v python3)"
DATE="$(date +%F)"
ARTIFACT_DIR="$ROOT/build/release-readiness"
ARTIFACT="$ARTIFACT_DIR/clean-install-$DATE.json"
LOG="$ARTIFACT_DIR/clean-install-$DATE.log"
RUN_ROOT="$(mktemp -d /private/tmp/shikiru-release-smoke.XXXXXX)"
HOME_DIR="$RUN_ROOT/home"
CACHE_DIR="$RUN_ROOT/cache"
TOOL_DIR="$RUN_ROOT/uv/tools"
TOOL_BIN_DIR="$RUN_ROOT/uv/bin"
TMUX_TMPDIR="$RUN_ROOT/tmux"
TMUX_SHIM_DIR="$RUN_ROOT/shims"
SOCKET_NAME="shikiru-release-$RANDOM-$$"
ATTACH_PID=""
SMOKE_STAGE="source-checkout installation"
HOME_DASHBOARD_CHECK="not run"
PICKER_CHECK="not run"
SMALL_CLIENT_SIZE="not run"

mkdir -p "$ARTIFACT_DIR" "$HOME_DIR" "$CACHE_DIR/xdg" "$CACHE_DIR/config" \
    "$CACHE_DIR/data" "$CACHE_DIR/state" "$CACHE_DIR/uv" "$TOOL_DIR" "$TOOL_BIN_DIR" \
    "$TMUX_TMPDIR" "$TMUX_SHIM_DIR" "$RUN_ROOT/tmp"
: > "$LOG"

PATH="$TOOL_BIN_DIR:$TMUX_SHIM_DIR:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
RUNTIME_ENV=(
    "HOME=$HOME_DIR"
    "XDG_CACHE_HOME=$CACHE_DIR/xdg"
    "XDG_CONFIG_HOME=$CACHE_DIR/config"
    "XDG_DATA_HOME=$CACHE_DIR/data"
    "XDG_STATE_HOME=$CACHE_DIR/state"
    "UV_CACHE_DIR=$CACHE_DIR/uv"
    "UV_TOOL_DIR=$TOOL_DIR"
    "UV_TOOL_BIN_DIR=$TOOL_BIN_DIR"
    "TMUX_TMPDIR=$TMUX_TMPDIR"
    "TMPDIR=$RUN_ROOT/tmp"
    "PATH=$PATH"
    "SHELL=/bin/sh"
    "TERM=xterm-256color"
    "NO_COLOR=1"
    "GIT_CONFIG_NOSYSTEM=1"
    "GIT_CONFIG_GLOBAL=/dev/null"
    "SHIKIRU_FAKE_TICK_A1=1"
)

record() {
    printf '\n$' >> "$LOG"
    printf ' %q' "$@" >> "$LOG"
    printf '\n' >> "$LOG"
    "$@" 2>&1 | tee -a "$LOG"
}

tmux_run() {
    env -i "${RUNTIME_ENV[@]}" "$TMUX_BIN" -L "$SOCKET_NAME" -f /dev/null "$@"
}

app() {
    env -i "${RUNTIME_ENV[@]}" "$TOOL_BIN" --project "$1" "${@:2}"
}

run_waiter() {
    env -i "${RUNTIME_ENV[@]}" "PYTHONPATH=$ROOT" "$PACKAGE_PYTHON" \
        -m tests.support.release_wait "$@"
}

wait_for_text() {
    run_waiter pane "$TMUX_BIN" "$SOCKET_NAME" "$1" "$2" >> "$LOG" 2>&1
}

wait_for_advance() {
    run_waiter advance "$TMUX_BIN" "$SOCKET_NAME" "$1" "$2" >> "$LOG" 2>&1
}

wait_for_task() {
    run_waiter task "$TOOL_BIN" "$WORK_PROJECT" "$STATUS_PATH" >> "$LOG" 2>&1
}

wait_for_client() {
    run_waiter client "$TMUX_BIN" "$SOCKET_NAME" "$1"
}

prepare_project() {
    local project="$1"
    mkdir -p "$project"
    cp -R "$ROOT/tests/fixtures/sample-project/." "$project/"
    "$GIT_BIN" -C "$project" init --quiet --initial-branch main
    "$GIT_BIN" -C "$project" config user.name 'Shikiru Release Smoke'
    "$GIT_BIN" -C "$project" config user.email release-smoke@example.invalid
    "$GIT_BIN" -C "$project" add .
    "$GIT_BIN" -C "$project" commit --quiet -m fixture
}

finish() {
    local exit_status=$?
    trap - EXIT
    printf '\nRelease smoke stage: %s\n' "$SMOKE_STAGE" >> "$LOG"
    if [[ -f "${STATUS_PATH:-}" ]]; then
        printf '\nLast observed task status:\n' >> "$LOG"
        cat "$STATUS_PATH" >> "$LOG"
    fi
    if [[ -x "${TOOL_BIN:-}" ]]; then
        for project in "${WORK_PROJECT:-}" "${SECONDARY_PROJECT:-}"; do
            if [[ -n "$project" && -f "$project/.shikiru/config.toml" ]]; then
                app "$project" stop --kill-session >> "$LOG" 2>&1 || true
            fi
        done
    fi
    env -i "${RUNTIME_ENV[@]}" "$TMUX_BIN" -L "$SOCKET_NAME" -f /dev/null \
        kill-server >> "$LOG" 2>&1 || true
    if [[ -n "$ATTACH_PID" ]]; then
        kill "$ATTACH_PID" 2>/dev/null || true
        wait "$ATTACH_PID" 2>/dev/null || true
    fi
    SOURCE_COMMIT="$("$GIT_BIN" -C "$ROOT" rev-parse HEAD)"
    SOURCE_FINGERPRINT="$(
        {
            "$GIT_BIN" -C "$ROOT" diff HEAD --binary
            shasum -a 256 "$ROOT/scripts/release-readiness-smoke.sh"
            shasum -a 256 "$ROOT/tests/support/release_wait.py"
            if [[ -f "$ROOT/docs/release-readiness-2026-09-29.md" ]]; then
                shasum -a 256 "$ROOT/docs/release-readiness-2026-09-29.md"
            fi
        } | shasum -a 256 | awk '{print $1}'
    )"
    SMOKE_EXIT_STATUS="$exit_status" "$PYTHON_BIN" - "$ARTIFACT" "$DATE" "$SOURCE_COMMIT" "$SOURCE_FINGERPRINT" "$SMOKE_STAGE" "$HOME_DASHBOARD_CHECK" "$PICKER_CHECK" "$SMALL_CLIENT_SIZE" <<'PY'
import json
import os
import sys
from pathlib import Path

artifact = Path(sys.argv[1])
data = {
    "date": sys.argv[2],
    "source_commit": sys.argv[3],
    "source_diff_fingerprint": sys.argv[4],
    "failed_stage": sys.argv[5] if os.environ["SMOKE_EXIT_STATUS"] != "0" else None,
    "status": "passed" if os.environ["SMOKE_EXIT_STATUS"] == "0" else "failed",
    "installation": "uv tool install . --with tests/fake_agent --with pytest",
    "fresh_disposable_home": True,
    "runtime_isolation": ["HOME", "XDG directories", "uv cache/tools/bin", "private tmux socket"],
    "installed_package_data_and_entrypoints": "verified" if os.environ["SMOKE_EXIT_STATUS"] == "0" else "incomplete",
    "installed_cli_flows": ["interactive init wizard and Doctor", "init --yes", "doctor", "start", "submit", "status", "ls", "picker attach", "named attach", "stop and cleanup"],
    "wizard_terminals": ["120x38", "68x16"],
    "picker_terminal": sys.argv[7],
    "small_picker_client_size": sys.argv[8],
    "home_dashboard_pane_after_merge": sys.argv[6],
    "task_result": "T-1 merged on main and A1 ticked from interactive quickstart" if os.environ["SMOKE_EXIT_STATUS"] == "0" else "incomplete",
    "agent_cost": "unknown; test-only fake agent reports no billed cost",
    "limitation": "The existing test-only fake-agent plugin and pytest (needed by its test-kit import) were injected only into this disposable tool environment for repeatable unauthenticated packaging and CLI smoke coverage; native E1-E6 evidence is separate.",
    "log": f"build/release-readiness/clean-install-{sys.argv[2]}.log",
}
artifact.write_text(json.dumps(data, indent=2) + "\n")
PY
    rm -rf "$RUN_ROOT"
    exit "$exit_status"
}

trap finish EXIT

if [[ -n "$(find "$HOME_DIR" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
    printf 'disposable HOME was not empty before installation\n' >&2
    exit 1
fi

cat > "$TMUX_SHIM_DIR/tmux" <<EOF
#!/bin/sh
exec "$TMUX_BIN" -L "$SOCKET_NAME" -f /dev/null "\$@"
EOF
chmod 755 "$TMUX_SHIM_DIR/tmux"

cd "$ROOT"
record env -i \
    "HOME=$HOME_DIR" \
    "XDG_CACHE_HOME=$CACHE_DIR/xdg" \
    "XDG_CONFIG_HOME=$CACHE_DIR/config" \
    "XDG_DATA_HOME=$CACHE_DIR/data" \
    "XDG_STATE_HOME=$CACHE_DIR/state" \
    "UV_CACHE_DIR=$CACHE_DIR/uv" \
    "UV_TOOL_DIR=$TOOL_DIR" \
    "UV_TOOL_BIN_DIR=$TOOL_BIN_DIR" \
    "PATH=$PATH" \
    "$UV_BIN" tool install . --with "$ROOT/tests/fake_agent" --with pytest

TOOL_BIN="$TOOL_BIN_DIR/shikiru"
PACKAGE_PYTHON="$(sed -n '1{s/^#!//;p;}' "$TOOL_BIN")"
[[ -x "$TOOL_BIN" && -x "$PACKAGE_PYTHON" ]]
record "$TOOL_BIN" --version
record env -i "HOME=$HOME_DIR" "PATH=$PATH" "UV_TOOL_DIR=$TOOL_DIR" "$PACKAGE_PYTHON" - <<'PY'
from importlib.metadata import entry_points
from importlib.resources import files
from pathlib import Path

import os
import shikiru

defaults = files("shikiru.config").joinpath("defaults.toml")
assert defaults.is_file()
assert "sample_seconds = 2" in defaults.read_text()
module_path = Path(shikiru.__file__).resolve()
assert module_path.is_relative_to(Path(os.environ["UV_TOOL_DIR"]))
assert not module_path.is_relative_to(Path.cwd())
agents = {entry.name for entry in entry_points(group="shikiru.agents")}
widgets = {entry.name for entry in entry_points(group="shikiru.widgets")}
assert "fake" in agents
assert {"header", "resources", "docker"} <= widgets
print("installed defaults.toml, fake agent entry point and built-in widget entry points verified")
PY

WIZARD_PROJECT="$RUN_ROOT/quickstart"
SMALL_PROJECT="$RUN_ROOT/wizard-small"
WORK_PROJECT="$WIZARD_PROJECT"
SECONDARY_PROJECT="$RUN_ROOT/yes-smoke"
SMOKE_STAGE="normal-size inline interactive wizard"
prepare_project "$WIZARD_PROJECT"
prepare_project "$SMALL_PROJECT"
prepare_project "$SECONDARY_PROJECT"

tmux_run new-session -d -s release-smoke -x 120 -y 38 -c "$RUN_ROOT" /bin/sh
tmux_run new-window -d -t release-smoke -n wizard -c "$WIZARD_PROJECT" \
    "$TOOL_BIN" --project "$WIZARD_PROJECT" init
wait_for_text release-smoke:wizard 'Step 1 of 7 · Repository'
tmux_run send-keys -t release-smoke:wizard Tab
wait_for_advance release-smoke:wizard 'Step 2 of 7 · Agents and ladders'
wait_for_text release-smoke:wizard 'Enter an explicit model supported by each selected plugin.'
tmux_run send-keys -t release-smoke:wizard BTab
tmux_run send-keys -t release-smoke:wizard BTab
tmux_run send-keys -t release-smoke:wizard C-a
tmux_run send-keys -t release-smoke:wizard C-k
tmux_run send-keys -l -t release-smoke:wizard 'fake:smoke'
wait_for_text release-smoke:wizard 'fake:smoke'
tmux_run send-keys -t release-smoke:wizard BTab
tmux_run send-keys -t release-smoke:wizard C-a
tmux_run send-keys -t release-smoke:wizard C-k
tmux_run send-keys -l -t release-smoke:wizard 'fake:strong'
wait_for_text release-smoke:wizard 'fake:strong'
tmux_run send-keys -t release-smoke:wizard BTab
tmux_run send-keys -t release-smoke:wizard C-a
tmux_run send-keys -t release-smoke:wizard C-k
tmux_run send-keys -l -t release-smoke:wizard 'fake:smoke'
wait_for_text release-smoke:wizard 'fake:smoke'
tmux_run capture-pane -p -t release-smoke:wizard -S - > "$RUN_ROOT/wizard-agents.txt"
tmux_run send-keys -t release-smoke:wizard Tab
tmux_run send-keys -t release-smoke:wizard Tab
tmux_run send-keys -t release-smoke:wizard Tab
tmux_run send-keys -t release-smoke:wizard Tab
wait_for_advance release-smoke:wizard 'Step 3 of 7 · Trackers'
for step in 'Step 4 of 7 · Caches and CoW' 'Step 5 of 7 · Parallelism' \
    'Step 6 of 7 · Integration mode' 'Step 7 of 7 · Finish'; do
    wait_for_advance release-smoke:wizard "$step"
done
tmux_run send-keys -t release-smoke:wizard Tab
tmux_run send-keys -t release-smoke:wizard Enter
wait_for_text release-smoke:wizard 'Doctor passed.'
tmux_run send-keys -t release-smoke:wizard Escape
wait_for_text release-smoke:wizard 'initialized '

WIZARD_CONFIG="$WIZARD_PROJECT/.shikiru/config.toml"
[[ -f "$WIZARD_CONFIG" ]]
grep -Fq 'model = "smoke"' "$WIZARD_CONFIG"
grep -Fq 'model = "strong"' "$WIZARD_CONFIG"
record "$PACKAGE_PYTHON" - "$WORK_PROJECT" <<'PY'
import sys
from pathlib import Path

from shikiru.config.loader import load_settings

settings = load_settings(Path(sys.argv[1]))
assert settings.roles.leader.ladder[0].model == "smoke"
assert settings.roles.implementer.ladder[0].model == "strong"
assert settings.roles.helper.ladder[0].model == "smoke"
PY

SMOKE_STAGE="small-size inline wizard render"
tmux_run new-session -d -s release-small -x 68 -y 16 -c "$RUN_ROOT" /bin/sh
tmux_run new-window -d -t release-small -n wizard -c "$SMALL_PROJECT" \
    "$TOOL_BIN" --project "$SMALL_PROJECT" init
wait_for_text release-small:wizard 'Step 1 of 7 · Repository'
wait_for_text release-small:wizard 'Next'
tmux_run send-keys -t release-small:wizard Escape

SMOKE_STAGE="init --yes and doctor"
record app "$SECONDARY_PROJECT" init --yes
grep -Fq 'review the generated default model placeholders' "$LOG"
record "$PACKAGE_PYTHON" - "$SECONDARY_PROJECT/.shikiru/config.toml" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
contents = path.read_text()
if contents.count('model = "default"') != 3:
    raise SystemExit("init --yes did not generate all three editable model placeholders")
path.write_text(contents.replace('model = "default"', 'model = "smoke"'))
PY
DOCTOR_PATH="$RUN_ROOT/doctor.json"
app "$WORK_PROJECT" --json doctor > "$DOCTOR_PATH" 2>> "$LOG"
cat "$DOCTOR_PATH" | tee -a "$LOG"
"$PACKAGE_PYTHON" - "$DOCTOR_PATH" <<'PY'
import json
import sys
from pathlib import Path

report = json.loads(Path(sys.argv[1]).read_text())
assert report["ok"]
assert {"config", "plugins"} <= {check["name"] for check in report["checks"]}
PY
SMOKE_STAGE="quickstart start and task merge"
record app "$WORK_PROJECT" \
    --set orchestration.poll_seconds=1 \
    --set orchestration.resources.max_cpu_pct=100 \
    --set orchestration.resources.min_free_ram_gb=0 start
SUBMIT_OUTPUT="$(app "$WORK_PROJECT" submit 'Implement task A1 from tasks/plan.md' 2>> "$LOG")"
printf '%s\n' "$SUBMIT_OUTPUT" | tee -a "$LOG"
[[ "$SUBMIT_OUTPUT" == T-1 ]]

STATUS_PATH="$RUN_ROOT/status.json"
wait_for_task
cat "$STATUS_PATH" | tee -a "$LOG"
grep -Fq -- '- [x] A1' "$WORK_PROJECT/tasks/todo.md"
[[ "$("$GIT_BIN" -C "$WORK_PROJECT" branch --show-current)" == main ]]
[[ "$("$GIT_BIN" -C "$WORK_PROJECT" log -1 --format=%s)" == fake:* ]]
"$GIT_BIN" -C "$WORK_PROJECT" show HEAD:tasks/todo.md | grep -Fq -- '- [x] A1'
HOME_PANE="$(tmux_run list-panes -t '=[shikiru] quickstart:shikiru' -F '#{pane_id}' | sed -n '1p')"
[[ -n "$HOME_PANE" ]]
[[ "$(tmux_run display-message -p -t "$HOME_PANE" '#{pane_dead}')" == 0 ]]
tmux_run capture-pane -p -t "$HOME_PANE" -S - > "$RUN_ROOT/home-dashboard.txt"
if grep -E 'Traceback|ValueError|active but has no step recorded' "$RUN_ROOT/home-dashboard.txt"; then
    cat "$RUN_ROOT/home-dashboard.txt" >> "$LOG"
    exit 1
fi
cat "$RUN_ROOT/home-dashboard.txt" >> "$LOG"
HOME_DASHBOARD_CHECK="home pane alive without rendered traceback after merged task"

SMOKE_STAGE="session listing and attach"
record app "$SECONDARY_PROJECT" start
LS_PATH="$RUN_ROOT/sessions.json"
app "$WORK_PROJECT" ls --json > "$LS_PATH" 2>> "$LOG"
cat "$LS_PATH" | tee -a "$LOG"
"$PACKAGE_PYTHON" - "$LS_PATH" <<'PY'
import json
import sys
from pathlib import Path

rows = json.loads(Path(sys.argv[1]).read_text())
assert len(rows) == 2
PY

tmux_run new-window -d -t release-smoke -n picker -c "$WORK_PROJECT" \
    "$TOOL_BIN" --project "$WORK_PROJECT" attach
wait_for_text release-smoke:picker 'Attach to a Shikiru session'
wait_for_text release-smoke:picker 'Select a session and press Enter. Esc cancels.'
wait_for_text release-smoke:picker 'quickstart'
wait_for_text release-smoke:picker 'yes-smoke'
tmux_run send-keys -t release-smoke:picker Escape
wait_for_text release-smoke:picker 'session selection was cancelled'

SMOKE_STAGE="small-size inline session picker selection"
tmux_run new-window -d -t release-small -n picker -c "$WORK_PROJECT" \
    env -u TMUX "$TOOL_BIN" --project "$WORK_PROJECT" attach
wait_for_text release-small:picker 'Attach to a Shikiru session'
wait_for_text release-small:picker 'quickstart'
tmux_run send-keys -t release-small:picker Down
wait_for_text release-small:picker 'yes-smoke'
tmux_run send-keys -t release-small:picker Enter
SMALL_CLIENT="$(wait_for_client '=[shikiru] yes-smoke')"
SMALL_CLIENT_SIZE="$(tmux_run display-message -p -t "$SMALL_CLIENT" '#{client_width}x#{client_height}')"
[[ "$SMALL_CLIENT_SIZE" == 68x16 ]]
printf 'small picker attached %s at %s\n' "$SMALL_CLIENT" "$SMALL_CLIENT_SIZE" | tee -a "$LOG"
tmux_run detach-client -t "$SMALL_CLIENT"
PICKER_CHECK="normal 120x38 displayed both rows and Escape cancelled; small 68x16 scrolled to and attached the second row"

ATTACH_LOG="$RUN_ROOT/attach.log"
env -i "${RUNTIME_ENV[@]}" /usr/bin/script -q "$ATTACH_LOG" "$TOOL_BIN" --project "$WORK_PROJECT" attach quickstart \
    > "$RUN_ROOT/attach-driver.log" 2>&1 &
ATTACH_PID=$!
ATTACH_CLIENT="$(wait_for_client '=[shikiru] quickstart')"
[[ -n "$ATTACH_CLIENT" ]]
tmux_run detach-client -t "$ATTACH_CLIENT"
wait "$ATTACH_PID"
ATTACH_PID=""
cat "$ATTACH_LOG" | tee -a "$LOG"

SMOKE_STAGE="stop and cleanup"
record app "$SECONDARY_PROJECT" stop --kill-session
record app "$WORK_PROJECT" stop --kill-session
app "$WORK_PROJECT" ls --json > "$LS_PATH" 2>> "$LOG"
[[ "$("$PACKAGE_PYTHON" -c 'import json,sys; print(len(json.load(open(sys.argv[1]))) )' "$LS_PATH")" == 0 ]]
[[ -z "$(tmux_run list-sessions -F '#{session_name}' | grep -F '[shikiru]' || true)" ]]
