#!/usr/bin/env bash
# MAGI agent layer test (run in the distro test VM as the desktop user, inside a MAGI session;
# ssh is fine). Uses tests/agent/claude-stub instead of Claude Code: no account needed.
#
#   test-agent.sh setup      install (stub installer) -> on -> sign-in -> orchestrator up ->
#                            listener up -> voice loop with no mic -> bar status -> snapshot
#   test-agent.sh reboot     (after a reboot) session back, orchestrator resumed in window 1,
#                            listener up, bar status
#   test-agent.sh voice      the voice loop alone
#   test-agent.sh offline    (with the VM's network link down) listener + console still up,
#                            bar says OFFLINE
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
LOG=~/.claude/stub-received.log
win1() { tmux list-windows -t =magi -F '#{window_index} #W' 2>/dev/null | awk '$1==1{print $2}'; }
status() { magi-agent status --json; }
jget() { status | python3 -c "import json,sys; print(json.load(sys.stdin)['$1'])"; }

voice_loop() {
  local sentence="Open the control centre and switch to the red theme."
  n0=$(grep -c "received: \[voice\]" "$LOG" 2>/dev/null || echo 0)
  paplay --device=magi_test "$T/say.wav" 2>/dev/null || {
    "$VOICE_PY" -m piper --model /usr/lib/magi/voice/voices/en_US-ljspeech-medium.onnx --output_file "$T/say.wav" <<< "$sentence" >/dev/null 2>&1
    sleep 1; paplay --device=magi_test "$T/say.wav"; }
  if waitfor 40 bash -c "[ \$(grep -ci 'received: \[voice\].*control cent' '$LOG') -gt $n0 ]"; then
    pass "voice loop: spoken into the null sink -> whisper -> orchestrator pane: $(grep -i 'received: \[voice\]' "$LOG" | tail -1 | cut -d' ' -f3-)"
  else
    fail "voice loop: nothing arrived in the orchestrator (heard.log: $(tail -2 ~/.local/state/magi/agent/heard.log 2>/dev/null | tr '\n' ' '))"
  fi
}

case "${1:-setup}" in
setup)
  . /usr/share/magi/agent/bin/magi-voice-env
  check "Claude Code not installed yet" bash -c '! command -v claude'
  out=$(MAGI_CLAUDE_INSTALL_URL=file://$T/install-stub.sh CLAUDE_STUB=$T/claude-stub magi-agent install-claude 2>&1)
  check "install-claude ran the installer it was pointed at" test -L ~/.local/bin/claude
  # the mic: a null sink, so the test can "speak" into it (test only)
  pactl load-module module-null-sink sink_name=magi_test sink_properties=device.description=magi-test >/dev/null
  mkdir -p ~/.config/magi && printf 'SOURCE=magi_test.monitor\n' >> ~/.config/magi/agent.conf
  magi-agent on
  check "magi-agents.service enabled for every login" systemctl --user is-enabled magi-agents.service
  check "tmux-snapshot.timer + tmux-reap-stale.timer enabled" systemctl --user is-enabled tmux-snapshot.timer tmux-reap-stale.timer
  check "tmux session magi exists" waitfor 10 tmux has-session -t =magi
  [ "$(win1)" = orchestrator ] && pass "window 1 is the orchestrator" || fail "window 1 is '$(win1)'"
  check "orchestrator signs in (stub auth login) and starts" waitfor 30 grep -q "start argv=" "$LOG"
  grep "start argv=" "$LOG" | tail -1 | grep -q -- "'--append-system-prompt-file', '/usr/share/magi/agent/ORCHESTRATOR.md'" &&
    pass "orchestrator runs with MAGI's ORCHESTRATOR.md" || fail "orchestrator argv: $(grep 'start argv=' "$LOG" | tail -1)"
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
  tmux-snapshot
  check "tmux layout snapshot saved" python3 -c "import json,os; d=json.load(open(os.path.expanduser('~/.local/state/tmux-snapshot/layout.json'))); assert any(p.get('kind')=='claude' for w in d['windows'] for p in w['panes'])"
  sid=$(python3 -c "import json,os; d=json.load(open(os.path.expanduser('~/.local/state/tmux-snapshot/layout.json'))); print([p['session_id'] for w in d['windows'] for p in w['panes'] if p.get('kind')=='claude'][0])")
  echo "$sid" > "$T/orchestrator-session-id"
  echo "orchestrator session id: $sid"
  ;;
reboot)
  sid=$(cat "$T/orchestrator-session-id" 2>/dev/null)
  pactl load-module module-null-sink sink_name=magi_test >/dev/null 2>&1   # the test "mic" is gone after a reboot
  check "tmux session magi back after reboot (no terminal opened)" waitfor 60 tmux has-session -t =magi
  [ "$(win1)" = orchestrator ] && pass "window 1 is the orchestrator" || fail "window 1 is '$(win1)'"
  check "orchestrator resumed: claude --resume $sid" waitfor 30 grep -q "start argv=.*'--resume', '$sid'" "$LOG"
  check "listener up after reboot" waitfor 60 grep -q "listening on" <(tail -5 ~/.local/state/magi/agent/heard.log)
  check "bar status: orchestrator IDLE" waitfor 20 bash -c '[ "$(magi-agent status --json | python3 -c "import json,sys;print(json.load(sys.stdin)[\"orchestrator\"])")" = IDLE ]'
  check "tmux server lives in magi-agents.service, not in the desktop session" \
    bash -c "grep -q magi-agents.service /proc/\$(tmux display-message -p '#{pid}')/cgroup"
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
