#!/usr/bin/env bash
# Agent adapter test: drives the MAGI agent layer with a stub of each agent (tests/agent/agent-stub)
# through install, sign-in, launch with the orchestrator instructions, IDLE / WORKING / ASKING in
# the bar module and the AGENTS page data, sub-agents, snapshot -> tmux restart -> restore with
# resume, and the idle reaper (busy, asking and a typed draft are never closed).
#
#   tests/agent/test-adapters.sh [claude opencode codex custom]   (default: all four)
#
# Runs anywhere with tmux and python3, in a throwaway HOME with its own tmux server; it does not
# touch your tmux, systemd units or agent configs. No account needed.
set -u
REPO=$(cd "$(dirname "$0")/../.." && pwd)
STUB=$REPO/tests/agent/agent-stub
fails=0 total=0
pass() { total=$((total + 1)); printf '  PASS  %s\n' "$*"; }
fail() { total=$((total + 1)); fails=$((fails + 1)); printf '  FAIL  %s\n' "$*"; }
check() { local what=$1; shift; if "$@" >/dev/null 2>&1; then pass "$what"; else fail "$what"; fi; }
waitfor() { local n=$1; shift; for _ in $(seq 1 $((n * 4))); do "$@" >/dev/null 2>&1 && return 0; sleep 0.25; done; return 1; }

orch() { magi-agent status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["orchestrator"])'; }
is_orch() { [ "$(orch)" = "$1" ]; }
win_state() { magi-agent status --json | python3 -c "import json,sys; print(next((w['state'] for w in json.load(sys.stdin)['windows'] if w['name']=='$1'), 'none'))"; }
is_win() { [ "$(win_state "$1")" = "$2" ]; }
starts() { grep -c "start argv=" "$LOG" 2>/dev/null || echo 0; }
send() { tmux send-keys -t "=magi:=$1" -l "$2"; sleep 0.3; tmux send-keys -t "=magi:=$1" Enter; }
has_win() { tmux list-windows -t =magi -F '#W' 2>/dev/null | grep -qx "$1"; }

run_adapter() {
  local name=$1
  echo "== $name"
  T=$(mktemp -d "${TMPDIR:-/tmp}/magi-adapter-$name.XXXXXX")
  export HOME=$T/home XDG_CONFIG_HOME=$T/home/.config XDG_STATE_HOME=$T/home/.local/state \
    XDG_DATA_HOME=$T/home/.local/share XDG_CACHE_HOME=$T/home/.cache XDG_RUNTIME_DIR=$T/run
  # a short path: unix socket paths are limited to ~108 bytes
  TMUX_TMPDIR=$(mktemp -d /tmp/magi-tmux.XXXXXX); export TMUX_TMPDIR
  unset TMUX TMUX_PANE DBUS_SESSION_BUS_ADDRESS CODEX_HOME
  mkdir -p "$HOME/proj" "$XDG_RUNTIME_DIR" "$T/bin" "$XDG_CONFIG_HOME/magi"
  chmod 700 "$XDG_RUNTIME_DIR"
  # never reach the real user manager
  printf '#!/bin/sh\nexit 1\n' > "$T/bin/systemctl"; chmod +x "$T/bin/systemctl"
  export PATH=$T/bin:$REPO/agent/bin:$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
  LOG=$HOME/.magi-stub/$name.log
  printf 'ENABLED=1\nVOICE=0\nCONSOLE_AT_LOGIN=0\n' > "$XDG_CONFIG_HOME/magi/agent.conf"
  export MAGI_EVENT_SETTLE=3
  echo "Fix the failing test in proj." > "$T/brief.txt"

  if [ "$name" = custom ]; then
    cp "$STUB" "$T/bin/myagent"
    magi-agent use custom "$T/bin/myagent --flag" >/dev/null
    printf "AGENT_BUSY_RE='working\\\\.\\\\.\\\\.'\nAGENT_PROMPT='> '\n" >> "$XDG_CONFIG_HOME/magi/agent.conf"
    check "use custom: AGENT_CMD keeps its spaces" bash -c "[ \"\$(python3 $REPO/agent/lib/magi_agents.py info | python3 -c 'import json,sys; print(json.load(sys.stdin)[\"path\"])')\" = $T/bin/myagent ]"
  else
    magi-agent use "$name" >/dev/null
    check "status SETUP before $name is installed" is_orch SETUP
    up=$(tr a-z A-Z <<< "$name")
    env "MAGI_${up}_INSTALL_URL=file://$REPO/tests/agent/install-stub.sh" STUB_AGENT="$name" AGENT_STUB="$STUB" \
      magi-agent install > "$T/install.log" 2>&1
    check "magi-agent install ran the $name installer it was pointed at" python3 "$REPO/agent/lib/magi_agents.py" installed
    if [ "$name" != opencode ]; then
      check "$name not signed in yet" bash -c "! python3 $REPO/agent/lib/magi_agents.py signed-in"
    fi
  fi

  magi-agent start
  tmux has-session -t =magi 2>/dev/null || { fail "tmux session magi did not start"; return; }
  check "tmux session magi, window 1 is the orchestrator" bash -c "[ \"\$(tmux list-windows -t =magi -F '#{window_index} #W' | awk '\$1==1{print \$2}')\" = orchestrator ]"
  check "orchestrator signs in if needed and starts $name" waitfor 20 grep -q "start argv=" "$LOG"
  local first; first=$(grep "start argv=" "$LOG" | head -1)
  if [ "$name" = claude ]; then
    grep -q -- "'--append-system-prompt-file', '$REPO/agent/ORCHESTRATOR.md'" <<< "$first" &&
      pass "orchestrator gets ORCHESTRATOR.md (--append-system-prompt-file)" || fail "argv: $first"
  else
    grep -q "agents_md=# MAGI orchestrator" <<< "$first" &&
      pass "orchestrator runs in a folder whose AGENTS.md is ORCHESTRATOR.md" || fail "start: $first"
  fi
  [ "$name" = custom ] && { grep -q "argv=\['--flag'\]" <<< "$first" && pass "custom command runs with its arguments" || fail "argv: $first"; }

  check "bar: orchestrator IDLE" waitfor 10 is_orch IDLE
  send orchestrator "work 5"
  check "bar: orchestrator WORKING while it works" waitfor 4 is_orch WORKING
  check "bar: back to IDLE when done" waitfor 10 is_orch IDLE
  send orchestrator "ask"
  check "bar: orchestrator ASKING on a permission prompt" waitfor 5 is_orch ASKING
  check "waybar class is 'asking'" bash -c 'magi-agent status --waybar | grep -q "\"class\": \"asking\""'
  send orchestrator "y"
  check "answered: back to IDLE" waitfor 6 is_orch IDLE

  magi-agent spawn sub1 "$HOME/proj" "$T/brief.txt" > /dev/null
  check "spawn: sub-agent window sub1 started" waitfor 10 bash -c "[ \$(grep -c 'start argv=' '$LOG') -ge 2 ]"
  if [ "$name" = custom ]; then
    check "spawn: brief typed into the custom agent" waitfor 15 grep -q "received: Read the brief in $T/brief.txt" "$LOG"
  else
    check "spawn: brief passed to $name at launch" grep -q "start argv=.*Read the brief in $T/brief.txt" "$LOG"
  fi
  check "AGENTS data: sub1 listed as $name, idle" waitfor 10 bash -c "magi-agent status --json | python3 -c \"import json,sys; d=json.load(sys.stdin); w=[w for w in d['windows'] if w['name']=='sub1']; assert w and w[0]['agent']=='$name' and w[0]['state']=='idle' and d['agents']==1\""
  check "bar text: ◉ IDLE · 1 AGENT" bash -c 'magi-agent status --waybar | grep -q "◉ IDLE · 1 AGENT "'
  send sub1 "work 4"
  check "AGENTS data: sub1 busy" waitfor 4 is_win sub1 busy
  check "bar class busy-agent doesn't change orchestrator IDLE" is_orch IDLE
  check "AGENTS data: sub1 idle again" waitfor 10 is_win sub1 idle
  check "spawn's watcher: the orchestrator gets '[magi] window sub1 ... finished'" \
    waitfor 20 grep -q "received: \[magi\] window sub1 ($name) finished and is idle" "$LOG"
  check "watcher message names the pane to read" grep -q "received: \[magi\] window sub1 .*capture-pane -p -t %" "$LOG"
  check "orchestrator IDLE after answering it" waitfor 10 is_orch IDLE
  send sub1 "ask"
  check "spawn's watcher: '[magi] window sub1 ... waiting on a prompt'" \
    waitfor 20 grep -q "received: \[magi\] window sub1 ($name) is waiting on a prompt" "$LOG"
  send sub1 "n"; waitfor 6 is_win sub1 idle

  # snapshot -> tmux restart -> restore
  tmux-snapshot
  local L=$HOME/.local/state/tmux-snapshot/layout.json
  check "snapshot saved the orchestrator as kind=orchestrator, agent=$name" python3 -c "import json; d=json.load(open('$L')); assert any(p.get('kind')=='orchestrator' and p['agent']=='$name' for w in d['windows'] for p in w['panes'])"
  check "snapshot saved sub1 as kind=agent" python3 -c "import json; d=json.load(open('$L')); assert any(p.get('kind')=='agent' and w['name']=='sub1' for w in d['windows'] for p in w['panes'])"
  local osid ssid
  osid=$(python3 -c "import json; d=json.load(open('$L')); print([p.get('session_id') for w in d['windows'] for p in w['panes'] if p.get('kind')=='orchestrator'][0])")
  ssid=$(python3 -c "import json; d=json.load(open('$L')); print([p.get('session_id') for w in d['windows'] for p in w['panes'] if w['name']=='sub1'][0])")
  echo "        orchestrator session: $osid   sub1 session: $ssid"
  local n0; n0=$(starts)
  tmux kill-server; rm -f "$XDG_RUNTIME_DIR/tmux-restored"; sleep 1
  magi-agent start
  check "restore: both agents started again" waitfor 20 bash -c "[ \$(grep -c 'start argv=' '$LOG') -ge $((n0 + 2)) ]"
  check "restore: window 1 is the orchestrator" bash -c "[ \"\$(tmux list-windows -t =magi -F '#{window_index} #W' | awk '\$1==1{print \$2}')\" = orchestrator ]"
  local after; after=$(tail -n +$((n0 + 1)) "$LOG" | grep "start argv=")
  case "$name" in
    claude)   grep -q "'--resume', '$osid'" <<< "$after" && grep -q "'--resume', '$ssid'" <<< "$after" && pass "restore: claude --resume <same ids>" || fail "restore argv: $after" ;;
    opencode) grep -q "'--session', '$osid'" <<< "$after" && grep -q "'--session', '$ssid'" <<< "$after" && pass "restore: opencode --session <same ids>" || fail "restore argv: $after" ;;
    codex)    grep -q "\['resume', '$osid'" <<< "$after" && grep -q "\['resume', '$ssid'" <<< "$after" && pass "restore: codex resume <same ids>" || fail "restore argv: $after" ;;
    custom)   [ "$osid" = None ] && ! grep -q resume <<< "$after" && pass "restore: custom agent (no resume) started fresh" || fail "restore: sid=$osid $after" ;;
  esac
  check "orchestrator IDLE after restore" waitfor 10 is_orch IDLE
  [ "$name" = claude ] || check "restored orchestrator still reads AGENTS.md" bash -c "tail -n +$((n0 + 1)) '$LOG' | grep 'start argv=' | grep -q 'agents_md=# MAGI orchestrator'"

  # reaper
  check "sub1 idle before reaping" waitfor 10 is_win sub1 idle
  send sub1 "work 6"; waitfor 4 is_win sub1 busy
  REAP_IDLE_MIN=0 tmux-reap-stale >/dev/null
  check "reaper: busy sub1 left alone" has_win sub1
  waitfor 10 is_win sub1 idle
  send sub1 "ask"; waitfor 5 is_win sub1 asking
  REAP_IDLE_MIN=0 tmux-reap-stale >/dev/null
  check "reaper: asking sub1 left alone" has_win sub1
  send sub1 "n"; waitfor 6 is_win sub1 idle
  if [ "$name" != opencode ]; then
    tmux send-keys -t =magi:=sub1 -l "half typed"; sleep 0.5
    REAP_IDLE_MIN=0 tmux-reap-stale >/dev/null
    check "reaper: sub1 with a typed draft left alone" has_win sub1
    tmux send-keys -t =magi:=sub1 C-u; sleep 0.5
  fi
  REAP_IDLE_MIN=60 tmux-reap-stale >/dev/null
  check "reaper: idle under the limit left alone" has_win sub1
  REAP_IDLE_MIN=0 tmux-reap-stale >/dev/null
  check "reaper: idle sub1 closed" bash -c "! tmux list-windows -t =magi -F '#W' | grep -qx sub1"
  check "reaper: orchestrator is exempt" has_win orchestrator
  local cl; cl=$(tail -1 "$HOME/.local/state/tmux-reap/closed.log" 2>/dev/null)
  case "$name" in
    custom) grep -q "can't resume" <<< "$cl" && pass "reaper log: says the custom agent starts fresh" || fail "log: $cl" ;;
    *)      grep -q "resume: cd .* && [^ ]*$name .*$ssid" <<< "$cl" && pass "reaper log has the resume command" || fail "log: $cl" ;;
  esac

  tmux kill-server 2>/dev/null
  [ -f "$XDG_STATE_HOME/opencode/service.json" ] && kill "$(python3 -c "import json;print(json.load(open('$XDG_STATE_HOME/opencode/service.json'))['pid'])")" 2>/dev/null
  [ "${KEEP:-0}" = 1 ] || rm -rf "$T" "$TMUX_TMPDIR"
}

for a in "${@:-claude opencode codex custom}"; do
  for b in $a; do run_adapter "$b"; done
done
echo
[ "$fails" = 0 ] && echo "RESULT: PASS ($total checks)" || echo "RESULT: FAIL ($fails of $total)"
exit $(( fails > 0 ))
