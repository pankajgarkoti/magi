#!/usr/bin/env bash
# mAGI event bus, agent browser and browser-bus test. Run in the distro test VM as the desktop
# user inside a mAGI session (ssh is fine), after `test-agent.sh setup` has turned the
# orchestrator on with the stub agent (no account needed).
#
#   test-bus.sh            units, core tmux windows, loopback-only debug port, MCP config, bus
#                          round trip + delivery into the orchestrator, browser-bus probe event,
#                          doctor, latencies and memory
#   test-bus.sh reboot     (after a reboot) windows, browser and browser-bus came back
set -u
export PATH=/usr/bin:$PATH XDG_RUNTIME_DIR=/run/user/$(id -u)
export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus
T=/var/tmp/magi-test
mkdir -p "$T"
fails=0
pass() { printf 'PASS  %s\n' "$*"; }
fail() { printf 'FAIL  %s\n' "$*"; fails=$((fails + 1)); }
info() { printf 'INFO  %s\n' "$*"; }
check() { local what=$1; shift; if "$@" >/dev/null 2>&1; then pass "$what"; else fail "$what"; fi; }
waitfor() { local n=$1; shift; for _ in $(seq 1 "$n"); do "$@" >/dev/null 2>&1 && return 0; sleep 1; done; return 1; }
wins() { tmux list-windows -t =magi -F '#W' 2>/dev/null; }
port_up() { curl -s --max-time 2 http://127.0.0.1:9222/json/version | grep -q webSocketDebuggerUrl; }
bb_connected() { python3 -c 'import json,os,sys; d=json.load(open(os.path.expanduser("~/.local/state/browser-bus/status.json"))); sys.exit(0 if d.get("connected") else 1)'; }

common() {
  for u in orch-bus.service browser-bus.service; do check "unit $u active" systemctl --user is-active "$u"; done
  for u in orch-doctor.timer orch-open-threads.timer; do check "timer $u active" systemctl --user is-active "$u"; done
  for w in orchestrator bus chrome; do check "tmux window $w in session magi" waitfor 60 bash -c "tmux list-windows -t =magi -F '#W' | grep -qx $w"; done
  check "Chromium answers on 127.0.0.1:9222" waitfor 90 port_up
  local listen; listen=$(ss -Hltn 'sport = :9222' | awk '{print $4}' | sort -u | tr '\n' ' ')
  if [ -n "$listen" ] && ! grep -qvE '^(127\.0\.0\.1|\[::1\]):9222$' <<< "$(tr ' ' '\n' <<< "$listen" | sed '/^$/d')"; then
    pass "debug port bound to loopback only ($listen)"
  else
    fail "debug port listeners: ${listen:-none}"
  fi
  check "the browser in window chrome is Chromium" bash -c "curl -s http://127.0.0.1:9222/json/version | grep -q '\"Browser\": \"Chrome/'"
  check "Chromium profile is ~/.config/chromium-agent" test -d ~/.config/chromium-agent/Default
  check "browser-bus connected to the agent browser" waitfor 60 bb_connected
  check "default probe unread-titles installed" test -f ~/.config/browser-bus/probes/unread-titles.js
}

if [ "${1:-}" = reboot ]; then
  info "boot: $(systemd-analyze 2>/dev/null | head -1)"
  common
  check "orch doctor: OK after reboot" orch doctor
  echo; [ $fails = 0 ] && echo "RESULT: PASS" || echo "RESULT: FAIL ($fails)"
  exit $fails
fi

info "image: $(cat /usr/share/magi/IMAGE_VERSION) · $(. /usr/lib/os-release; echo "$PRETTY_NAME")"
info "boot: $(systemd-analyze 2>/dev/null | head -1)"
common
check "chrome-devtools MCP server in the agent's config (~/.claude.json)" \
  python3 -c 'import json,os,sys; d=json.load(open(os.path.expanduser("~/.claude.json"))); a=d["mcpServers"]["chrome-devtools"]["args"]; sys.exit(0 if "--browserUrl=http://127.0.0.1:9222" in a else 1)'

# ---- bus: emit / unread round trip
orch-bus unread >/dev/null
orch-bus emit test-bus "round trip $$"
check "orch-bus emit -> unread" bash -c "orch-bus unread | grep -q 'test-bus: round trip $$'"
orch-bus emit test-bus "dedupe $$" --key "k$$"; orch-bus emit test-bus "dedupe $$" --key "k$$"
check "orch-bus --key drops the repeat" bash -c "[ \$(grep -c 'dedupe $$' ~/.local/state/orchestrator/events.jsonl) = 1 ]"

# ---- delivery into the idle orchestrator (the stub logs every line it receives)
LOG=$(ls -t ~/.magi-stub/*.log 2>/dev/null | head -1)
lat=()
for i in 1 2 3 4 5; do
  tag="lat$$-$i"
  t0=$(date +%s.%N)
  orch-bus emit test-bus "$tag"
  if waitfor 60 grep -q "received: \[event bus\].*$tag" "$LOG"; then
    t1=$(date +%s.%N); lat+=("$(python3 -c "print(round($t1-$t0,2))")")
  fi
  sleep 2
done
if [ ${#lat[@]} = 5 ]; then pass "bus events typed into the idle orchestrator as [event bus] lines (5/5)"
else fail "delivery: ${#lat[@]}/5 arrived"; fi
info "emit -> orchestrator input latency, s (includes the 3 s coalescing wait and the 1 s poll): ${lat[*]}"

# ---- browser-bus: a probe event from a real page reaches the bus
browser-bus probe add /usr/share/magi/agent/browser-bus/examples/page-title.js
cat > "$T/index.html" <<'EOF'
<html><head><title>start</title></head><body><script>
let n = 0; setInterval(() => { n++; document.title = `build ${n} t=${Date.now()}`; }, 4000);
</script></body></html>
EOF
(cd "$T" && exec python3 -m http.server 8765 --bind 127.0.0.1 >/dev/null 2>&1) &
srv=$!
sleep 2
tab=$(curl -s -X PUT "http://127.0.0.1:9222/json/new?http://127.0.0.1:8765/index.html" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
since=$(date +%s)
newev() { python3 -c "import json,os,sys; sys.exit(0 if any(json.loads(l).get('at',0)>=$since for l in open(os.path.expanduser('~/.local/state/orchestrator/events.jsonl')) if '\"page-title\"' in l) else 1)"; }
if waitfor 30 newev; then
  pass "browser-bus: page-title probe event on the bus: $(grep '"source": "page-title"' ~/.local/state/orchestrator/events.jsonl | tail -1 | cut -c1-120)"
else
  fail "browser-bus: no page-title event"; browser-bus status | head -20
fi
sleep 20
python3 - <<'PY'
import json, os, re, time
ev = [e for e in (json.loads(l) for l in open(os.path.expanduser("~/.local/state/orchestrator/events.jsonl")) if '"page-title"' in l) if e["at"] > time.time() - 60]
d = [round(e["at"] - int(m.group(1)) / 1000, 3) for e in ev if (m := re.search(r"t=(\d+)", e["text"]))]
d = sorted(d)
if d:
    print(f"INFO  page title change -> bus event latency, s: n={len(d)} min={d[0]} median={d[len(d)//2]} max={d[-1]}")
PY
check "browser-bus status lists the tab" bash -c "browser-bus status | grep -q 127.0.0.1:8765"
check "probe add registered it as a ledger watcher (orch watches)" bash -c "orch watches | grep -q probe:page-title"
curl -s "http://127.0.0.1:9222/json/close/$tab" >/dev/null
browser-bus probe rm page-title >/dev/null
kill $srv 2>/dev/null

# ---- ledger and doctor
orch thread add "test thread $$" </dev/null >/dev/null
NOTE=$(orch note)
check "orch thread add -> today's daily note + count" bash -c "grep -q 'test thread $$' $NOTE && [ \$(orch thread count) -ge 1 ]"
sed -i "s/- \[ \] \(.*test thread $$\)/- [x] \1/" "$NOTE"
check "ticking it in the daily note closes it" bash -c "! orch thread scan | grep -q 'test thread $$'"
check "orch doctor: OK" orch doctor
check "orch status prints" bash -c "orch status | grep -q 'ORCH STATUS'"

# ---- resources
rss() { ps -o rss= -p "$1" 2>/dev/null | awk '{s+=$1} END {printf "%.0f", s/1024}'; }
bbpid=$(systemctl --user show -p MainPID --value browser-bus.service)
bspid=$(systemctl --user show -p MainPID --value orch-bus.service)
cr=$(ps -u "$(id -u)" -o rss=,comm= | awk '$2 ~ /^chromium/ {s+=$1} END {printf "%.0f", s/1024}')
info "memory (RSS): browser-bus node $(rss "$bbpid") MB, orch-bus dispatch $(rss "$bspid") MB, Chromium all processes ${cr} MB (one tab)"
info "CPU time so far: browser-bus $(ps -o times= -p "$bbpid") s, orch-bus $(ps -o times= -p "$bspid") s"
info "disk: chromium $(rpm -q --qf '%{size}' chromium | awk '{printf "%.0f", $1/1048576}') MB, nodejs22+npm $(rpm -q --qf '%{size}\n' nodejs22 nodejs22-libs nodejs22-npm | awk '{s+=$1} END {printf "%.0f", s/1048576}') MB installed"

echo; [ $fails = 0 ] && echo "RESULT: PASS" || echo "RESULT: FAIL ($fails)"
exit $fails
