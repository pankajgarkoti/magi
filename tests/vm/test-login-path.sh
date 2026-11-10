#!/usr/bin/env bash
# Regression test: the MAGI tools resolve at login (run in the test VM, as the desktop user,
# after a real reboot and GDM login; ssh is fine, nothing is shown on screen).
#
# Background: GDM starts Hyprland with PATH=/usr/local/bin:/usr/bin. When the MAGI tools were
# only in ~/.local/bin, eva-wifi / magi / magi-overview never started, SUPER+SHIFT+W did
# nothing, and waybar's on-click commands failed (waybar inherits the same PATH).
#
# Checks:
#  1. what Hyprland itself resolves: a command run through Hyprland's exec (the same path a
#     key binding takes) records `command -v` for each tool
#  2. what waybar resolves: each tool looked up with the PATH from waybar's /proc environ
#     (its on-click commands run with exactly that)
#  3. the three resident daemons are running: eva-wifi, magi, magi-overview --daemon
# Exit 0 = all pass. Prints Hyprland's own start-up PATH for the record.
set -u
TOOLS=(eva-wifi magi magi-overview wall-next)
DAEMONS=(eva-wifi magi magi-overview)
U=$(id -u)
RUN=/run/user/$U
fails=0
pass() { printf 'PASS  %s\n' "$*"; }
fail() { printf 'FAIL  %s\n' "$*"; fails=$((fails + 1)); }
env_of() { tr '\0' '\n' < "/proc/$1/environ" 2>/dev/null | sed -n "s/^$2=//p"; }
resolve() {  # resolve PATH TOOL -> path or empty
  local IFS=: d
  for d in $1; do [ -n "$d" ] && [ -x "$d/$2" ] && [ ! -d "$d/$2" ] && { echo "$d/$2"; return; }; done
}

# wait for the session (autologin) to be up
hp= wp=
for _ in $(seq 1 120); do
  hp=$(pgrep -u "$U" -x Hyprland | head -1); wp=$(pgrep -u "$U" -x waybar | head -1)
  [ -n "$hp" ] && [ -n "$wp" ] && break
  sleep 1
done
[ -n "$hp" ] || { fail "no Hyprland process for uid $U"; exit 1; }
[ -n "$wp" ] || fail "waybar is not running"
sleep 5   # let the autostart commands settle
echo "Hyprland pid $hp, start-up PATH (from GDM): $(env_of "$hp" PATH)"
uptime -s | sed 's/^/booted: /'

# 1. Hyprland's exec path
export XDG_RUNTIME_DIR=$RUN
export HYPRLAND_INSTANCE_SIGNATURE=$(env_of "$hp" HYPRLAND_INSTANCE_SIGNATURE)
[ -n "$HYPRLAND_INSTANCE_SIGNATURE" ] || HYPRLAND_INSTANCE_SIGNATURE=$(ls -t "$RUN/hypr" | head -1)
probe=$RUN/magi-path-probe
rm -f "$probe"
cmd="{ echo \"PATH=\$PATH\"; for t in ${TOOLS[*]}; do printf '%s=%s\\n' \"\$t\" \"\$(command -v \$t)\"; done; } > $probe"
hyprctl dispatch "hl.dsp.exec_cmd([[$cmd]])" >/dev/null 2>&1
for _ in $(seq 1 20); do [ -s "$probe" ] && break; sleep 0.5; done
if [ ! -s "$probe" ]; then fail "Hyprland exec probe produced nothing (hyprctl dispatch failed?)"
else
  echo "Hyprland exec PATH: $(sed -n 's/^PATH=//p' "$probe")"
  for t in "${TOOLS[@]}"; do
    p=$(sed -n "s/^$t=//p" "$probe")
    [ -n "$p" ] && pass "Hyprland resolves $t -> $p" || fail "Hyprland cannot resolve $t"
  done
fi

# 2. waybar
if [ -n "$wp" ]; then
  wpath=$(env_of "$wp" PATH)
  echo "waybar PATH: $wpath"
  for t in "${TOOLS[@]}"; do
    p=$(resolve "$wpath" "$t")
    [ -n "$p" ] && pass "waybar resolves $t -> $p" || fail "waybar cannot resolve $t"
  done
fi

# 3. resident daemons
for d in "${DAEMONS[@]}"; do
  if pgrep -u "$U" -f "(^|/)$d --daemon" >/dev/null; then pass "$d --daemon is running"
  else fail "$d --daemon is not running"; fi
done

echo
if [ "$fails" = 0 ]; then echo "RESULT: PASS (all ${#TOOLS[@]} tools resolve for Hyprland and waybar; ${#DAEMONS[@]} daemons up)"
else echo "RESULT: FAIL ($fails check(s) failed)"; fi
exit $(( fails > 0 ))
