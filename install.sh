#!/usr/bin/env bash
# MAGI installer: an Evangelion-themed Hyprland desktop for Fedora 44 Workstation.
#
#   bash <(curl -fsSL https://raw.githubusercontent.com/pankajgarkoti/magi/main/install.sh)
#   ./install.sh [--dry-run] [--theme blue|nerv|unit01] [--no-packages] [--no-wallpapers]
#
# Safe to re-run: every step checks before it acts. Existing files that MAGI would
# replace are moved to ~/.magi-backup/<timestamp>/ first (uninstall.sh puts them back).
# Run it as your normal user; it asks sudo only for dnf.
set -Eeuo pipefail

MAGI_REPO=${MAGI_REPO:-https://github.com/pankajgarkoti/magi.git}
MAGI_BRANCH=${MAGI_BRANCH:-main}
COPR=sdegler/hyprland
NF_VERSION=v3.5.1   # Nerd Fonts release for IosevkaTerm
STOW_PACKAGES=(hypr gtk ranger alacritty tmux shell)

# Every package the configs and scripts use (audited from hyprland.lua, waybar, the
# eva-*/magi* tools, eva-theme, ranger, tmux). Names checked against the Fedora 44
# repos + COPR sdegler/hyprland. Most are already on Fedora Workstation.
PKGS=(
  # Hyprland stack (COPR sdegler/hyprland)
  hyprland hyprlock hypridle hyprpaper hyprsunset hyprpolkitagent hyprland-guiutils
  xdg-desktop-portal-hyprland waybar cliphist
  # bar, notifications, launcher, tray applets, portals
  mako wofi blueman network-manager-applet nm-connection-editor xdg-desktop-portal-gtk
  libnotify gnome-keyring polkit
  # wayland tools: clipboard, screenshots, brightness, media keys, X11 apps
  wl-clipboard grim slurp brightnessctl playerctl xorg-x11-server-Xwayland
  # MAGI panels and pickers (Python + GTK 4, layer-shell overlays)
  python3-gobject python3-pillow gtk3 gtk4 gtk4-layer-shell
  # sound, network, bluetooth, power, used by magi / eva-wifi / eva-bluetooth
  wireplumber pulseaudio-utils NetworkManager bluez upower
  # theming: GTK settings for X11 apps, icons, cursor, Qt, dconf, fonts
  xsettingsd adwaita-icon-theme adwaita-cursor-theme qt6-qtbase-gui dconf
  gsettings-desktop-schemas glib2 fontconfig google-noto-sans-cjk-vf-fonts google-noto-emoji-fonts
  # files: Nautilus (+ thumbnails, archives), ranger with text previews
  nautilus ffmpegthumbnailer file-roller ranger file poppler-utils bat jq
  # terminal
  alacritty tmux
  # settings, session plumbing, installer needs
  gnome-control-center xdg-utils xdg-user-dirs dbus-tools dbus-daemon procps-ng hostname
  ncurses stow git-core curl tar xz
)

usage() {
  sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
  cat <<EOF

Options:
  --dry-run         show what would be done, change nothing
  --theme NAME      starting theme: blue (default), nerv or unit01
  --update          keep the current theme and old backups (used by magi-update)
  --no-packages     skip COPR + dnf (configs only)
  --no-wallpapers   skip the wallpaper downloads
  -h, --help        this help
Environment: MAGI_DIR (checkout, default ~/.local/share/magi), MAGI_REPO, MAGI_BRANCH
EOF
}

DRY=0 UPDATE=0 THEME= DO_PKGS=1 DO_WALLS=1
ARGS=("$@")
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --update) UPDATE=1 ;;
    --theme) THEME=${2:?--theme needs a name}; shift ;;
    --theme=*) THEME=${1#--theme=} ;;
    --no-packages) DO_PKGS=0 ;;
    --no-wallpapers) DO_WALLS=0 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "install.sh: unknown option $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done
case "${THEME:-blue}" in blue|nerv|unit01) ;; *) echo "install.sh: theme must be blue, nerv or unit01" >&2; exit 2 ;; esac

# ---------------------------------------------------------------- output helpers
if [ -t 1 ]; then B=$'\e[1m' D=$'\e[2m' G=$'\e[32m' Y=$'\e[33m' RD=$'\e[31m' C=$'\e[36m' R=$'\e[0m'
else B= D= G= Y= RD= C= R=; fi
STEP=0 TOTAL=10 ERRORS=0
WARNINGS=()
step() { STEP=$((STEP + 1)); printf '\n%s[%d/%d] %s%s\n' "$B$C" "$STEP" "$TOTAL" "$*" "$R"; }
ok()   { printf '  %s✓%s %s\n' "$G" "$R" "$*"; }
skip() { printf '  %s· %s%s\n' "$D" "$*" "$R"; }
warn() { printf '  %s! %s%s\n' "$Y" "$*" "$R"; WARNINGS+=("$*"); }
fail() { printf '  %s✗ %s%s\n' "$RD" "$*" "$R"; ERRORS=$((ERRORS + 1)); }
die()  { printf '%s✗ %s%s\n' "$RD" "$*" "$R" >&2; exit 1; }
# run CMD...: execute, or only print it in --dry-run
run() {
  if [ "$DRY" = 1 ]; then printf '  %s[dry-run]%s' "$Y" "$R"; printf ' %q' "$@"; printf '\n'; return 0; fi
  "$@"
}
# fontconfig knows the font (not `fc-list | grep -q`: grep's early exit + pipefail = false negative)
have_font() { local l; l=$(fc-list : family 2>/dev/null) || true; [[ $l == *"IosevkaTerm Nerd Font"* ]]; }
trap 'printf "%s✗ install.sh failed at line %s: %s%s\n" "$RD" "$LINENO" "$BASH_COMMAND" "$R" >&2' ERR

export PATH="$HOME/.local/bin:$PATH"
TS=$(date +%Y%m%d-%H%M%S)
BACKUP="$HOME/.magi-backup/$TS"
STATE="$HOME/.local/state/magi"

# move PATH (relative to $HOME) into this run's backup folder, keeping its layout
backup() {
  local rel=$1 src="$HOME/$1"
  [ -e "$src" ] || [ -L "$src" ] || return 0
  if [ "$DRY" = 1 ]; then printf '  %s[dry-run]%s back up ~/%s\n' "$Y" "$R" "$rel"; return 0; fi
  mkdir -p "$BACKUP/$(dirname "$rel")"
  mv "$src" "$BACKUP/$rel"
  echo "$rel" >> "$BACKUP/MANIFEST"
  printf '  %s↳%s backed up ~/%s\n' "$Y" "$R" "$rel"
}

# the curl one-liner re-runs install.sh from the fresh checkout: print the banner once
if [ -z "${MAGI_REEXEC:-}" ]; then
printf '%s' "$B$C"
cat <<'EOF'
 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   MAGI SYSTEM · installer      MELCHIOR · BALTHASAR · CASPER
   Evangelion-themed Hyprland for Fedora
 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
EOF
printf '%s' "$R"
fi
[ "$DRY" = 1 ] && printf '%sDRY RUN: nothing will be changed.%s\n' "$Y" "$R"

# ---------------------------------------------------------------- 1. preflight
step "Preflight"
[ "$(id -u)" -ne 0 ] || die "run install.sh as your normal user, not root (it uses sudo for dnf)"
[ -r /etc/os-release ] && . /etc/os-release
[ "${ID:-}" = fedora ] || die "MAGI targets Fedora (found ${PRETTY_NAME:-unknown OS})"
if [ "${VERSION_ID:-}" = 44 ]; then ok "Fedora $VERSION_ID"
else warn "MAGI is tested on Fedora 44; this is Fedora ${VERSION_ID:-?}. Continuing."; fi
SUDO=()
if [ "$DO_PKGS" = 1 ]; then
  command -v sudo >/dev/null || die "sudo is needed to install packages (or use --no-packages)"
  SUDO=(sudo)
  if [ "$DRY" = 0 ] && ! sudo -n true 2>/dev/null; then
    echo "  sudo is needed for dnf; you may be asked for your password."
    sudo -v || die "sudo failed"
  fi
  ok "sudo available"
fi

# ---------------------------------------------------------------- 2. checkout
step "MAGI checkout"
SELF_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || true)
if [ -n "$SELF_DIR" ] && [ -d "$SELF_DIR/packages/hypr" ]; then
  MAGI_DIR=$SELF_DIR
  ok "using this checkout: $MAGI_DIR"
else
  # run via curl: clone (or reuse) the checkout, then continue from its own install.sh
  MAGI_DIR=${MAGI_DIR:-$HOME/.local/share/magi}
  if [ ! -d "$MAGI_DIR/.git" ]; then
    command -v git >/dev/null || run "${SUDO[@]}" dnf -y install git-core
    run git clone --branch "$MAGI_BRANCH" "$MAGI_REPO" "$MAGI_DIR"
  else
    skip "checkout exists: $MAGI_DIR"
  fi
  [ "$DRY" = 1 ] && [ ! -d "$MAGI_DIR" ] && { echo "  (dry run: would continue with $MAGI_DIR/install.sh)"; exit 0; }
  echo "  continuing with $MAGI_DIR/install.sh"
  MAGI_REEXEC=1 exec "$MAGI_DIR/install.sh" "${ARGS[@]}"
fi
PK="$MAGI_DIR/packages"

# ---------------------------------------------------------------- 3. COPR
step "COPR $COPR (Hyprland 0.57 stack)"
if [ "$DO_PKGS" = 0 ]; then skip "--no-packages"
elif dnf -q repolist --enabled 2>/dev/null | grep -q "sdegler:hyprland"; then skip "already enabled"
else
  if ! dnf copr --help >/dev/null 2>&1; then run "${SUDO[@]}" dnf -y install dnf5-plugins; fi
  run "${SUDO[@]}" dnf -y copr enable "$COPR" && ok "enabled $COPR"
fi

# ---------------------------------------------------------------- 4. packages
step "Packages (${#PKGS[@]})"
if [ "$DO_PKGS" = 0 ]; then skip "--no-packages"
else
  missing=()
  for p in "${PKGS[@]}"; do rpm -q --quiet "$p" 2>/dev/null || missing+=("$p"); done
  if [ ${#missing[@]} -eq 0 ]; then skip "all ${#PKGS[@]} installed"
  else
    echo "  installing ${#missing[@]}: ${missing[*]}"
    run "${SUDO[@]}" dnf -y install "${missing[@]}"
    if [ "$DRY" = 0 ]; then
      still=(); for p in "${missing[@]}"; do rpm -q --quiet "$p" || still+=("$p"); done
      [ ${#still[@]} -eq 0 ] && ok "installed ${#missing[@]} packages" || fail "not installed: ${still[*]}"
    fi
  fi
fi
command -v stow >/dev/null || [ "$DRY" = 1 ] || die "stow is missing (needed to link the configs)"

# ---------------------------------------------------------------- 5. font
step "IosevkaTerm Nerd Font ($NF_VERSION)"
FONT_DIR="$HOME/.local/share/fonts/IosevkaTerm-NerdFont"
if have_font || ls "$FONT_DIR"/*.ttf >/dev/null 2>&1; then
  skip "already installed"
elif [ "$DRY" = 1 ]; then
  printf '  %s[dry-run]%s download IosevkaTerm.tar.xz from nerd-fonts %s into %s\n' "$Y" "$R" "$NF_VERSION" "$FONT_DIR"
else
  tmp=$(mktemp -d)
  base="https://github.com/ryanoasis/nerd-fonts/releases/download/$NF_VERSION"
  if curl -fsSL --retry 3 -o "$tmp/IosevkaTerm.tar.xz" "$base/IosevkaTerm.tar.xz" &&
     curl -fsSL --retry 3 -o "$tmp/SHA-256.txt" "$base/SHA-256.txt"; then
    want=$(awk '$2 ~ /(^|\/)IosevkaTerm\.tar\.xz$/ {print $1}' "$tmp/SHA-256.txt")
    have=$(sha256sum "$tmp/IosevkaTerm.tar.xz" | cut -d' ' -f1)
    if [ -n "$want" ] && [ "$want" = "$have" ]; then
      mkdir -p "$FONT_DIR"
      tar -xJf "$tmp/IosevkaTerm.tar.xz" -C "$FONT_DIR" --wildcards '*.ttf' 2>/dev/null || tar -xJf "$tmp/IosevkaTerm.tar.xz" -C "$FONT_DIR"
      fc-cache -f "$FONT_DIR" >/dev/null 2>&1 || true
      ok "installed $(ls "$FONT_DIR"/*.ttf | wc -l) font files (sha256 verified)"
    else
      fail "IosevkaTerm.tar.xz checksum mismatch; not installed"
    fi
  else
    warn "could not download the font (offline?); re-run install.sh later"
  fi
  rm -rf "$tmp"
fi

# ---------------------------------------------------------------- 6. back up + stow
step "Link configs (stow: ${STOW_PACKAGES[*]})"
FIRST_INSTALL=1; [ -f "$STATE/installed" ] && FIRST_INSTALL=0
moved=0
for pkg in "${STOW_PACKAGES[@]}"; do
  while IFS= read -r -d '' f; do
    rel=${f#./}; src="$PK/$pkg/$rel"; tgt="$HOME/$rel"
    # a parent that is a symlink to somewhere else (an old dotfiles stow) or a file
    cur=$HOME; prel=
    IFS=/ read -r -a parts <<< "$(dirname "$rel")"
    for comp in "${parts[@]}"; do
      [ "$comp" = . ] && continue
      cur="$cur/$comp"; prel="${prel:+$prel/}$comp"
      if [ -L "$cur" ]; then
        case "$(readlink -f "$cur")" in "$PK/$pkg"/*) ;; *) backup "$prel"; moved=$((moved + 1)) ;; esac
        break
      elif [ -e "$cur" ] && [ ! -d "$cur" ]; then backup "$prel"; moved=$((moved + 1)); break
      fi
    done
    if [ -L "$tgt" ]; then
      [ "$(readlink -f "$tgt")" = "$(readlink -f "$src")" ] && continue
      backup "$rel"; moved=$((moved + 1))
    elif [ -e "$tgt" ]; then
      backup "$rel"; moved=$((moved + 1))
    fi
  done < <(cd "$PK/$pkg" && find . \( -type f -o -type l \) ! -name .stow-local-ignore ! -path '*/__pycache__/*' -print0)
done
# files eva-theme generates (not links): keep the user's originals on the first install
if [ "$FIRST_INSTALL" = 1 ] && [ "$UPDATE" = 0 ]; then
  for rel in .config/mako/config .config/wofi/style.css .config/hypr/hyprlock.conf \
             .config/hypr/hyprpaper.conf .config/hypr/hyprtoolkit.conf .config/xsettingsd/xsettingsd.conf \
             .config/waybar/colors.css; do
    if [ -e "$HOME/$rel" ] && [ ! -L "$HOME/$rel" ]; then backup "$rel"; moved=$((moved + 1)); fi
  done
fi
[ "$moved" -gt 0 ] && ok "$moved existing path(s) moved to ${BACKUP/#$HOME/\~}" || skip "nothing to back up"
run stow --no-folding -d "$PK" -t "$HOME" -R "${STOW_PACKAGES[@]}" && ok "stowed ${#STOW_PACKAGES[@]} packages from ${PK/#$HOME/\~}"

# ---------------------------------------------------------------- 7. shell + tmux
step "Shell and tmux"
if grep -qs 'bashrc.d' "$HOME/.bashrc"; then skip "~/.bashrc already sources ~/.bashrc.d (MAGI banner + PATH)"
else
  if [ "$DRY" = 0 ] && [ -f "$HOME/.bashrc" ]; then mkdir -p "$BACKUP"; cp -a "$HOME/.bashrc" "$BACKUP/.bashrc.orig"; fi
  if [ "$DRY" = 1 ]; then printf '  %s[dry-run]%s append a ~/.bashrc.d loader to ~/.bashrc\n' "$Y" "$R"
  else
    cat >> "$HOME/.bashrc" <<'EOF'

# >>> MAGI: load ~/.bashrc.d snippets (MAGI banner, ~/.local/bin on PATH)
for rc in ~/.bashrc.d/*; do [ -f "$rc" ] && . "$rc"; done; unset rc
# <<< MAGI
EOF
    ok "added a ~/.bashrc.d loader to ~/.bashrc (original copied to backup)"
  fi
fi
if [ -d "$HOME/.tmux/plugins/tmux-sensible" ]; then skip "tmux-sensible present"
elif [ "$DRY" = 1 ]; then printf '  %s[dry-run]%s git clone tmux-sensible\n' "$Y" "$R"
elif git clone -q --depth 1 https://github.com/tmux-plugins/tmux-sensible "$HOME/.tmux/plugins/tmux-sensible" 2>/dev/null; then ok "tmux-sensible plugin cloned"
else warn "tmux-sensible not cloned (offline?); tmux works without it"; fi

# ---------------------------------------------------------------- 8. wallpapers
step "Wallpapers"
WD="$HOME/Pictures/wallpapers/eva"
run mkdir -p "$WD"
if [ -f "$WD/catppuccin-blue.png" ]; then skip "catppuccin-blue.png exists"
else
  run python3 -I "$MAGI_DIR/wallpapers/make-blue.py" "$WD/catppuccin-blue.png" >/dev/null && ok "generated catppuccin-blue.png"
fi
if [ "$DO_WALLS" = 0 ]; then skip "--no-wallpapers"
else
  got=0 have=0 bad=0 off=0
  while IFS=$'\t' read -r file sum url page; do
    case "$file" in ''|\#*) continue ;; esac
    if [ -f "$WD/$file" ]; then have=$((have + 1)); continue; fi
    if [ "$DRY" = 1 ]; then printf '  %s[dry-run]%s download %s\n' "$Y" "$R" "$url"; continue; fi
    if curl -fsSL --retry 2 --connect-timeout 10 -A "magi-installer" -o "$WD/.$file.part" "$url"; then
      if [ "$(sha256sum "$WD/.$file.part" | cut -d' ' -f1)" = "$sum" ]; then
        mv "$WD/.$file.part" "$WD/$file"; got=$((got + 1))
      else rm -f "$WD/.$file.part"; bad=$((bad + 1)); fi
    else rm -f "$WD/.$file.part"; off=$((off + 1)); fi
  done < "$MAGI_DIR/wallpapers/manifest.tsv"
  [ "$have" -gt 0 ] && skip "$have already downloaded"
  [ "$got" -gt 0 ] && ok "downloaded $got EVA wallpapers (sha256 verified)"
  [ "$bad" -gt 0 ] && warn "$bad wallpaper(s) failed the checksum and were skipped"
  [ "$off" -gt 0 ] && warn "$off wallpaper(s) could not be downloaded (offline?); re-run install.sh later. The blue wallpaper is used meanwhile."
fi

# ---------------------------------------------------------------- 9. desktop settings + theme
step "Desktop settings and theme"
if [ -z "$THEME" ]; then
  cur=$(basename "$(readlink "$HOME/.config/hypr/themes/current" 2>/dev/null)" 2>/dev/null || true)
  if [ "$UPDATE" = 1 ] && [ -n "$cur" ] && [ -d "$PK/hypr/.config/hypr/themes/$cur" ]; then THEME=$cur; else THEME=blue; fi
fi
command -v xdg-user-dirs-update >/dev/null && run xdg-user-dirs-update >/dev/null 2>&1 || true
if systemctl --user show-environment >/dev/null 2>&1; then
  run systemctl --user daemon-reload && ok "systemd user units reloaded (hyprland-session.target starts with Hyprland)"
else
  skip "no systemd user manager here (container?); units load at next login"
fi
# gsettings/dconf need a session bus; outside a desktop session start a private one
settings() {
  set -e
  gs() { gsettings set "$@" 2>/dev/null || echo "  ! gsettings $*: not applied" >&2; }
  gs org.gnome.desktop.interface font-name 'IosevkaTerm Nerd Font Mono 10'
  gs org.gnome.desktop.interface document-font-name 'IosevkaTerm Nerd Font Mono 10'
  gs org.gnome.desktop.interface monospace-font-name 'IosevkaTerm Nerd Font Mono 11'
  gs org.gnome.desktop.wm.preferences titlebar-font 'IosevkaTerm Nerd Font Mono Bold 10'
  gs org.gnome.desktop.interface gtk-theme EVA
  gs org.gnome.desktop.interface color-scheme prefer-dark
  # eva-theme writes mako/wofi/hyprlock/hyprpaper/hyprtoolkit/xsettingsd configs, GTK theme,
  # icons, GtkSourceView scheme and the lock image, and sets the theme gsettings
  eva-theme "$1"
}
export -f settings
if [ "$DRY" = 1 ]; then
  printf '  %s[dry-run]%s gsettings (fonts, EVA GTK theme, dark) + eva-theme %s\n' "$Y" "$R" "$THEME"
else
  if [ -n "${DBUS_SESSION_BUS_ADDRESS:-}" ]; then out=$(bash -c 'settings "$0"' "$THEME" 2>&1) && rc=0 || rc=$?
  else out=$(dbus-run-session -- bash -c 'settings "$0"' "$THEME" 2>&1) && rc=0 || rc=$?; fi
  printf '%s\n' "$out" | grep -v '^dbus-daemon\[' | sed 's/^/    /'
  [ "$rc" = 0 ] && ok "theme: $THEME" || fail "eva-theme $THEME exited with $rc"
fi
if command -v xdg-mime >/dev/null && [ -z "$(xdg-mime query default inode/directory 2>/dev/null)" ]; then
  run xdg-mime default org.gnome.Nautilus.desktop inode/directory && ok "Nautilus opens folders"
fi

# ---------------------------------------------------------------- 10. verify
step "Verify"
if [ "$DRY" = 1 ]; then skip "dry run: nothing to verify"
else
  links=0 bad=0
  for pkg in "${STOW_PACKAGES[@]}"; do
    while IFS= read -r -d '' f; do
      rel=${f#./}
      if [ -L "$HOME/$rel" ] && [ "$(readlink -f "$HOME/$rel")" = "$(readlink -f "$PK/$pkg/$rel")" ]; then links=$((links + 1))
      else bad=$((bad + 1)); fail "not linked: ~/$rel"; fi
    done < <(cd "$PK/$pkg" && find . \( -type f -o -type l \) ! -name .stow-local-ignore ! -path '*/__pycache__/*' -print0)
  done
  [ "$bad" = 0 ] && ok "$links config links in place"
  gen=0
  for f in .config/mako/config .config/wofi/style.css .config/hypr/hyprlock.conf .config/hypr/hyprpaper.conf \
           .config/hypr/hyprtoolkit.conf .config/xsettingsd/xsettingsd.conf .cache/eva-lock.png \
           .local/share/themes/EVA/gtk-3.0/gtk.css .local/share/themes/EVA/gtk-4.0/gtk.css \
           .local/share/icons/EVA/index.theme .local/share/gtksourceview-5/styles/eva.xml .config/waybar/colors.css \
           Pictures/wallpapers/eva/catppuccin-blue.png; do
    if [ -s "$HOME/$f" ]; then gen=$((gen + 1)); else fail "missing generated file: ~/$f"; fi
  done
  [ "$(readlink "$HOME/.config/hypr/themes/current" 2>/dev/null)" = "$THEME" ] && gen=$((gen + 1)) || fail "themes/current is not $THEME"
  ok "$gen generated files/links present"
  if [ "$DO_PKGS" = 1 ]; then
    miss=(); for p in "${PKGS[@]}"; do rpm -q --quiet "$p" || miss+=("$p"); done
    [ ${#miss[@]} -eq 0 ] && ok "all ${#PKGS[@]} packages installed" || fail "packages missing: ${miss[*]}"
  fi
  have_font && ok "IosevkaTerm Nerd Font available" || warn "IosevkaTerm Nerd Font not found by fontconfig"
  if command -v Hyprland >/dev/null; then
    # Hyprland wants a runtime dir even to parse; give it a throwaway one outside a session
    rt=${XDG_RUNTIME_DIR:-}; [ -n "$rt" ] || { rt=$(mktemp -d); chmod 700 "$rt"; }
    if v=$(XDG_RUNTIME_DIR=$rt Hyprland --verify-config -c "$HOME/.config/hypr/hyprland.lua" 2>&1) && grep -q "config ok" <<< "$v"; then ok "Hyprland config verifies"
    else fail "Hyprland --verify-config: $(tail -3 <<< "$v" | tr '\n' ' ')"; fi
  fi
  mkdir -p "$STATE"
  { echo "dir=$MAGI_DIR"; echo "theme=$THEME"; echo "date=$TS"; } > "$STATE/installed"
fi

# ---------------------------------------------------------------- summary
echo
if [ "$ERRORS" -gt 0 ]; then
  printf '%s%s✗ MAGI install finished with %d error(s); see ✗ lines above.%s\n' "$B" "$RD" "$ERRORS" "$R"
else
  printf '%s%s✓ MAGI installed.%s %s\n' "$B" "$G" "$R" "$([ ${#WARNINGS[@]} -gt 0 ] && echo "(${#WARNINGS[@]} warning(s))")"
fi
for w in "${WARNINGS[@]}"; do printf '  %s! %s%s\n' "$Y" "$w" "$R"; done
[ -d "$BACKUP" ] && printf '  Backups: %s (restore with %s/uninstall.sh)\n' "${BACKUP/#$HOME/\~}" "${MAGI_DIR/#$HOME/\~}"
if [ "$UPDATE" = 0 ]; then
cat <<EOF

${B}Next steps${R}
  1. Log out.
  2. On the login screen, pick your user, click the gear (⚙) and choose ${B}Hyprland${R}.
  3. Log in. SUPER+Return opens a terminal, SUPER+Space the launcher,
     SUPER+SHIFT+Space the MAGI control centre, SUPER+SHIFT+T cycles the theme.
  Update later with ${B}magi-update${R}. Shortcuts: ${MAGI_DIR/#$HOME/\~}/README.md
EOF
fi
trap - ERR
exit $(( ERRORS > 0 ))
