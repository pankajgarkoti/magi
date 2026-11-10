#!/bin/sh
# TEST ONLY: stands in for an agent's official installer, with the same layout:
#   claude   https://claude.ai/install.sh          ~/.local/bin/claude -> ~/.local/share/claude/versions/<v>
#   opencode https://opencode.ai/install           ~/.opencode/bin/opencode
#   codex    https://chatgpt.com/codex/install.sh  ~/.local/bin/codex
# Used via MAGI_<AGENT>_INSTALL_URL=file://.../install-stub.sh with STUB_AGENT=<agent>.
# POSIX sh, because the Codex installer is piped into sh.
set -e
agent=${STUB_AGENT:?STUB_AGENT=claude|opencode|codex}
stub=${AGENT_STUB:-/var/tmp/magi-test/agent-stub}
echo "Installing $agent (stub, test only)..."
case "$agent" in
  claude) mkdir -p ~/.local/share/claude/versions ~/.local/bin
          cp "$stub" ~/.local/share/claude/versions/0.0.0-stub; chmod +x ~/.local/share/claude/versions/0.0.0-stub
          ln -sfn ~/.local/share/claude/versions/0.0.0-stub ~/.local/bin/claude; dest=~/.local/bin/claude ;;
  opencode) mkdir -p ~/.opencode/bin; cp "$stub" ~/.opencode/bin/opencode; chmod 755 ~/.opencode/bin/opencode
            dest=~/.opencode/bin/opencode ;;
  codex) mkdir -p ~/.local/bin; cp "$stub" ~/.local/bin/codex; chmod 755 ~/.local/bin/codex; dest=~/.local/bin/codex ;;
  *) echo "unknown agent $agent" >&2; exit 1 ;;
esac
sleep 1; echo "✔ $agent installed to $dest (stub)"
