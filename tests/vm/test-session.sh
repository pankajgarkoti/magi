#!/usr/bin/env bash
# mAGI 0.3 terminal session and notes test. Run in the distro test VM as the desktop user,
# inside a mAGI session (ssh is fine), after a fresh install and login.
#
#   test-session.sh            login opened Alacritty attached to tmux "main"; tmux and Alacritty
#                              config applied (plugins offline); a second terminal reattaches;
#                              notes repo, daily note, threads, ledger, system/ links; then
#                              prepares a window to be restored (run "reboot" after rebooting)
#   test-session.sh reboot     the prepared window came back in "main" with its folder and
#                              program; a terminal is attached again; the daily note still there
set -u
export PATH=/usr/bin:$PATH XDG_RUNTIME_DIR=/run/user/$(id -u)
export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus
export HYPRLAND_INSTANCE_SIGNATURE=$(ls -t "$XDG_RUNTIME_DIR/hypr" 2>/dev/null | head -1)
T=/var/tmp/magi-test
mkdir -p "$T"
fails=0
pass() { printf 'PASS  %s\n' "$*"; }
fail() { printf 'FAIL  %s\n' "$*"; fails=$((fails + 1)); }
info() { printf 'INFO  %s\n' "$*"; }
check() { local what=$1; shift; if "$@" >/dev/null 2>&1; then pass "$what"; else fail "$what"; fi; }
waitfor() { local n=$1; shift; for _ in $(seq 1 "$n"); do "$@" >/dev/null 2>&1 && return 0; sleep 1; done; return 1; }
clients_main() { tmux list-clients -t =main -F '#{client_pid}' 2>/dev/null | grep -c .; }
alacritty_on() { hyprctl -j clients | python3 -c "import json,sys; sys.exit(0 if any(c['class']=='Alacritty' and c['workspace']['name']=='$1' for c in json.load(sys.stdin)) else 1)"; }
note() { echo ~/notes/Journal/$(date +%Y/%m/%Y-%m-%d).md; }

common() {
  check "Alacritty open on workspace 1 after login" waitfor 60 alacritty_on 1
  check "tmux session main exists" waitfor 30 tmux has-session -t =main
  check "a terminal is attached to main" waitfor 30 bash -c '[ "$(tmux list-clients -t =main 2>/dev/null | grep -c .)" -ge 1 ]'
  check "today's daily note exists" test -s "$(note)"
}

if [ "${1:-}" = reboot ]; then
  info "boot: $(systemd-analyze 2>/dev/null | head -1)"
  common
  check "tmux-restore.service ran this boot" bash -c 'systemctl --user is-active tmux-restore.service | grep -qx active'
  check "window 'work' restored in main" bash -c "tmux list-windows -t =main -F '#W' | grep -qx work"
  check "  ... in its folder (/var/tmp/magi-test/workdir-$USER)" bash -c "tmux list-panes -t =main:=work -F '#{pane_current_path}' | grep -qx /var/tmp/magi-test/workdir-$USER"
  # (the restored pane runs "tail ...; exec bash" in a shell, so tail is that shell's child)
  check "  ... running its program again (tail)" waitfor 20 bash -c "pgrep -P \$(tmux list-panes -t =main:=work -F '#{pane_pid}') -x tail"
  check "window 'scratch' restored in main as a shell" bash -c "tmux list-windows -t =main -F '#W' | grep -qx scratch"
  check "the open thread survived the reboot" bash -c "orch thread scan | grep -q 'session test thread'"
  check "the status panel window came back running the panel" waitfor 20 bash -c "tmux capture-pane -p -t magi-status | grep -q 'mAGI STATUS'"
  info "restore log: $(tail -6 ~/.local/state/tmux-snapshot/restore.log | tr '\n' ' ' | cut -c1-300)"
  echo; [ $fails = 0 ] && echo "RESULT: PASS" || echo "RESULT: FAIL ($fails)"
  exit $fails
fi

info "image: $(cat /usr/share/magi/IMAGE_VERSION) · $(. /usr/lib/os-release; echo "$PRETTY_NAME")"
info "boot: $(systemd-analyze 2>/dev/null | head -1)"
common
for u in tmux-snapshot.timer voice-digest.timer orch-doctor.timer; do check "unit $u active" systemctl --user is-active "$u"; done
check "tmux-restore.service enabled" bash -c 'systemctl --user is-enabled tmux-restore.service | grep -qx enabled'

# ---- config applied, plugins offline
check "tmux history-limit 100000 (owner config)" bash -c "tmux show -gv history-limit | grep -qx 100000"
check "tmux mouse on" bash -c "tmux show -gv mouse | grep -qx on"
check "tmux Alt binds (M-c new-window)" bash -c "tmux list-keys -T root M-c | grep -q new-window"
check "dracula status bar loaded (status-right set by the plugin)" bash -c "tmux show -gv status-right | grep -q ."
check "tmux-sensible present in the image copy" test -f ~/.tmux/plugins/tmux-sensible/sensible.tmux
check "no plugin download needed (no git dir under ~/.tmux/plugins/tmux-sensible)" bash -c '! test -d ~/.tmux/plugins/tmux-sensible/.git'
check "Alacritty config linked (font size 12, Iosevka)" bash -c "grep -q '^size = 12' ~/.config/alacritty.toml && grep -q 'IosevkaTerm' ~/.config/alacritty.toml"

# ---- a second terminal reattaches to the same session
before=$(tmux list-sessions -F '#S' | sort | tr '\n' ' ')
n0=$(clients_main)
hyprctl dispatch 'hl.dsp.exec_cmd("magi-terminal")' >/dev/null
if waitfor 20 bash -c "[ \$(tmux list-clients -t =main | grep -c .) -gt $n0 ]"; then
  pass "a second terminal attached to the same session main ($n0 -> $(clients_main) clients)"
else fail "second terminal: clients $n0 -> $(clients_main)"; fi
after=$(tmux list-sessions -F '#S' | sort | tr '\n' ' ')
[ "$before" = "$after" ] && pass "no new tmux session was created ($after)" || fail "sessions: '$before' -> '$after'"

# ---- notes: repo, daily note, threads, ledger, system links
check "~/notes is a git repository" test -d ~/notes/.git
check "daily note from the template (# Tasks / # Notes / # Logs)" bash -c "grep -qx '# Tasks' $(note) && grep -qx '# Notes' $(note) && grep -qx '# Logs' $(note)"
orch thread add "session test thread $$" </dev/null >/dev/null
check "orch thread add -> '## Open threads' in today's note" bash -c "sed -n '/^## Open threads/,/^#/p' $(note) | grep -q 'session test thread $$'"
orch decide "test decision $$" >/dev/null
check "orch decide -> '# Logs' in today's note" bash -c "sed -n '/^# Logs/,/^# /p' $(note) | grep -q 'decision: test decision $$'"
check "ledger in the notes (orch.db)" test -s ~/notes/orchestrator/orch.db
check "~/.local/bin/orch -> ~/notes/orchestrator/system" bash -c "readlink -f ~/.local/bin/orch | grep -q /notes/orchestrator/system/agent/bin/orch"
check "~/.config/systemd/user/orch-doctor.timer -> ~/notes/orchestrator/system" bash -c "readlink -f ~/.config/systemd/user/orch-doctor.timer | grep -q /notes/orchestrator/system/"
check "system/install.sh and units.json present" bash -c "test -x ~/notes/orchestrator/system/install.sh && test -s ~/notes/orchestrator/system/units.json"
orch doctor >/dev/null 2>&1
check "orch doctor commits the notes (clean tree afterwards)" bash -c "[ -z \"\$(git -C ~/notes status --porcelain)\" ]"
info "notes commits: $(git -C ~/notes log --format=%s | tr '\n' '|' | cut -c1-200)"
# voice digest: one fake utterance, no agent installed -> raw lines in # Notes
mkdir -p ~/.local/state/magi/voice/transcripts
python3 -c "import json,datetime as d,os; n=d.datetime.now().astimezone(); open(os.path.expanduser('~/.local/state/magi/voice/transcripts/%s.jsonl' % n.strftime('%Y-%m-%d')),'a').write(json.dumps({'at': (n-d.timedelta(minutes=2)).isoformat(timespec='seconds'), 'text': 'remind me to water the plants'})+'\n')"
orch voice-digest >/dev/null
check "voice digest wrote to today's note" bash -c "grep -q 'water the plants' $(note)"

# ---- status panel and ALT + M (0.3.1)
active_ok() { hyprctl -j activewindow | python3 -c "import json,sys; c=json.load(sys.stdin); sys.exit(0 if c.get('class') in ('Alacritty','magi-scratch') else 1)"; }
panel_selected() { tmux list-windows -a -F '#{window_name} #{window_active} #{session_attached}' | grep -q '^magi-status 1 [1-9]'; }
check "magi-status --once prints a frame (SYSTEM, mAGI version)" bash -c "magi-status --once --no-color | grep -q \"mAGI .*$(cat /usr/share/magi/IMAGE_VERSION)\""
check "ALT + M is bound to magi-status-show" bash -c "hyprctl -j binds | python3 -c \"import json,sys; sys.exit(0 if any(b['key'].upper()=='M' and b['modmask']==8 for b in json.load(sys.stdin)) else 1)\""
tmux kill-window -t magi-status 2>/dev/null
hyprctl dispatch 'hl.dsp.focus({ workspace = 4 })' >/dev/null
hyprctl dispatch 'hl.dsp.exec_cmd("magi-status-show")' >/dev/null
check "ALT + M created the tmux window magi-status" waitfor 10 bash -c "tmux list-windows -a -F '#W' | grep -qx magi-status"
check "  ... running the panel" waitfor 10 bash -c "tmux capture-pane -p -t magi-status | grep -q 'mAGI STATUS'"
check "  ... selected, in a session a terminal shows" waitfor 10 panel_selected
check "  ... and that terminal has focus (left workspace 4)" waitfor 10 active_ok
info "panel window: $(tmux list-windows -a -F '#S:#I #W' | grep magi-status) · focus: $(hyprctl -j activewindow | python3 -c "import json,sys; c=json.load(sys.stdin); print(c.get('class'), c.get('workspace',{}).get('name'))")"
tmux select-window -t "$(tmux list-windows -a -F '#S:#I #W' | grep magi-status | cut -d' ' -f1 | cut -d: -f1):^"
hyprctl dispatch 'hl.dsp.focus({ workspace = 4 })' >/dev/null
hyprctl dispatch 'hl.dsp.exec_cmd("magi-status-show")' >/dev/null
check "ALT + M again (window exists, other window selected): panel shown right away" waitfor 5 panel_selected
check "  ... terminal focused again" waitfor 5 active_ok
check "orch-thread add / count (same as orch thread)" bash -c "orch-thread add 'orch-thread alias test $$' </dev/null && orch-thread scan | grep -q 'alias test $$'"
check "open-threads reminder at :30 every 3 h" bash -c "systemctl --user show -p TimersCalendar orch-open-threads.timer | grep -q '00/3:30:00'"

# ---- prepare the restore check: a window with a folder and a program, and a plain shell
mkdir -p "$T/workdir-$USER"; echo "line" > "$T/workdir-$USER/log.txt"
tmux kill-window -t =main:=work 2>/dev/null; tmux kill-window -t =main:=scratch 2>/dev/null
tmux new-window -d -t =main: -n work -c "$T/workdir-$USER" "tail -f log.txt"
tmux new-window -d -t =main: -n scratch -c /tmp
tmux set-option -w -t =main:=work automatic-rename off; tmux set-option -w -t =main:=scratch automatic-rename off
tmux-snapshot
check "snapshot has main:work with its command" python3 -c "import json,os; d=json.load(open(os.path.expanduser('~/.local/state/tmux-snapshot/layout.json'))); assert any(w['session']=='main' and w['name']=='work' and w['panes'][0].get('command','').startswith('tail') for w in d['windows'])"

echo; [ $fails = 0 ] && echo "RESULT: PASS (now reboot and run: test-session.sh reboot)" || echo "RESULT: FAIL ($fails)"
exit $fails
