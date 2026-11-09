# MAGI shell snippet (sourced by Fedora's default ~/.bashrc from ~/.bashrc.d).
# ~/.local/bin holds the MAGI tools (eva-theme, magi, wall-next, magi-update...).
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) PATH="$HOME/.local/bin:$PATH" ;; esac
export PATH

# MAGI boot banner: top-level interactive terminals only (not every tmux pane),
# plus the MAGI scratchpad session. Set MAGI_NO_BANNER=1 to silence it.
if [[ $- == *i* && -z ${MAGI_NO_BANNER:-} ]] && command -v magi-banner >/dev/null; then
  if [ -z "${TMUX:-}" ] || [ "$(tmux display-message -p '#S' 2>/dev/null)" = magi ]; then
    magi-banner
  fi
fi
