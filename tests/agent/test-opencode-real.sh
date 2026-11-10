#!/usr/bin/env bash
# The opencode adapter against the real opencode binary, headless, with no account: opencode
# talks to tests/agent/fake-provider.py (a local OpenAI-compatible endpoint) instead of a model.
# Checks the orchestrator gets ORCHESTRATOR.md, IDLE / WORKING / ASKING from opencode's own
# server API, a real permission prompt, spawn, snapshot -> tmux restart -> `opencode --session`
# resume with the history intact, and the reaper.
#
#   tests/agent/test-opencode-real.sh [path/to/opencode]   (default: ~/.opencode/bin/opencode)
#
# Throwaway HOME, its own tmux server and opencode background service; nothing on screen.
set -u
REPO=$(cd "$(dirname "$0")/../.." && pwd)
OC=$(readlink -f "${1:-$HOME/.opencode/bin/opencode}")
[ -x "$OC" ] || { echo "opencode not found at $OC"; exit 2; }
fails=0 total=0
pass() { total=$((total + 1)); printf '  PASS  %s\n' "$*"; }
fail() { total=$((total + 1)); fails=$((fails + 1)); printf '  FAIL  %s\n' "$*"; }
check() { local what=$1; shift; if "$@" >/dev/null 2>&1; then pass "$what"; else fail "$what"; fi; }
waitfor() { local n=$1; shift; for _ in $(seq 1 $((n * 4))); do "$@" >/dev/null 2>&1 && return 0; sleep 0.25; done; return 1; }

T=$(mktemp -d "${TMPDIR:-/tmp}/magi-oc-real.XXXXXX")
export HOME=$T/home XDG_CONFIG_HOME=$T/home/.config XDG_STATE_HOME=$T/home/.local/state \
  XDG_DATA_HOME=$T/home/.local/share XDG_CACHE_HOME=$T/home/.cache XDG_RUNTIME_DIR=$T/run OPENCODE_DISABLE_AUTOUPDATE=1
TMUX_TMPDIR=$(mktemp -d /tmp/magi-tmux.XXXXXX); export TMUX_TMPDIR
unset TMUX TMUX_PANE DBUS_SESSION_BUS_ADDRESS
mkdir -p "$HOME/proj" "$HOME/.opencode/bin" "$XDG_CONFIG_HOME/opencode" "$XDG_CONFIG_HOME/magi" "$XDG_RUNTIME_DIR" "$T/bin"
chmod 700 "$XDG_RUNTIME_DIR"
printf '#!/bin/sh\nexit 1\n' > "$T/bin/systemctl"; chmod +x "$T/bin/systemctl"
ln -s "$OC" "$HOME/.opencode/bin/opencode"   # where opencode's installer puts it
export PATH=$T/bin:$REPO/agent/bin:/usr/local/bin:/usr/bin:/bin
PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
REQ=$T/requests.jsonl
setsid python3 "$REPO/tests/agent/fake-provider.py" "$PORT" "$REQ" >/dev/null 2>&1 &
FP=$!
cat > "$XDG_CONFIG_HOME/opencode/opencode.json" <<EOF
{"\$schema": "https://opencode.ai/config.json",
 "provider": {"fake": {"npm": "@ai-sdk/openai-compatible", "name": "Fake",
   "options": {"baseURL": "http://127.0.0.1:$PORT/v1", "apiKey": "none"}, "models": {"m1": {"name": "Fake M1"}}}},
 "model": "fake/m1", "permission": {"bash": "ask"}, "autoupdate": false}
EOF
printf 'ENABLED=1\nVOICE=0\nAGENT=opencode\n' > "$XDG_CONFIG_HOME/magi/agent.conf"
echo "Fix the failing test in proj." > "$T/brief.txt"
cleanup() {
  tmux kill-server 2>/dev/null
  "$OC" service stop >/dev/null 2>&1
  kill "$FP" 2>/dev/null
  [ "${KEEP:-0}" = 1 ] || rm -rf "$T" "$TMUX_TMPDIR"
}
trap cleanup EXIT

orch() { magi-agent status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["orchestrator"])'; }
is_orch() { [ "$(orch)" = "$1" ]; }
src() { python3 "$REPO/agent/lib/magi_agents.py" list | python3 -c "import json,sys; print(next(r['source'] for r in json.load(sys.stdin) if r['window']=='$1'))"; }
send() { tmux send-keys -t "=magi:=$1" -l "$2"; sleep 0.5; tmux send-keys -t "=magi:=$1" Enter; }
nreq() { [ -f "$REQ" ] && wc -l < "$REQ" || echo 0; }
last_req_has() { tail -1 "$REQ" | grep -q -- "$1"; }

echo "== opencode (real: $("$OC" --version 2>/dev/null))"
check "adapter finds opencode in ~/.opencode/bin" python3 "$REPO/agent/lib/magi_agents.py" installed
magi-agent start
check "orchestrator window runs opencode" waitfor 20 bash -c "tmux capture-pane -p -t =magi:=orchestrator | grep -q 'Ask anything'"
check "bar: IDLE before the first message (read from the pane)" waitfor 10 is_orch IDLE
send orchestrator "hello"
check "first message reaches the model" waitfor 20 bash -c "[ \$(wc -l < '$REQ' 2>/dev/null || echo 0) -ge 1 ]"
check "the model's system prompt holds MAGI's ORCHESTRATOR.md (via AGENTS.md)" grep -q "# MAGI orchestrator" "$REQ"
check "reply shown, IDLE" waitfor 15 bash -c "tmux capture-pane -p -t =magi:=orchestrator | grep -q PONG"
check "state now comes from opencode's server API" bash -c "[[ \$(python3 '$REPO/agent/lib/magi_agents.py' list) == *'server API'* ]]"
send orchestrator "SLOW please"
check "bar: WORKING while opencode streams" waitfor 6 is_orch WORKING
check "bar: IDLE when it finishes" waitfor 20 is_orch IDLE
send orchestrator "TOOL please"
check "bar: ASKING on opencode's permission prompt" waitfor 15 is_orch ASKING
check "opencode's API lists the pending permission" python3 -c "
import sys; sys.path.insert(0, '$REPO/agent/lib'); import magi_agents as m
a = m.OpenCode(); sid = [r for r in m.agents() if r['window'] == 'orchestrator'][0]['session_id']
assert a._api(f'/api/session/{sid}/permission')['data']"
check "waybar class asking" bash -c 'magi-agent status --waybar | grep -q "\"class\": \"asking\""'
tmux send-keys -t =magi:=orchestrator Right; sleep 0.3; tmux send-keys -t =magi:=orchestrator Right; sleep 0.3
tmux send-keys -t =magi:=orchestrator Enter   # Reject
check "rejected: back to IDLE (no running session in /api/session/active)" waitfor 15 is_orch IDLE

n0=$(nreq)
magi-agent spawn sub1 "$HOME/proj" "$T/brief.txt" >/dev/null
check "spawn: sub1 runs opencode with the brief" waitfor 30 bash -c "tail -n +$((n0 + 1)) '$REQ' | grep -q 'Read the brief in'"
check "AGENTS data: sub1 listed as opencode" waitfor 20 bash -c "magi-agent status --json | python3 -c \"import json,sys; d=json.load(sys.stdin); assert [w for w in d['windows'] if w['name']=='sub1' and w['agent']=='opencode' and w['state']=='idle']\""

tmux-snapshot
L=$HOME/.local/state/tmux-snapshot/layout.json
osid=$(python3 -c "import json; d=json.load(open('$L')); print([p.get('session_id') for w in d['windows'] for p in w['panes'] if p.get('kind')=='orchestrator'][0])")
ssid=$(python3 -c "import json; d=json.load(open('$L')); print([p.get('session_id') for w in d['windows'] for p in w['panes'] if w['name']=='sub1'][0])")
[[ $osid == ses_* && $ssid == ses_* ]] && pass "snapshot has both session ids ($osid, $ssid)" || fail "snapshot ids: $osid $ssid"
tmux kill-server; rm -f "$XDG_RUNTIME_DIR/tmux-restored"; sleep 1
magi-agent start
check "restore: orchestrator back with opencode --session $osid" waitfor 20 bash -c "pgrep -af 'opencode --session $osid' | grep -v pgrep"
check "restore: sub1 back with opencode --session $ssid" waitfor 20 bash -c "pgrep -af 'opencode --session $ssid' | grep -v pgrep"
check "restored TUI shows the old conversation" waitfor 20 bash -c "tmux capture-pane -p -t =magi:=orchestrator | grep -q -E 'TOOL please|declined'"
n0=$(nreq)
waitfor 10 is_orch IDLE
send orchestrator "after restore"
check "next request carries the history from before the restart" waitfor 20 bash -c "tail -n +$((n0 + 1)) '$REQ' | grep 'after restore' | grep -q 'SLOW please'"
check "bar: IDLE" waitfor 15 is_orch IDLE

waitfor 10 bash -c "magi-agent status --json | grep -q '\"name\": \"sub1\", \"state\": \"idle\"'"
REAP_IDLE_MIN=0 tmux-reap-stale >/dev/null
check "reaper closed idle sub1" bash -c "! tmux list-windows -t =magi -F '#W' | grep -qx sub1"
check "reaper log: opencode --session $ssid" grep -q "opencode --session $ssid" "$HOME/.local/state/tmux-reap/closed.log"
check "orchestrator kept" bash -c "tmux list-windows -t =magi -F '#W' | grep -qx orchestrator"

echo
[ "$fails" = 0 ] && echo "RESULT: PASS ($total checks)" || echo "RESULT: FAIL ($fails of $total)"
exit $(( fails > 0 ))
