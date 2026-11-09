#!/usr/bin/env bash
# Runs inside the Fedora 44 container (as root); see container-test.sh.
set -euo pipefail
echo keepcache=True >> /etc/dnf/dnf.conf
dnf -y -q install sudo git-core util-linux >/dev/null
useradd -m pilot
echo 'pilot ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/pilot
# the checkout lives where the curl installer would put it
su - pilot -c 'mkdir -p ~/.local/share && cp -a /src ~/.local/share/magi'
rm -rf /home/pilot/.local/share/magi/tests/*.log
# pre-existing user files that MAGI replaces: they must end up in ~/.magi-backup
su - pilot -c 'printf "set -g mouse off\n" > ~/.tmux.conf; mkdir -p ~/.config/waybar ~/.config/mako; echo "{}" > ~/.config/waybar/config.jsonc; echo "font=Sans 9" > ~/.config/mako/config'
ok() { echo "TEST PASS: $*"; }; bad() { echo "TEST FAIL: $*"; FAILS=$((FAILS+1)); }; FAILS=0
M=/home/pilot/.local/share/magi
echo "=================== RUN 1: fresh install"
if su - pilot -c "$M/install.sh" </dev/null; then ok "install.sh run 1 exit 0"; else bad "install.sh run 1 exit $?"; fi
b=$(ls -d /home/pilot/.magi-backup/*/ | head -1)
for f in .tmux.conf .config/waybar/config.jsonc .config/mako/config; do
  [ -f "$b/$f" ] && ok "backed up $f" || bad "no backup of $f"
done
echo "=================== RUN 2: idempotent re-run"
out=$(su - pilot -c "$M/install.sh" </dev/null 2>&1) && ok "install.sh run 2 exit 0" || bad "install.sh run 2 failed"
echo "$out" | tail -25
echo "$out" | grep -q 'backed up' && bad "run 2 backed something up" || ok "run 2 moved nothing"
[ "$(ls -d /home/pilot/.magi-backup/*/ | wc -l)" = 1 ] && ok "still one backup folder" || bad "extra backup folders"
echo "=================== RUN 3: --dry-run"
su - pilot -c "$M/install.sh --dry-run" </dev/null >/tmp/dry.log 2>&1 && ok "dry-run exit 0" || bad "dry-run failed"
tail -5 /tmp/dry.log
echo "=================== RUN 4: magi-update --no-packages (no git remote to pull: uses --dry-run pull path)"
su - pilot -c "cd $M && git remote remove origin 2>/dev/null; MAGI_DIR=$M bash -lc 'magi-update --no-packages --no-wallpapers'" </dev/null >/tmp/upd.log 2>&1 && ok "magi-update exit 0" || { bad "magi-update failed"; tail -20 /tmp/upd.log; }
tail -4 /tmp/upd.log
echo "=================== RUN 5: uninstall + restore"
su - pilot -c "$M/uninstall.sh" </dev/null >/tmp/un.log 2>&1 && ok "uninstall exit 0" || { bad "uninstall failed"; cat /tmp/un.log; }
grep -q 'mouse off' /home/pilot/.tmux.conf && [ ! -L /home/pilot/.tmux.conf ] && ok "original ~/.tmux.conf restored" || bad "~/.tmux.conf not restored"
grep -q 'Sans 9' /home/pilot/.config/mako/config && ok "original mako config restored" || bad "mako config not restored"
left=$(find /home/pilot -lname "*/.local/share/magi/*" | wc -l); [ "$left" = 0 ] && ok "no MAGI links left" || bad "$left MAGI links left"
echo "=================== RUN 6: reinstall after uninstall"
su - pilot -c "$M/install.sh" </dev/null >/tmp/re.log 2>&1 && ok "reinstall exit 0" || { bad "reinstall failed"; tail -30 /tmp/re.log; }
tail -3 /tmp/re.log
echo "links: $(find /home/pilot -lname "*/.local/share/magi/*" | wc -l)"
echo "=================== RESULT: $FAILS failure(s)"
[ "$FAILS" = 0 ]
