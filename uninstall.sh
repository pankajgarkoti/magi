#!/usr/bin/env bash
# MAGI uninstall / rollback: remove MAGI's config links and generated files, then put back
# what install.sh moved to ~/.magi-backup/<timestamp>/ (every backup; the oldest is restored last, so the
# state from before the first install wins). Packages stay installed (see README).
#
#   ./uninstall.sh [--dry-run] [--backup ~/.magi-backup/<timestamp>]
# Backups are copied back, never deleted: remove ~/.magi-backup yourself when done.
set -euo pipefail
DRY=0 ONLY=
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --backup) ONLY=${2:?}; shift ;;
    -h|--help) sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "uninstall.sh: unknown option $1" >&2; exit 2 ;;
  esac
  shift
done
run() { if [ "$DRY" = 1 ]; then printf '[dry-run]'; printf ' %q' "$@"; printf '\n'; else "$@"; fi; }
MAGI_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PK="$MAGI_DIR/packages"
STOW_PACKAGES=(hypr gtk ranger alacritty tmux shell)

echo "==> removing MAGI config links"
run stow --no-folding -d "$PK" -t "$HOME" -D "${STOW_PACKAGES[@]}"

echo "==> removing files MAGI generated"
for f in .config/mako/config .config/wofi/style.css .config/hypr/hyprlock.conf .config/hypr/hyprpaper.conf \
         .config/hypr/hyprtoolkit.conf .config/xsettingsd/xsettingsd.conf .config/hypr/themes/current \
         .cache/eva-lock.png .local/share/themes/EVA .local/share/icons/EVA \
         .local/share/gtksourceview-5/styles/eva.xml .local/state/magi .cache/magi; do
  if [ -e "$HOME/$f" ] || [ -L "$HOME/$f" ]; then run rm -rf "${HOME:?}/$f"; fi
done
# the ~/.bashrc.d loader install.sh may have appended
if grep -qs '^# >>> MAGI' "$HOME/.bashrc"; then
  run sed -i '/^# >>> MAGI/,/^# <<< MAGI/d' "$HOME/.bashrc"
fi
# empty directories left behind by the links (never non-empty ones)
for d in .config/hypr/themes/blue .config/hypr/themes/nerv .config/hypr/themes/unit01 .config/hypr/themes \
         .config/hypr .config/waybar .config/mako .config/wofi .config/ranger/colorschemes .config/ranger \
         .config/xsettingsd .bashrc.d .tmux/scripts; do
  [ -d "$HOME/$d" ] && run rmdir --ignore-fail-on-non-empty "$HOME/$d"
done

echo "==> restoring backups"
if [ -n "$ONLY" ]; then backups=("$ONLY"); else
  mapfile -t backups < <(ls -d "$HOME"/.magi-backup/*/ 2>/dev/null | sort -r)
fi
# newest first, so the oldest backup (the pre-MAGI state) is restored last and wins
n=0
for b in "${backups[@]}"; do
  b=${b%/}; [ -f "$b/MANIFEST" ] || continue
  while IFS= read -r rel; do
    [ -n "$rel" ] || continue
    if [ -e "$HOME/$rel" ] || [ -L "$HOME/$rel" ]; then run rm -rf "${HOME:?}/$rel"; fi
    run mkdir -p "$(dirname "$HOME/$rel")"
    run cp -a "$b/$rel" "$HOME/$rel"
    n=$((n + 1))
  done < "$b/MANIFEST"
  if [ -f "$b/.bashrc.orig" ]; then run cp -a "$b/.bashrc.orig" "$HOME/.bashrc"; fi
done
echo "    restored $n path(s)"
[ "$n" -gt 0 ] && echo "    backups kept in ~/.magi-backup (delete them yourself once you are happy)"
cat <<EOF

MAGI configs removed. Packages (Hyprland, waybar, ...) are still installed; remove them with
  sudo dnf remove hyprland hyprlock hypridle hyprpaper hyprsunset hyprpolkitagent waybar
  sudo dnf copr remove sdegler/hyprland
Wallpapers in ~/Pictures/wallpapers/eva and the font in ~/.local/share/fonts/IosevkaTerm-NerdFont
were left in place. Log out and pick your previous session (GNOME) at the login screen.
EOF
