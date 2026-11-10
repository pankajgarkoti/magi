#!/usr/bin/env bash
# TEST ONLY: stands in for https://claude.ai/install.sh (same layout: ~/.local/bin/claude ->
# ~/.local/share/claude/versions/<v>). Used via MAGI_CLAUDE_INSTALL_URL=file://.../install-stub.sh
set -e
echo "Installing Claude Code (stub, test only)..."
mkdir -p ~/.local/share/claude/versions ~/.local/bin
cp "${CLAUDE_STUB:-/var/tmp/magi-test/claude-stub}" ~/.local/share/claude/versions/0.0.0-stub
chmod +x ~/.local/share/claude/versions/0.0.0-stub
ln -sfn ~/.local/share/claude/versions/0.0.0-stub ~/.local/bin/claude
sleep 1; echo "✔ Claude Code installed to ~/.local/bin/claude (stub)"
