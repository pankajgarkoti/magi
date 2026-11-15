#!/usr/bin/env bash
# MAGI agent layer for an install.sh setup (the MAGI image already contains all of this).
# Opt-in. Links the agent tools into ~/.local/bin, the user units into ~/.config/systemd/user,
# and builds the local voice runtime in ~/.local/share/magi-voice (venv with faster-whisper +
# piper-tts, whisper base.en, Piper en_US-ljspeech-medium: ~600 MB, downloaded once; after that
# the listener and the voice work offline). Then opens PILOT SETUP inside a MAGI session.
#   agent/setup.sh [--no-voice]
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
VH=${MAGI_VOICE_HOME:-$HOME/.local/share/magi-voice}
voice=1; [ "${1:-}" = --no-voice ] && voice=0
mkdir -p "$HOME/.local/bin" "$HOME/.config/systemd/user"
for f in "$here"/bin/*; do ln -sfn "$f" "$HOME/.local/bin/$(basename "$f")"; done
for f in "$here"/systemd/*; do ln -sfn "$f" "$HOME/.config/systemd/user/$(basename "$f")"; done
systemctl --user daemon-reload 2>/dev/null || true
# the event bus, browser-bus and the health checks run whether or not the orchestrator is on
systemctl --user enable --now orch-bus.service browser-bus.service orch-doctor.timer orch-open-threads.timer 2>/dev/null || true
probes=${XDG_CONFIG_HOME:-$HOME/.config}/browser-bus/probes
[ -d "$probes" ] || { mkdir -p "$probes"; cp "$here/browser-bus/examples/unread-titles.js" "$probes/"; }
"$here/bin/orch" init >/dev/null 2>&1 || true
echo "linked $(ls "$here/bin" | wc -l) tools and $(ls "$here/systemd" | wc -l) user units"
if [ "$voice" = 1 ]; then
  "$here/voice/build.sh" "$VH"
fi
echo "Done. In a MAGI session: magi-agent setup (or the AGENTS page, SUPER+SHIFT+Space)."
