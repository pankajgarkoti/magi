#!/usr/bin/env bash
# The Codex adapter against the real Codex binary, headless, with no account: Codex talks to
# tests/agent/fake-provider.py (a local endpoint speaking the Responses API) through a custom
# model_provider instead of OpenAI. Checks the orchestrator gets ORCHESTRATOR.md, IDLE / WORKING
# from Codex's rollout file, ASKING on a real approval prompt, AGENT_ARGS, spawn, snapshot ->
# tmux restart -> `codex resume <id>` with the history intact, and the reaper.
#
#   tests/agent/test-codex-real.sh [path/to/codex]   (default: codex on PATH)
#
# Throwaway HOME and CODEX_HOME, its own tmux server; nothing on screen.
set -u
REPO=$(cd "$(dirname "$0")/../.." && pwd)
CX=$(readlink -f "${1:-$(command -v codex)}")
[ -x "$CX" ] || { echo "codex not found ($CX)"; exit 2; }
fails=0 total=0
pass() { total=$((total + 1)); printf '  PASS  %s\n' "$*"; }
fail() { total=$((total + 1)); fails=$((fails + 1)); printf '  FAIL  %s\n' "$*"; }
check() { local what=$1; shift; if "$@" >/dev/null 2>&1; then pass "$what"; else fail "$what"; fi; }
waitfor() { local n=$1; shift; for _ in $(seq 1 $((n * 4))); do "$@" >/dev/null 2>&1 && return 0; sleep 0.25; done; return 1; }

T=$(mktemp -d "${TMPDIR:-/tmp}/magi-cx-real.XXXXXX")
export HOME=$T/home XDG_CONFIG_HOME=$T/home/.config XDG_STATE_HOME=$T/home/.local/state \
  XDG_DATA_HOME=$T/home/.local/share XDG_CACHE_HOME=$T/home/.cache XDG_RUNTIME_DIR=$T/run CODEX_HOME=$T/home/.codex
TMUX_TMPDIR=$(mktemp -d /tmp/magi-tmux.XXXXXX); export TMUX_TMPDIR
unset TMUX TMUX_PANE DBUS_SESSION_BUS_ADDRESS
mkdir -p "$HOME/proj" "$HOME/.local/bin" "$CODEX_HOME" "$XDG_CONFIG_HOME/magi" "$XDG_RUNTIME_DIR" "$T/bin"
chmod 700 "$XDG_RUNTIME_DIR"
printf '#!/bin/sh\nexit 1\n' > "$T/bin/systemctl"; chmod +x "$T/bin/systemctl"
ln -s "$CX" "$HOME/.local/bin/codex"   # where Codex's installer puts it
export PATH=$T/bin:$REPO/agent/bin:/usr/local/bin:/usr/bin:/bin
PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
REQ=$T/requests.jsonl
setsid python3 "$REPO/tests/agent/fake-provider.py" "$PORT" "$REQ" >/dev/null 2>&1 &
FP=$!
cat > "$CODEX_HOME/config.toml" <<EOF
model = "m1"
model_provider = "fake"
[model_providers.fake]
name = "Fake"
base_url = "http://127.0.0.1:$PORT/v1"
wire_api = "responses"
EOF
printf "ENABLED=1\nVOICE=0\nAGENT=codex\nAGENT_ARGS='-a on-request -s read-only'\n" > "$XDG_CONFIG_HOME/magi/agent.conf"
echo "Fix the failing test in proj." > "$T/brief.txt"
cleanup() {
  tmux kill-server 2>/dev/null
  kill "$FP" 2>/dev/null
  pkill -f "$CODEX_HOME/packages/" 2>/dev/null   # Codex's background app-server for this HOME
  [ "${KEEP:-0}" = 1 ] || rm -rf "$T" "$TMUX_TMPDIR"
}
trap cleanup EXIT

orch() { magi-agent status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["orchestrator"])'; }
is_orch() { [ "$(orch)" = "$1" ]; }
src() { python3 "$REPO/agent/lib/magi_agents.py" list | python3 -c "import json,sys; print(next(r['source'] for r in json.load(sys.stdin) if r['window']=='$1'))"; }
send() { tmux send-keys -t "=magi:=$1" -l "$2"; sleep 0.5; tmux send-keys -t "=magi:=$1" Enter; }
nreq() { [ -f "$REQ" ] && wc -l < "$REQ" || echo 0; }
last_req_has() { tail -1 "$REQ" | grep -q -- "$1"; }

echo "== codex (real: $("$CX" --version 2>/dev/null | tail -1))"
check "adapter finds codex in ~/.local/bin" python3 "$REPO/agent/lib/magi_agents.py" installed
magi-agent start
check "orchestrator window runs codex" waitfor 20 bash -c "tmux capture-pane -p -t =magi:=orchestrator | grep -q 'Ask Codex'"
check "AGENT_ARGS reach codex" bash -c "pgrep -af 'codex' | grep -q -- '-a on-request -s read-only'"
check "bar: IDLE before the first message (read from the pane)" waitfor 10 is_orch IDLE
send orchestrator "hello"
check "first message reaches the model" waitfor 20 bash -c "[ \$(wc -l < '$REQ' 2>/dev/null || echo 0) -ge 1 ]"
check "the model's system prompt holds MAGI's ORCHESTRATOR.md (via AGENTS.md)" grep -q "# MAGI orchestrator" "$REQ"
check "reply shown, IDLE" waitfor 15 bash -c "tmux capture-pane -p -t =magi:=orchestrator | grep -q PONG"
check "state now comes from Codex's rollout file" bash -c "[[ \$(python3 '$REPO/agent/lib/magi_agents.py' list) == *'rollout'* ]]"
send orchestrator "SLOW please"
check "bar: WORKING while codex streams" waitfor 6 is_orch WORKING
sleep 3
check "bar: still WORKING mid-stream (no 'esc to interrupt' on screen then)" is_orch WORKING
check "bar: IDLE when it finishes" waitfor 20 is_orch IDLE
send orchestrator "TOOL please"
check "bar: ASKING on codex's approval prompt" waitfor 15 is_orch ASKING
check "waybar class asking" bash -c 'magi-agent status --waybar | grep -q "\"class\": \"asking\""'
tmux send-keys -t =magi:=orchestrator Escape   # No
check "declined: back to IDLE (turn_aborted in the rollout)" waitfor 15 is_orch IDLE

n0=$(nreq)
magi-agent spawn sub1 "$HOME/proj" "$T/brief.txt" >/dev/null
check "spawn: sub1 runs codex with the brief" waitfor 30 bash -c "tail -n +$((n0 + 1)) '$REQ' | grep -q 'Read the brief in'"
check "AGENTS data: sub1 listed as codex" waitfor 20 bash -c "magi-agent status --json | python3 -c \"import json,sys; d=json.load(sys.stdin); assert [w for w in d['windows'] if w['name']=='sub1' and w['agent']=='codex' and w['state']=='idle']\""

tmux-snapshot
L=$HOME/.local/state/tmux-snapshot/layout.json
osid=$(python3 -c "import json; d=json.load(open('$L')); print([p.get('session_id') for w in d['windows'] for p in w['panes'] if p.get('kind')=='orchestrator'][0])")
ssid=$(python3 -c "import json; d=json.load(open('$L')); print([p.get('session_id') for w in d['windows'] for p in w['panes'] if w['name']=='sub1'][0])")
[[ $osid =~ ^[0-9a-f-]{36}$ && $ssid =~ ^[0-9a-f-]{36}$ && $osid != "$ssid" ]] && pass "snapshot has both Codex thread ids ($osid, $ssid)" || fail "snapshot ids: $osid $ssid"
tmux kill-server; rm -f "$XDG_RUNTIME_DIR/tmux-restored"; sleep 1
magi-agent start
check "restore: orchestrator back with codex resume $osid" waitfor 20 bash -c "pgrep -af 'codex resume $osid' | grep -v pgrep"
check "restore: sub1 back with codex resume $ssid" waitfor 20 bash -c "pgrep -af 'codex resume $ssid' | grep -v pgrep"
check "restored TUI shows the old conversation" waitfor 20 bash -c "tmux capture-pane -p -t =magi:=orchestrator | grep -q -E 'TOOL please|canceled'"
n0=$(nreq)
waitfor 10 is_orch IDLE
send orchestrator "after restore"
check "next request carries the history from before the restart" waitfor 20 bash -c "tail -n +$((n0 + 1)) '$REQ' | grep 'after restore' | grep -q 'SLOW please'"
check "bar: IDLE" waitfor 15 is_orch IDLE

waitfor 10 bash -c "magi-agent status --json | grep -q '\"name\": \"sub1\", \"state\": \"idle\"'"
REAP_IDLE_MIN=0 tmux-reap-stale >/dev/null
check "reaper closed idle sub1" bash -c "! tmux list-windows -t =magi -F '#W' | grep -qx sub1"
check "reaper log: codex resume $ssid" grep -q "codex resume $ssid" "$HOME/.local/state/tmux-reap/closed.log"
check "orchestrator kept" bash -c "tmux list-windows -t =magi -F '#W' | grep -qx orchestrator"

echo
[ "$fails" = 0 ] && echo "RESULT: PASS ($total checks)" || echo "RESULT: FAIL ($fails of $total)"
exit $(( fails > 0 ))
