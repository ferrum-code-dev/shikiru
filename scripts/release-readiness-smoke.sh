#!/bin/bash

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

require_tool() {
    local path
    if ! path="$(command -v "$1")"; then
        printf 'missing tool: %s\n' "$1" >&2
        exit 1
    fi
    printf '%s\n' "$path"
}

UV_BIN="$(require_tool uv)"
TMUX_BIN="$(require_tool tmux)"
GIT_BIN="$(require_tool git)"
PYTHON_BIN="$(require_tool python3)"
SCRIPT_BIN="$(require_tool script)"
SMOKE_TMPDIR="${SHIKIRU_SMOKE_TMPDIR:-/tmp}"
SOCKET_PATH_LIMIT=100
FAILURE_LOG_LINES=60
DATE="$(date +%F)"
RUN_STAMP="$(date -u +%Y%m%dT%H%M%SZ)-$$"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
ARTIFACT_DIR="$ROOT/build/release-readiness"
ARTIFACT_NAME="clean-install-$RUN_STAMP"
ARTIFACT="$ARTIFACT_DIR/$ARTIFACT_NAME.json"
LOG="$ARTIFACT_DIR/$ARTIFACT_NAME.log"
LATEST_NAME="clean-install-latest"
RUN_ROOT="$(mktemp -d "$SMOKE_TMPDIR/shikiru-release-smoke.XXXXXX")"
HOME_DIR="$RUN_ROOT/home"
CACHE_DIR="$RUN_ROOT/cache"
TOOL_DIR="$RUN_ROOT/uv/tools"
TOOL_BIN_DIR="$RUN_ROOT/uv/bin"
BASE_TOOL_DIR="$RUN_ROOT/uv/base-tools"
BASE_TOOL_BIN_DIR="$RUN_ROOT/uv/base-bin"
TMUX_TMPDIR="$RUN_ROOT/tmux"
TMUX_SHIM_DIR="$RUN_ROOT/shims"
SOCKET_NAME="shikiru-release-$RANDOM-$$"
ATTACH_PID=""
SMOKE_STAGE="preflight"
HOME_DASHBOARD_CHECK="not run"
PICKER_CHECK="not run"
SMALL_CLIENT_SIZE="not run"
WORKSPACE_NAVIGATION_CHECK="not run"

sha256() {
    if command -v sha256sum > /dev/null; then
        sha256sum "$@"
    else
        shasum -a 256 "$@"
    fi
}

mkdir -p "$ARTIFACT_DIR" "$HOME_DIR" "$CACHE_DIR/xdg" "$CACHE_DIR/config" \
    "$CACHE_DIR/data" "$CACHE_DIR/state" "$CACHE_DIR/uv" "$TOOL_DIR" "$TOOL_BIN_DIR" \
    "$BASE_TOOL_DIR" "$BASE_TOOL_BIN_DIR" "$TMUX_TMPDIR" "$TMUX_SHIM_DIR" "$RUN_ROOT/tmp"
: > "$LOG"

finish() {
    local exit_status=$?
    trap - EXIT
    printf '\nRelease smoke stage: %s\n' "$SMOKE_STAGE" >> "$LOG"
    if (( exit_status != 0 )); then
        printf 'Release smoke failed in stage: %s\nLast %d lines of %s:\n' \
            "$SMOKE_STAGE" "$FAILURE_LOG_LINES" "$LOG" >&2
        tail -n "$FAILURE_LOG_LINES" "$LOG" >&2
    fi
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
            sha256 "$ROOT/scripts/release-readiness-smoke.sh"
            sha256 "$ROOT/tests/support/release_wait.py"
        } | sha256 | awk '{print $1}'
    )"
    SMOKE_EXIT_STATUS="$exit_status" "$PYTHON_BIN" - "$ARTIFACT" "$DATE" "$STARTED_AT" "$ARTIFACT_NAME" "$SOURCE_COMMIT" "$SOURCE_FINGERPRINT" "$SMOKE_STAGE" "$HOME_DASHBOARD_CHECK" "$PICKER_CHECK" "$SMALL_CLIENT_SIZE" "$WORKSPACE_NAVIGATION_CHECK" <<'PY'
import json
import os
import sys
from pathlib import Path

artifact = Path(sys.argv[1])
data = {
    "date": sys.argv[2],
    "started_at": sys.argv[3],
    "source_commit": sys.argv[5],
    "source_diff_fingerprint": sys.argv[6],
    "failed_stage": sys.argv[7] if os.environ["SMOKE_EXIT_STATUS"] != "0" else None,
    "status": "passed" if os.environ["SMOKE_EXIT_STATUS"] == "0" else "failed",
    "installation": ["uv tool install <built wheel>", "uv tool install <built wheel> --with <fake-agent wheel> --with pytest --with pytest-asyncio --with pytest-xdist"],
    "fresh_disposable_home": True,
    "runtime_isolation": ["HOME", "XDG directories", "uv cache/tools/bin", "private tmux socket"],
    "installed_package_data_and_entrypoints": "verified" if os.environ["SMOKE_EXIT_STATUS"] == "0" else "incomplete",
    "installed_cli_flows": ["interactive init wizard and Doctor", "init --yes", "doctor", "start", "submit", "status", "ls", "picker attach", "named attach", "installed-package Textual workspace boundary", "stop and cleanup"],
    "wizard_terminals": ["120x38", "68x16"],
    "picker_terminal": sys.argv[9],
    "small_picker_client_size": sys.argv[10],
    "workspace_navigation": sys.argv[11],
    "home_dashboard_pane_after_merge": sys.argv[8],
    "task_result": "T-1 merged on main and A1 ticked from interactive quickstart" if os.environ["SMOKE_EXIT_STATUS"] == "0" else "incomplete",
    "agent_cost": "unknown; test-only fake agent reports no billed cost",
    "limitation": "The existing test-only fake-agent plugin, pytest and pytest-xdist (needed by its test-kit import and conftest hooks) were injected only into this disposable tool environment for repeatable unauthenticated packaging and CLI smoke coverage; no paid-provider or real-project reliability claim is made.",
    "log": f"build/release-readiness/{sys.argv[4]}.log",
}
artifact.write_text(json.dumps(data, indent=2) + "\n")
PY
    if (( exit_status == 0 )); then
        cp "$ARTIFACT" "$ARTIFACT_DIR/$LATEST_NAME.json"
        cp "$LOG" "$ARTIFACT_DIR/$LATEST_NAME.log"
    fi
    rm -rf "$RUN_ROOT"
    exit "$exit_status"
}

TOOL_PATH=""
for tool in "$UV_BIN" "$TMUX_BIN" "$GIT_BIN" "$PYTHON_BIN"; do
    TOOL_PATH="$TOOL_PATH:$(dirname "$tool")"
done
PATH="$TOOL_BIN_DIR:$TMUX_SHIM_DIR$TOOL_PATH:$(getconf PATH)"
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

trap finish EXIT

SOCKET_PATH="$TMUX_TMPDIR/tmux-$(id -u)/$SOCKET_NAME"
if (( ${#SOCKET_PATH} > SOCKET_PATH_LIMIT )); then
    printf 'tmux socket path is %d characters, over %d; set SHIKIRU_SMOKE_TMPDIR to a shorter directory\n' \
        "${#SOCKET_PATH}" "$SOCKET_PATH_LIMIT" >&2
    exit 1
fi

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

wait_for_exit() {
    run_waiter exit "$TMUX_BIN" "$SOCKET_NAME" "$WIZARD_PANE" "$1" >> "$LOG" 2>&1
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


if [[ -n "$(find "$HOME_DIR" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
    printf 'disposable HOME was not empty before installation\n' >&2
    exit 1
fi

cat > "$TMUX_SHIM_DIR/tmux" <<EOF
#!/bin/sh
exec "$TMUX_BIN" -L "$SOCKET_NAME" -f /dev/null "\$@"
EOF
chmod 755 "$TMUX_SHIM_DIR/tmux"

SMOKE_STAGE="wheel and sdist build"
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
    "$UV_BIN" build --out-dir "$RUN_ROOT/dist"

shopt -s nullglob
WHEELS=("$RUN_ROOT/dist"/*.whl)
SDISTS=("$RUN_ROOT/dist"/*.tar.gz)
[[ ${#WHEELS[@]} -eq 1 && ${#SDISTS[@]} -eq 1 ]]
SMOKE_STAGE="source distribution build"
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
    "$UV_BIN" build "${SDISTS[0]}" --wheel --out-dir "$RUN_ROOT/from-sdist"
FROM_SDIST_WHEELS=("$RUN_ROOT/from-sdist"/*.whl)
[[ ${#FROM_SDIST_WHEELS[@]} -eq 1 ]]
SMOKE_STAGE="runtime-only wheel installation"
BASE_PATH="$BASE_TOOL_BIN_DIR:$TMUX_SHIM_DIR$TOOL_PATH:$(getconf PATH)"
record env -i \
    "HOME=$HOME_DIR" \
    "XDG_CACHE_HOME=$CACHE_DIR/xdg" \
    "XDG_CONFIG_HOME=$CACHE_DIR/config" \
    "XDG_DATA_HOME=$CACHE_DIR/data" \
    "XDG_STATE_HOME=$CACHE_DIR/state" \
    "UV_CACHE_DIR=$CACHE_DIR/uv" \
    "UV_TOOL_DIR=$BASE_TOOL_DIR" \
    "UV_TOOL_BIN_DIR=$BASE_TOOL_BIN_DIR" \
    "PATH=$BASE_PATH" \
    "$UV_BIN" tool install "${WHEELS[0]}"
[[ -x "$BASE_TOOL_BIN_DIR/shikiru" ]]
record env -i "HOME=$HOME_DIR" "PATH=$BASE_PATH" "$BASE_TOOL_BIN_DIR/shikiru" --help

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
    "$UV_BIN" build --package fake-agent --wheel --out-dir "$RUN_ROOT/fake-agent"
FAKE_AGENT_WHEELS=("$RUN_ROOT/fake-agent"/*.whl)
[[ ${#FAKE_AGENT_WHEELS[@]} -eq 1 ]]

SMOKE_STAGE="built-wheel installation"
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
    "$UV_BIN" tool install "${WHEELS[0]}" --with "${FAKE_AGENT_WHEELS[0]}" --with pytest --with pytest-asyncio --with pytest-xdist

TOOL_BIN="$TOOL_BIN_DIR/shikiru"
PACKAGE_PYTHON="$(sed -n '1{s/^#!//;p;}' "$TOOL_BIN")"
[[ -x "$TOOL_BIN" && -x "$PACKAGE_PYTHON" ]]
record env -i "${RUNTIME_ENV[@]}" "$TOOL_BIN" --version
record env -i "HOME=$HOME_DIR" "PATH=$PATH" "UV_TOOL_DIR=$TOOL_DIR" "$PACKAGE_PYTHON" - <<'PY'
from importlib.metadata import entry_points
from importlib.resources import files
from pathlib import Path

import os
import shikiru

defaults = files("shikiru.config").joinpath("defaults.toml")
assert defaults.is_file()
assert "sample_seconds = 2" in defaults.read_text()
assert files("shikiru.prompts").joinpath("templates", "implement.md.j2").is_file()
assert files("shikiru.store.migrations").joinpath("009_billing_declarations.sql").is_file()
assert files("shikiru.dashboard").joinpath("shikiru_init_ascii_art.txt").is_file()
module_path = Path(shikiru.__file__).resolve()
assert module_path.is_relative_to(Path(os.environ["UV_TOOL_DIR"]).resolve())
assert not module_path.is_relative_to(Path.cwd())
agents = {entry.name for entry in entry_points(group="shikiru.agents")}
widgets = {entry.name for entry in entry_points(group="shikiru.widgets")}
commands = {entry.name for entry in entry_points(group="console_scripts")}
assert "fake" in agents
assert {"header", "resources", "docker", "faults"} <= widgets
assert "shikiru" in commands
from tempfile import TemporaryDirectory

from shikiru.store.database import Database

with TemporaryDirectory() as temporary_directory:
    assert Database(Path(temporary_directory) / "state.db").health() > 0
print("installed entry points, defaults, prompt, migration and artwork resources verified")
PY

WIZARD_PROJECT="$RUN_ROOT/quickstart"
SMALL_PROJECT="$RUN_ROOT/wizard-small"
WORK_PROJECT="$WIZARD_PROJECT"
SECONDARY_PROJECT="$RUN_ROOT/yes-smoke"
SMOKE_STAGE="normal-size interactive wizard"
prepare_project "$WIZARD_PROJECT"
prepare_project "$SMALL_PROJECT"
prepare_project "$SECONDARY_PROJECT"

tmux_run new-session -d -s release-smoke -x 120 -y 38 -c "$RUN_ROOT" /bin/sh
WIZARD_WINDOW="$(tmux_run new-window -P -F '#{window_id}' -d -t release-smoke -n wizard -c "$WIZARD_PROJECT" \
    "$TOOL_BIN" --project "$WIZARD_PROJECT" init
    )"
WIZARD_PANE="$(tmux_run display-message -p -t "$WIZARD_WINDOW" '#{pane_id}')"
tmux_run set-option -w -t "$WIZARD_WINDOW" remain-on-exit on
click_wizard_button() {
    run_waiter click "$TMUX_BIN" "$SOCKET_NAME" "$WIZARD_PANE" "$1" "$2" \
        >> "$LOG" 2>&1
}
wait_for_text "$WIZARD_WINDOW" 'SHIKIRU · Repository'
click_wizard_button 'Next' 'SHIKIRU · Agents and ladders'
wait_for_text "$WIZARD_WINDOW" 'Role and complexity ladders'
tmux_run send-keys -t "$WIZARD_WINDOW" Enter
run_waiter ladder "$TMUX_BIN" "$SOCKET_NAME" "$WIZARD_PANE" 'Leader' 'fake:smoke' >> "$LOG" 2>&1
tmux_run send-keys -t "$WIZARD_WINDOW" BTab
tmux_run send-keys -t "$WIZARD_WINDOW" Down
tmux_run send-keys -t "$WIZARD_WINDOW" Enter
wait_for_text "$WIZARD_WINDOW" 'Edit Implementer · Base'
run_waiter ladder "$TMUX_BIN" "$SOCKET_NAME" "$WIZARD_PANE" 'Implementer · Base' 'fake:strong' >> "$LOG" 2>&1
tmux_run send-keys -t "$WIZARD_WINDOW" BTab
tmux_run send-keys -t "$WIZARD_WINDOW" Down
tmux_run send-keys -t "$WIZARD_WINDOW" Down
tmux_run send-keys -t "$WIZARD_WINDOW" Down
tmux_run send-keys -t "$WIZARD_WINDOW" Down
tmux_run send-keys -t "$WIZARD_WINDOW" Enter
wait_for_text "$WIZARD_WINDOW" 'Edit Helper'
run_waiter ladder "$TMUX_BIN" "$SOCKET_NAME" "$WIZARD_PANE" 'Helper' 'fake:smoke' >> "$LOG" 2>&1
tmux_run capture-pane -p -t "$WIZARD_WINDOW" -S - > "$RUN_ROOT/wizard-agents.txt"
click_wizard_button 'Next' 'SHIKIRU · Trackers'
click_wizard_button 'Next' 'SHIKIRU · Caches and CoW'
click_wizard_button 'Next' 'SHIKIRU · Orchestration'
click_wizard_button 'Next' 'SHIKIRU · Agents'
click_wizard_button 'Next' 'SHIKIRU · Integration mode'
click_wizard_button 'Next' 'SHIKIRU · Finish'
wait_for_advance "$WIZARD_WINDOW" 'Doctor passed'
click_wizard_button 'Exit' ''
wait_for_exit ''

WIZARD_CONFIG="$WIZARD_PROJECT/.shikiru/config.toml"
[[ -f "$WIZARD_CONFIG" ]]
grep -Fq 'ladder = ["fake:smoke"]' "$WIZARD_CONFIG"
grep -Fq 'ladder = ["fake:strong"]' "$WIZARD_CONFIG"
record "$PACKAGE_PYTHON" - "$WORK_PROJECT" <<'PY'
import sys
from pathlib import Path

from shikiru.config.loader import load_settings

settings = load_settings(Path(sys.argv[1]))
assert settings.roles.leader.ladder[0].model == "smoke"
assert settings.roles.implementer.ladder[0].model == "strong"
assert settings.roles.helper.ladder[0].model == "smoke"
PY

SMOKE_STAGE="small-size wizard cancellation"
tmux_run new-session -d -s release-small -x 68 -y 16 -c "$RUN_ROOT" /bin/sh
SMALL_WIZARD_WINDOW="$(tmux_run new-window -P -F '#{window_id}' -d -t release-small -n wizard -c "$SMALL_PROJECT" \
    "$TOOL_BIN" --project "$SMALL_PROJECT" init
    )"
SMALL_WIZARD_PANE="$(tmux_run display-message -p -t "$SMALL_WIZARD_WINDOW" '#{pane_id}')"
tmux_run set-option -w -t "$SMALL_WIZARD_WINDOW" remain-on-exit on
wait_for_text "$SMALL_WIZARD_WINDOW" 'SHIKIRU · Repository'
wait_for_text "$SMALL_WIZARD_WINDOW" 'Next'
run_waiter click "$TMUX_BIN" "$SOCKET_NAME" "$SMALL_WIZARD_PANE" 'Cancel' '' >> "$LOG" 2>&1
run_waiter exit "$TMUX_BIN" "$SOCKET_NAME" "$SMALL_WIZARD_PANE" '' >> "$LOG" 2>&1
[[ ! -e "$SMALL_PROJECT/.shikiru/config.toml" ]]

SMOKE_STAGE="init --yes and doctor"
record app "$SECONDARY_PROJECT" init --yes
record "$PACKAGE_PYTHON" - "$SECONDARY_PROJECT/.shikiru/config.toml" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
contents = path.read_text()
if contents.count('"fake:default"') != 3:
    raise SystemExit("init --yes did not generate all three editable model placeholders")
path.write_text(contents.replace('"fake:default"', '"fake:smoke"'))
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
    --set orchestration.resources.min_free_ram_gb=0 \
    --set dashboard.animations=false start
SUBMIT_OUTPUT="$(app "$WORK_PROJECT" --json submit 'Implement task A1 from tasks/plan.md' 2>> "$LOG")"
printf '%s\n' "$SUBMIT_OUTPUT" | tee -a "$LOG"
SUBMIT_TASK_ID="$("$PACKAGE_PYTHON" - "$SUBMIT_OUTPUT" <<'PY'
import json
import sys

print(json.loads(sys.argv[1])["task_id"])
PY
)"
[[ "$SUBMIT_TASK_ID" == T-1 ]]

STATUS_PATH="$RUN_ROOT/status.json"
wait_for_task
cat "$STATUS_PATH" | tee -a "$LOG"
grep -Fq -- '- [x] A1' "$WORK_PROJECT/tasks/todo.md"
[[ "$("$GIT_BIN" -C "$WORK_PROJECT" branch --show-current)" == main ]]
[[ "$("$GIT_BIN" -C "$WORK_PROJECT" log -1 --format=%s)" == fake:* ]]
"$GIT_BIN" -C "$WORK_PROJECT" show HEAD:tasks/todo.md | grep -Fq -- '- [x] A1'
HOME_PANE="$(tmux_run list-panes -t '=[shikiru] quickstart:dashboard' -F '#{pane_id}' | sed -n '1p')"
[[ -n "$HOME_PANE" ]]
[[ "$(tmux_run display-message -p -t "$HOME_PANE" '#{pane_dead}')" == 0 ]]
tmux_run capture-pane -p -t "$HOME_PANE" -S - > "$RUN_ROOT/home-dashboard.txt"
if grep -E 'Traceback|ValueError|active but has no step recorded' "$RUN_ROOT/home-dashboard.txt"; then
    cat "$RUN_ROOT/home-dashboard.txt" >> "$LOG"
    exit 1
fi
cat "$RUN_ROOT/home-dashboard.txt" >> "$LOG"
HOME_DASHBOARD_CHECK="home pane alive without rendered traceback after merged task"

SMOKE_STAGE="installed-package Textual workspace boundary"
TEST_IMPORT_ROOT="$RUN_ROOT/test-import"
TEST_TMUX_TMPDIR="$RUN_ROOT/test-tmux"
TEST_PATH="$TOOL_BIN_DIR:$(dirname "$TMUX_BIN"):$(dirname "$GIT_BIN"):$(dirname "$PYTHON_BIN"):$(getconf PATH)"
mkdir -p "$TEST_IMPORT_ROOT" "$TEST_TMUX_TMPDIR"
ln -s "$ROOT/tests" "$TEST_IMPORT_ROOT/tests"
cd "$RUN_ROOT"
record env -i \
    "HOME=$HOME_DIR" \
    "PATH=$TEST_PATH" \
    "UV_TOOL_DIR=$TOOL_DIR" \
    "TMUX_TMPDIR=$TEST_TMUX_TMPDIR" \
    "TMPDIR=$RUN_ROOT/tmp" \
    "TERM=xterm-256color" \
    "NO_COLOR=1" \
    "PYTHONPATH=$TEST_IMPORT_ROOT" \
    "$PACKAGE_PYTHON" -m pytest -q -c /dev/null --import-mode=importlib \
    -o asyncio_mode=auto \
    "$ROOT/tests/integration/test_workspace_walkthroughs.py::test_fake_agent_workspace_finds_verdict_and_applies_contextual_message" \
    "$ROOT/tests/integration/test_dashboard_app.py::test_home_row_click_and_enter_keep_the_shared_inspector_on_home" \
    "$ROOT/tests/integration/test_dashboard_app.py::test_home_escape_clears_selection_after_search_and_keeps_compact_detail_access" \
    "$ROOT/tests/integration/test_dashboard_app.py::test_home_resizes_through_the_terminal_matrix_without_losing_selection_or_focus" \
    "$ROOT/tests/integration/test_dashboard_app.py::test_task_detail_contextual_controls_are_reachable_at_narrow_width" \
    "$ROOT/tests/integration/test_dashboard_task_detail.py::test_compact_palette_form_keeps_visible_text_cursor_and_run_after_resize" \
    "$ROOT/tests/integration/test_dashboard_task_detail.py::test_task_evidence_filter_and_reading_position_survive_resize_and_refresh" \
    "$ROOT/tests/integration/test_dashboard_motion.py::test_disabled_animations_keep_countdowns_live_without_animating_frames"
cd "$ROOT"
WORKSPACE_NAVIGATION_CHECK="eight installed-interpreter Textual boundary tests under TERM=xterm-256color and NO_COLOR=1: fake-agent contextual message reaches Applied; click/Enter keep inline Home detail mounted; Escape clears Home selection after overlays/search; Home resize 120x40 through 20x8 preserves selection/focus; narrow detail controls stay reachable; palette cursor/form survives resize; Evidence filter/reading position survives resize and refresh; disabled motion keeps countdown live; private-tmux installed Home launch and attach-picker checks are separate"

SMOKE_STAGE="session listing and attach"
record app "$SECONDARY_PROJECT" start
LS_PATH="$RUN_ROOT/sessions.json"
app "$WORK_PROJECT" --json ls > "$LS_PATH" 2>> "$LOG"
cat "$LS_PATH" | tee -a "$LOG"
"$PACKAGE_PYTHON" - "$LS_PATH" <<'PY'
import json
import sys
from pathlib import Path

rows = json.loads(Path(sys.argv[1]).read_text())
assert len(rows) == 2
PY

PICKER_WINDOW="$(tmux_run new-window -P -F '#{window_id}' -d -t release-smoke -n picker -c "$RUN_ROOT" \
    "$TOOL_BIN" attach
    )"
PICKER_PANE="$(tmux_run display-message -p -t "$PICKER_WINDOW" '#{pane_id}')"
tmux_run set-option -w -t "$PICKER_WINDOW" remain-on-exit on
wait_for_text "$PICKER_WINDOW" 'Select a session number'
wait_for_text "$PICKER_WINDOW" 'quickstart'
wait_for_text "$PICKER_WINDOW" 'yes-smoke'
tmux_run send-keys -t "$PICKER_WINDOW" q Enter
run_waiter exit "$TMUX_BIN" "$SOCKET_NAME" "$PICKER_PANE" 'session selection was cancelled' 2 >> "$LOG" 2>&1

SMOKE_STAGE="small-size numbered session selection"
SMALL_PICKER_WINDOW="$(tmux_run new-window -P -F '#{window_id}' -d -t release-small -n picker -c "$RUN_ROOT" \
    env -u TMUX "$TOOL_BIN" attach
    )"
wait_for_text "$SMALL_PICKER_WINDOW" 'Select a session number'
wait_for_text "$SMALL_PICKER_WINDOW" 'yes-smoke'
SESSION_NUMBER="$("$PACKAGE_PYTHON" - "$LS_PATH" <<'PYTHON'
import json
import sys
from pathlib import Path

rows = json.loads(Path(sys.argv[1]).read_text())
print(next(index for index, row in enumerate(rows, start=1) if row["session"] == "[shikiru] yes-smoke"))
PYTHON
)"
tmux_run send-keys -l -t "$SMALL_PICKER_WINDOW" "$SESSION_NUMBER"
tmux_run send-keys -t "$SMALL_PICKER_WINDOW" Enter
SMALL_CLIENT="$(wait_for_client '=[shikiru] yes-smoke')"
SMALL_CLIENT_SIZE="$(tmux_run display-message -p -t "$SMALL_CLIENT" '#{client_width}x#{client_height}')"
[[ "$SMALL_CLIENT_SIZE" == 68x16 ]]
printf 'small picker attached %s at %s\n' "$SMALL_CLIENT" "$SMALL_CLIENT_SIZE" | tee -a "$LOG"
tmux_run detach-client -t "$SMALL_CLIENT"
PICKER_CHECK="normal 120x38 displayed both rows and q cancelled; small 68x16 selected the numbered project and attached"

ATTACH_LOG="$RUN_ROOT/attach.log"
ATTACH_COMMAND=("$TOOL_BIN" --project "$WORK_PROJECT" attach quickstart)
if "$SCRIPT_BIN" --version > /dev/null 2>&1; then
    ATTACH_PTY=("$SCRIPT_BIN" -q -e -c "$(printf '%q ' "${ATTACH_COMMAND[@]}")" "$ATTACH_LOG")
else
    ATTACH_PTY=("$SCRIPT_BIN" -q "$ATTACH_LOG" "${ATTACH_COMMAND[@]}")
fi
env -i "${RUNTIME_ENV[@]}" "${ATTACH_PTY[@]}" > "$RUN_ROOT/attach-driver.log" 2>&1 &
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
app "$WORK_PROJECT" --json ls > "$LS_PATH" 2>> "$LOG"
[[ "$("$PACKAGE_PYTHON" -c 'import json,sys; print(len(json.load(open(sys.argv[1]))) )' "$LS_PATH")" == 0 ]]
[[ -z "$(tmux_run list-sessions -F '#{session_name}' | grep -F '[shikiru]' || true)" ]]
