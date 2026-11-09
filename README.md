# MAGI

**An Evangelion-themed Hyprland desktop for Fedora 44.** One installer turns a stock Fedora
Workstation into a keyboard-first tiling desktop styled after NERV's MAGI supercomputer:
square edges, black glass, hazard stripes, and three switchable colour themes that restyle
everything at once (bar, terminal, GTK and libadwaita apps, icons, lock screen, tmux, ranger).

GNOME stays installed. MAGI is just another session on the login screen.

![MAGI desktop, blue theme](docs/screenshots/desktop-blue.png)

| | |
|---|---|
| ![MAGI control centre](docs/screenshots/magi-control-centre.png) | ![Overview](docs/screenshots/overview.png) |
| ![Wi-Fi picker](docs/screenshots/wifi-picker.png) | ![Bluetooth picker](docs/screenshots/bluetooth-picker.png) |
| ![NERV theme](docs/screenshots/theme-nerv.png) | ![EVA Unit-01 theme](docs/screenshots/theme-unit01.png) |

## What you get

- **Hyprland 0.57** (Lua config) from COPR `sdegler/hyprland`, with hyprlock, hypridle,
  hyprpaper, hyprsunset and hyprpolkitagent.
- **waybar** with a MAGI badge, **mako** notifications, **wofi** launcher, clipboard history (cliphist).
- **MAGI control centre** (`magi`): network, Bluetooth, sound (devices, volume, mic), display
  (brightness, night light), power (profiles, lock, suspend, log out, reboot, shut down, each
  confirmed), theme and wallpaper picker, and tools. Keyboard first, opens in under 100 ms.
- **MAGI overview** (`magi-overview`): every window on every workspace with live thumbnails;
  type to filter, Enter to jump.
- **eva-wifi** and **eva-bluetooth**: keyboard-driven Wi-Fi and Bluetooth pickers.
- **Themes**: `blue` (Catppuccin Macchiato, default), `nerv` (red/orange on black) and
  `unit01` (purple/green). One command switches all of it live.
- A generated **EVA GTK theme and icon theme**, a libadwaita colour layer, an EVA
  GtkSourceView scheme, a themed polkit prompt, and a hex-lattice lock screen.
- **Alacritty** + **tmux** (Dracula-based status bar in theme colours), **ranger** with an EVA
  colour scheme and text previews, **Nautilus** for files.
- The IosevkaTerm Nerd Font everywhere, and a MAGI boot banner in new terminals.

## Install

On a fresh **Fedora 44 Workstation**, as your normal user (not root):

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/pankajgarkoti/magi/main/install.sh)
```

or from a checkout:

```bash
git clone https://github.com/pankajgarkoti/magi.git ~/.local/share/magi
~/.local/share/magi/install.sh            # add --dry-run to preview every step first
```

Then **log out**. On the login screen, pick your user, click the gear (⚙) in the corner and
choose **Hyprland**, then log in.

What the installer does, in order (each step is skipped when it is already done, so it is
safe to re-run):

1. Checks that this is Fedora and that sudo works.
2. Uses this checkout, or clones the repo to `~/.local/share/magi`.
3. Enables COPR `sdegler/hyprland`.
4. Installs the missing packages with `dnf` (the list is at the top of `install.sh`).
5. Downloads the IosevkaTerm Nerd Font from the official nerd-fonts release (checksum
   verified) into `~/.local/share/fonts`.
6. **Moves any existing file that MAGI would replace to `~/.magi-backup/<timestamp>/`**, then
   links the configs with GNU stow (`packages/*`, linked file by file).
7. Hooks `~/.bashrc.d` (Fedora's default `.bashrc` already does) and fetches tmux-sensible.
8. Generates the blue wallpaper and downloads the EVA wallpapers listed in
   [`wallpapers/manifest.tsv`](wallpapers/manifest.tsv) (sha256 checked). Offline? They are
   skipped and the blue wallpaper is used until you re-run the installer.
9. Sets fonts and dark mode in gsettings and runs `eva-theme blue`.
10. Verifies every link, generated file, package and the Hyprland config.

Options: `--dry-run`, `--theme nerv|unit01|blue`, `--no-packages`, `--no-wallpapers`.

## Update

```bash
magi-update
```

`git pull`s the checkout, re-links the configs, installs any newly added packages, re-runs the
setup steps with your current theme, and reloads Hyprland, waybar and the MAGI panels.

## Shortcuts

SUPER is the window-manager key. Alt is left to tmux.

| Keys | Action |
|---|---|
| SUPER + Return | Terminal (Alacritty) |
| SUPER + Space | App launcher (wofi) |
| SUPER + SHIFT + Space | MAGI control centre |
| SUPER + O | Overview / window switcher |
| SUPER + SHIFT + W / SUPER + SHIFT + B | Wi-Fi picker / Bluetooth picker |
| SUPER + SHIFT + S | Sound settings |
| SUPER + E | Files (Nautilus) |
| SUPER + R | ranger (floating terminal file manager) |
| SUPER + B | Web browser (your default) |
| SUPER + Q | Close window |
| SUPER + F / V / P / S | Fullscreen / float / pseudo-tile / toggle split |
| SUPER + h j k l (or arrows) | Move focus |
| SUPER + SHIFT + h j k l | Move window |
| SUPER + CTRL + h j k l | Resize window |
| SUPER + 1…9 / SUPER + SHIFT + 1…9 | Go to workspace / send window to workspace |
| SUPER + Tab, or tap SUPER twice | Previous workspace |
| SUPER + ` | MAGI scratchpad terminal (drop-down tmux) |
| SUPER + SHIFT + ` | Send window to the scratchpad |
| SUPER + A | **Window mode**: plain h j k l focus, H J K L move, arrows resize, 1–9, f, v, q; Esc leaves |
| SUPER + W | Next wallpaper |
| SUPER + SHIFT + T | Cycle theme (nerv → unit01 → blue) |
| SUPER + SHIFT + V | Clipboard history |
| SUPER + Escape | Lock screen |
| SUPER + SHIFT + E | Exit Hyprland |
| Print / SHIFT + Print | Screenshot a region to the clipboard / the screen to ~/Pictures |
| SUPER + drag / SUPER + right-drag | Move / resize a window with the mouse |
| Volume, brightness, media keys | Work as labelled |

Inside the control centre: arrows or `j k` move, `h l` adjust, Enter acts, `1`–`7` jump to a
section, Esc goes back. In the overview, type to filter, Enter jumps, Shift+Enter brings the
window here. The waybar **MAGI** badge opens the control centre (right-click cycles the theme).

## Themes

```bash
eva-theme blue      # Catppuccin Macchiato blue + mauve on slate (default)
eva-theme nerv      # NERV: red, orange, black
eva-theme unit01    # EVA Unit-01: purple and green
eva-theme toggle    # cycle (also SUPER + SHIFT + T, or the THEME page of the control centre)
```

A theme is a folder in `packages/hypr/.config/hypr/themes/<name>/` (`colors.css`, `palette`,
`theme.lua`, `alacritty.toml`, `tmux.conf`). eva-theme points `themes/current` at it and
regenerates everything that cannot read `colors.css` directly. libadwaita apps (Settings,
Files) pick up a new theme the next time they start.

## Uninstall / roll back

```bash
~/.local/share/magi/uninstall.sh          # add --dry-run to preview
```

This removes MAGI's links and generated files, removes the `~/.bashrc` loader if the
installer added one, and copies back everything from `~/.magi-backup/` (the state from
before the first install wins). Backups are never deleted; remove `~/.magi-backup` yourself
when you are happy. Packages stay installed. To remove them too:

```bash
sudo dnf remove hyprland hyprlock hypridle hyprpaper hyprsunset hyprpolkitagent waybar
sudo dnf copr remove sdegler/hyprland
```

Then pick GNOME again on the login screen.

## Layout

```
install.sh  uninstall.sh
packages/          stow packages, linked into ~ file by file
  hypr/            Hyprland, waybar, mako, wofi, themes, the magi/eva-* tools (~/.local/bin)
  gtk/             EVA GTK 3/4 theme sources, libadwaita colours, generators
  ranger/ alacritty/ tmux/ shell/
wallpapers/        manifest.tsv (downloaded at install) and make-blue.py (generated)
tests/             container-test.sh (podman, Fedora 44) and the VM test notes
```

## Credits

- Wallpapers are not part of this repository. The installer downloads them from
  [Wallhaven](https://wallhaven.cc); the manifest links each one's page. All rights stay with
  the original artists. *Neon Genesis Evangelion* is © khara / GAINAX; MAGI is an unofficial
  fan theme.
- tmux status bar: a lightly modified [dracula/tmux](https://github.com/dracula/tmux) (MIT),
  plus [tpm](https://github.com/tmux-plugins/tpm) (MIT), both vendored under `packages/tmux`.
- Hyprland packages for Fedora: COPR [sdegler/hyprland](https://copr.fedorainfracloud.org/coprs/sdegler/hyprland/).
- Font: [IosevkaTerm Nerd Font](https://github.com/ryanoasis/nerd-fonts).
