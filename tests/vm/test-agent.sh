#!/usr/bin/env bash
# MAGI agent layer test (run in the distro test VM as the desktop user, inside a MAGI session;
# ssh is fine). Uses tests/agent/agent-stub instead of the real agent: no account needed.
# AGENT=claude (default) | opencode | codex picks the adapter under test.
#
#   test-agent.sh setup      use $AGENT -> install (stub installer) -> on -> sign-in ->
#                            orchestrator up -> listener up -> voice loop with no mic -> bar
#                            status IDLE / WORKING / ASKING -> sub-agent -> snapshot
#   test-agent.sh reboot     (after a reboot) session back, orchestrator resumed in window 1,
#                            listener up, bar status
#   test-agent.sh voice      the voice loop alone
#   test-agent.sh offline    (with the VM's network link down) listener + console still up,
#                            bar says OFFLINE
#   test-agent.sh reset      turn the agent layer off and remove the stub agent and its state,
#                            so the next adapter starts from a fresh first login
# Voice loop with no mic: Piper speaks into a PipeWire null sink; the listener listens to that
# sink's monitor; whisper transcribes; the stub logs what arrives in the orchestrator pane.
set -u
T=/var/tmp/magi-test
export PATH=/usr/bin:$PATH XDG_RUNTIME_DIR=/run/user/$(id -u)
export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus
fails=0
pass() { printf 'PASS  %s\n' "$*"; }
fail() { printf 'FAIL  %s\n' "$*"; fails=$((fails + 1)); }
check() { local what=$1; shift; if "$@" >/dev/null 2>&1; then pass "$what"; else fail "$what"; fi; }
waitfor() { local n=$1; shift; for _ in $(seq 1 "$n"); do "$@" >/dev/null 2>&1 && return 0; sleep 1; done; return 1; }
AGENT=${AGENT:-claude}
LOG=~/.magi-stub/$AGENT.log
echo "adapter: $AGENT"
win1() { tmux list-windows -t =magi -F '#{window_index} #W' 2>/dev/null | awk '$1==1{print $2}'; }
status() { magi-agent status --json; }
jget() { status | python3 -c "import json,sys; print(json.load(sys.stdin)['$1'])"; }

voice_loop() {
  local sentence="Open the control centre and switch to the red theme."
  n0=$(grep -c "received: \[voice\]" "$LOG" 2>/dev/null); n0=${n0:-0}
  paplay --device=magi_test "$T/say.wav" 2>/dev/null || {
    "$VOICE_PY" -m piper --model /usr/lib/magi/voice/voices/en_US-ljspeech-medium.onnx --output_file "$T/say.wav" <<< "$sentence" >/dev/null 2>&1
    sleep 1; paplay --device=magi_test "$T/say.wav"; }
  if waitfor 40 bash -c "[ \$(grep -ci 'received: \[voice\].*control cent' '$LOG') -gt $n0 ]"; then
    pass "voice loop: spoken into the null sink -> whisper -> orchestrator pane: $(grep -i 'received: \[voice\]' "$LOG" | tail -1 | cut -d' ' -f3-)"
  else
    fail "voice loop: nothing arrived in the orchestrator (heard.log: $(tail -2 ~/.local/state/magi/agent/heard.log 2>/dev/null | tr '\n' ' '))"
  fi
}

# the bar through every orchestrator state, driven by the stub: IDLE -> WORKING -> IDLE -> ASKING -> IDLE
orch() { magi-agent status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["orchestrator"])'; }
send() { tmux send-keys -t =magi:=orchestrator -l "$1"; sleep 0.3; tmux send-keys -t =magi:=orchestrator Enter; }
states() {
  check "bar: IDLE" waitfor 15 bash -c '[ "$(magi-agent status --json | python3 -c "import json,sys;print(json.load(sys.stdin)[\"orchestrator\"])")" = IDLE ]'
  send "work 6"
  check "bar: WORKING" waitfor 5 bash -c '[ "$(magi-agent status --json | python3 -c "import json,sys;print(json.load(sys.stdin)[\"orchestrator\"])")" = WORKING ]'
  [ -n "${SHOT_WORKING:-}" ] && eval "$SHOT_WORKING"
  check "bar: back to IDLE" waitfor 15 bash -c '[ "$(magi-agent status --json | python3 -c "import json,sys;print(json.load(sys.stdin)[\"orchestrator\"])")" = IDLE ]'
  send "ask"
  check "bar: ASKING (class asking)" waitfor 6 bash -c 'magi-agent status --waybar | grep -q "\"class\": \"asking\""'
  [ -n "${SHOT_ASKING:-}" ] && eval "$SHOT_ASKING"
  send "y"
  check "bar: IDLE after answering" waitfor 8 bash -c '[ "$(magi-agent status --json | python3 -c "import json,sys;print(json.load(sys.stdin)[\"orchestrator\"])")" = IDLE ]'
}

case "${1:-setup}" in
setup)
  . /usr/share/magi/agent/bin/magi-voice-env
  magi-agent use "$AGENT" >/dev/null
  check "$AGENT not installed yet" bash -c "! /usr/share/magi/agent/lib/magi_agents.py installed"
  check "bar status: SETUP" bash -c '[ "$(magi-agent status --json | python3 -c "import json,sys;print(json.load(sys.stdin)[\"orchestrator\"])")" = SETUP ]'
  up=$(tr a-z A-Z <<< "$AGENT")
  out=$(env "MAGI_${up}_INSTALL_URL=file://$T/install-stub.sh" STUB_AGENT="$AGENT" AGENT_STUB="$T/agent-stub" magi-agent install 2>&1)
  check "magi-agent install ran the $AGENT installer it was pointed at" /usr/share/magi/agent/lib/magi_agents.py installed
  # the mic: a null sink, so the test can "speak" into it (test only)
  pactl load-module module-null-sink sink_name=magi_test sink_properties=device.description=magi-test >/dev/null
  mkdir -p ~/.config/magi && printf 'SOURCE=magi_test.monitor\n' >> ~/.config/magi/agent.conf
  magi-agent on
  check "magi-agents.service enabled for every login" systemctl --user is-enabled magi-agents.service
  check "tmux-snapshot.timer + tmux-reap-stale.timer enabled" systemctl --user is-enabled tmux-snapshot.timer tmux-reap-stale.timer
  check "tmux session magi exists" waitfor 10 tmux has-session -t =magi
  [ "$(win1)" = orchestrator ] && pass "window 1 is the orchestrator" || fail "window 1 is '$(win1)'"
  check "orchestrator signs in if needed and starts $AGENT" waitfor 30 grep -q "start argv=" "$LOG"
  if [ "$AGENT" = claude ]; then
    grep "start argv=" "$LOG" | tail -1 | grep -q -- "'--append-system-prompt-file', '/usr/share/magi/agent/ORCHESTRATOR.md'" &&
      pass "orchestrator runs with MAGI's ORCHESTRATOR.md" || fail "orchestrator argv: $(grep 'start argv=' "$LOG" | tail -1)"
  else
    grep "start argv=" "$LOG" | tail -1 | grep -q "agents_md=# MAGI orchestrator" &&
      pass "orchestrator runs where AGENTS.md is MAGI's ORCHESTRATOR.md" || fail "orchestrator start: $(grep 'start argv=' "$LOG" | tail -1)"
  fi
  check "listener window up" waitfor 10 bash -c "tmux list-windows -t =magi -F '#W' | grep -qx listen"
  check "listener loaded whisper offline and is listening" waitfor 60 grep -q "listening on magi_test.monitor" ~/.local/state/magi/agent/heard.log
  voice_loop
  check "bar status: orchestrator IDLE" waitfor 10 bash -c '[ "$(magi-agent status --json | python3 -c "import json,sys;print(json.load(sys.stdin)[\"orchestrator\"])")" = IDLE ]'
  [ "$(jget listener)" = LISTEN ] && pass "bar status: listener LISTEN" || fail "listener: $(jget listener)"
  magi-agent voice off >/dev/null; sleep 1
  [ "$(jget listener)" = MUTED ] && pass "mute: listener MUTED, window closed" || fail "mute: $(jget listener)"
  magi-agent voice on >/dev/null; sleep 2
  [ "$(jget listener)" = LISTEN ] && pass "unmute: listener back" || fail "unmute: $(jget listener)"
  check "waybar module renders" bash -c 'magi-agent status --waybar | python3 -c "import json,sys; d=json.load(sys.stdin); assert d[\"text\"].startswith(\"◉ IDLE\")"'
  states
  echo "Fix the failing test in ~/proj." > "$T/brief.txt"; mkdir -p ~/proj
  magi-agent spawn sub1 ~/proj "$T/brief.txt" >/dev/null
  check "sub-agent sub1 listed as $AGENT, idle" waitfor 15 bash -c "magi-agent status --json | python3 -c \"import json,sys; d=json.load(sys.stdin); assert [w for w in d['windows'] if w['name']=='sub1' and w['agent']=='$AGENT' and w['state']=='idle'] and d['agents']==1\""
  tmux-snapshot
  check "tmux layout snapshot saved" python3 -c "import json,os; d=json.load(open(os.path.expanduser('~/.local/state/tmux-snapshot/layout.json'))); assert any(p.get('kind')=='orchestrator' and p.get('agent')=='$AGENT' for w in d['windows'] for p in w['panes'])"
  sid=$(python3 -c "import json,os; d=json.load(open(os.path.expanduser('~/.local/state/tmux-snapshot/layout.json'))); print([p['session_id'] for w in d['windows'] for p in w['panes'] if p.get('kind')=='orchestrator'][0])")
  echo "$sid" > "$T/orchestrator-session-id"
  echo "orchestrator session id: $sid"
  ;;
reboot)
  sid=$(cat "$T/orchestrator-session-id" 2>/dev/null)
  pactl load-module module-null-sink sink_name=magi_test >/dev/null 2>&1   # the test "mic" is gone after a reboot
  check "tmux session magi back after reboot (no terminal opened)" waitfor 60 tmux has-session -t =magi
  [ "$(win1)" = orchestrator ] && pass "window 1 is the orchestrator" || fail "window 1 is '$(win1)'"
  case "$AGENT" in
    claude) check "orchestrator resumed: claude --resume $sid" waitfor 30 grep -q "start argv=.*'--resume', '$sid'" "$LOG" ;;
    opencode) check "orchestrator resumed: opencode --session $sid" waitfor 30 grep -q "start argv=.*'--session', '$sid'.*agents_md=# MAGI" "$LOG" ;;
    codex) check "orchestrator resumed: codex resume $sid" waitfor 30 grep -q "start argv=\['resume', '$sid'" "$LOG" ;;
  esac
  check "sub-agent sub1 restored" waitfor 30 bash -c "tmux list-windows -t =magi -F '#W' | grep -qx sub1"
  check "listener up after reboot" waitfor 60 grep -q "listening on" <(tail -5 ~/.local/state/magi/agent/heard.log)
  check "bar status: orchestrator IDLE" waitfor 20 bash -c '[ "$(magi-agent status --json | python3 -c "import json,sys;print(json.load(sys.stdin)[\"orchestrator\"])")" = IDLE ]'
  check "tmux server lives in magi-agents.service, not in the desktop session" \
    bash -c "grep -q magi-agents.service /proc/\$(tmux display-message -p '#{pid}')/cgroup"
  ;;
states)
  states
  ;;
reset)
  magi-agent off >/dev/null 2>&1
  tmux kill-server 2>/dev/null
  pkill -f '/var/tmp/magi-test/agent-stub serve' 2>/dev/null
  pkill -f '[.]opencode/bin/opencode serve' 2>/dev/null
  rm -rf ~/.magi-stub ~/.claude ~/.local/bin/claude ~/.local/share/claude ~/.opencode ~/.local/state/opencode \
    ~/.local/state/opencode-stub ~/.codex ~/.local/bin/codex ~/.local/state/tmux-snapshot ~/.local/state/tmux-reap \
    ~/.local/state/magi ~/.config/magi "$T/orchestrator-session-id"
  echo "agent layer reset"
  exit 0
  ;;
voice)
  . /usr/share/magi/agent/bin/magi-voice-env
  pactl load-module module-null-sink sink_name=magi_test >/dev/null 2>&1
  waitfor 30 grep -q "listening on magi_test.monitor" <(tail -3 ~/.local/state/magi/agent/heard.log)
  voice_loop
  ;;
offline)
  . /usr/share/magi/agent/bin/magi-voice-env
  pactl load-module module-null-sink sink_name=magi_test >/dev/null 2>&1
  check "network is down (NetworkManager connectivity)" bash -c '[ "$(nmcli -t networking connectivity)" != full ]'
  check "bar status: OFFLINE" waitfor 15 bash -c '[ "$(magi-agent status --json | python3 -c "import json,sys;print(json.load(sys.stdin)[\"orchestrator\"])")" = OFFLINE ]'
  check "console session still up" tmux has-session -t =magi
  [ "$(jget listener)" = LISTEN ] && pass "listener still listening offline" || fail "listener: $(jget listener)"
  voice_loop
  ;;
esac
echo
[ "$fails" = 0 ] && echo "RESULT: PASS" || echo "RESULT: FAIL ($fails)"
exit $(( fails > 0 ))
