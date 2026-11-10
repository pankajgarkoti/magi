# MAGI

MAGI is a Fedora remix where the main interface is an orchestrator agent. You speak or type
to it. It runs other agents, operates the desktop, and asks you only for decisions. The
desktop around it is Hyprland: square corners, black glass, a hazard stripe here and there,
three colour themes.

![The orchestrator console dropped down over the desktop, NERV theme](docs/screenshots/agent-console.png)

## The orchestrator

- **One agent in front.** A Claude Code session runs in tmux window 1 (session `magi`) and
  starts at login. SUPER+` drops its console over the desktop; the console also opens by
  itself at login. Other agents get their own tmux windows. The orchestrator starts them,
  checks their work, drives Hyprland through `hyprctl`, and brings you decisions.
- **Local voice.** A listener transcribes the microphone with faster-whisper (`base.en`) and
  types the text into the orchestrator as `[voice] …`. Replies are spoken with Piper. Both
  models are part of the image, so speech works offline and no audio leaves the machine.
  There is no wake word. SUPER+SHIFT+M mutes it.
- **Visible state.** The bar module next to the MAGI tag shows the orchestrator
  (`IDLE`, `WORKING`, `ASKING` when a prompt waits, `OFFLINE`), the number of agents, and the
  listener. The control centre's AGENTS page lists every agent and has the switches.
- **Housekeeping.** Agents idle for an hour are closed, with the resume command logged.
  After a reboot the tmux layout comes back with each session resumed.
- **Opt-in, and not bundled.** Claude Code is Anthropic's software and is not in the image.
  On first login, PILOT SETUP runs Anthropic's installer, you sign in with your own account,
  and you pick a microphone. "Not now" leaves a plain desktop; `magi-agent off` turns it off
  later. Without network, everything except Claude keeps working.

| | |
|---|---|
| ![Control centre, AGENTS page](docs/screenshots/agent-control-centre.png) | ![PILOT SETUP on first login](docs/screenshots/pilot-setup.png) |

![Bar module: orchestrator state, agents, listener](docs/screenshots/agent-bar.png)

The orchestrator text in these screenshots comes from a test stand-in for the `claude`
command; the VM was not signed in to a Claude account. They were captured at 2× in the MAGI
distro test VM, installed from the ISO.

## The desktop

![The MAGI control centre in the NERV theme, open on its THEME page](docs/screenshots/hero-nerv.png)

The control centre (SUPER + SHIFT + Space) in the NERV theme. More screenshots and short clips
are [below](#a-look-around).

GNOME stays installed. MAGI (and plain Hyprland) are extra sessions on the login screen, so
you can log out and pick GNOME again.

## Get it

**MAGI image (Fedora Atomic, bootc).** A bootable container image with everything above. It is
installed from an ISO and updated atomically: `sudo bootc upgrade` stages the next image, and
`sudo bootc rollback` returns to the previous one. No ISO or image is published yet. To build
both locally (podman only, no root):

```bash
podman build -f distro/Containerfile -t localhost/magi:latest .
distro/build-iso.sh --image localhost/magi:latest --out magi-44-x86_64.iso
```

The ISO is Fedora's netinstall boot media plus the MAGI image (installed offline from the ISO)
and a kickstart. Anaconda asks for the disk; GNOME Initial Setup creates the user. The login
screen defaults to the MAGI session. See [`distro/`](distro) and the VM test in
[`tests/vm/`](tests/vm).

**On an existing Fedora 44 Workstation:** `install.sh` below sets up the same desktop, with
backups and an uninstall script. The orchestrator layer is a separate opt-in step:
`agent/setup.sh` (links the tools and units, builds the local voice runtime in
`~/.local/share/magi-voice`).

## What's in it

- Hyprland 0.57 with the new Lua config, from the `sdegler/hyprland` COPR. Also hyprlock,
  hypridle, hyprpaper, hyprsunset and hyprpolkitagent.
- waybar, mako for notifications, wofi as the launcher, cliphist for clipboard history.
- `magi`, a control centre that replaces GNOME's quick settings: network, Bluetooth,
  sound devices and volume, brightness and night light, power profiles, lock/suspend/reboot
  (each one asks twice), themes and wallpapers. It stays resident, so it opens in under 100 ms.
- `magi-overview`: all windows on all workspaces as live thumbnails. Type to filter, Enter jumps.
- `eva-wifi` and `eva-bluetooth`, small keyboard pickers for Wi-Fi and Bluetooth.
- Three themes: `blue` (Catppuccin Macchiato, the default), `nerv` (red and orange on black),
  `unit01` (purple and green). Switching one restyles the bar, terminal, tmux, GTK and
  libadwaita apps, icons, the polkit prompt and the lock screen, live.
- Alacritty, tmux with a recoloured Dracula status bar, ranger, Nautilus, and the IosevkaTerm
  Nerd Font everywhere. New terminals print a short MAGI status banner (orchestrator,
  memory, network, power). `MAGI_NO_BANNER=1` turns it off.

## A look around

Everything here was captured in a throwaway Fedora 44 Workstation VM right after running the
installer, at 2× scale, and cropped to one thing per image. The Wi-Fi networks and Bluetooth
devices are simulated inside the VM.

Switching from NERV to Unit-01 on the control centre's THEME page. The few seconds the VM takes
to apply a theme are cut. ([MP4](docs/clips/theme-switch.mp4))

https://github.com/user-attachments/assets/c6d94bbf-9aad-4347-a9f7-353af367de6c

The three themes on the same page: `nerv`, `unit01`, `blue`.

![The control centre THEME page in the nerv, unit01 and blue themes, stacked](docs/screenshots/themes.png)

Opening the control centre, then jumping to NETWORK, BLUETOOTH and SOUND. (Recorded before
the AGENTS page became section 1; those sections are now `2`, `3`, `4`.)
([MP4](docs/clips/control-centre.mp4))

https://github.com/user-attachments/assets/a23c4f63-8490-48f2-acb0-4f0d3b44d156

The NETWORK page: link details, then shortcuts to the Wi-Fi picker, the connection editor and
GNOME's network settings.

![Control centre NETWORK page](docs/screenshots/control-centre-network.png)

`magi-overview` (SUPER + O): every window as a live thumbnail, grouped by workspace.

![magi-overview with live window thumbnails](docs/screenshots/overview.png)

Typing `naut` filters to the Files window on workspace 2; Enter jumps there.
([MP4](docs/clips/overview.mp4))

https://github.com/user-attachments/assets/49de09ae-e4ea-48fd-825a-d66681113a24

`eva-wifi` (SUPER + SHIFT + W): the current link and the networks in range, driven with `j`/`k`.
([MP4](docs/clips/wifi-picker.mp4))

![eva-wifi picker](docs/screenshots/wifi-picker.png)

https://github.com/user-attachments/assets/878598bc-4d1c-4f88-95f9-749c6f9874f4

`eva-bluetooth` (SUPER + SHIFT + B) during a scan: one paired speaker, one keyboard in range.

![eva-bluetooth picker during a scan](docs/screenshots/bluetooth-picker.png)

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
   [`wallpapers/manifest.tsv`](wallpapers/manifest.tsv) (sha256 checked). If you are offline they are
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

SUPER does window management. Alt is left alone because tmux uses it.

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
| SUPER + ` | Orchestrator console (drop-down tmux session `magi`) |
| SUPER + SHIFT + M | Mute / unmute the listener |
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

Inside the control centre: arrows or `j k` move, `h l` adjust, Enter acts, `1`–`8` jump to a
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
agent/             the orchestrator layer: magi-agent, PILOT SETUP, listener, hud-say, tmux
                   snapshot/restore/reaper, user units, ORCHESTRATOR.md, voice runtime build
distro/            Containerfile (bootc image), build-iso.sh, installer kickstart + branding
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
- MAGI is a Fedora remix: it is built from Fedora packages, but it is not Fedora and is not
  produced or endorsed by the Fedora Project. Fedora's logos are replaced by `generic-logos`.
- Voice: [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (MIT) with the
  `base.en` model, and [Piper](https://github.com/OHF-Voice/piper1-gpl) (GPL-3.0-or-later) with
  the `en_US-ljspeech-medium` voice (LJ Speech dataset, public domain).
- The orchestrator uses [Claude Code](https://code.claude.com), which MAGI does not include or
  redistribute; it is installed from Anthropic and used under Anthropic's terms.
