"""magi_vote_cli: the magi-vote command (the hook, the CLI, the terminal display).

magi-vote: three reviewers vote before an agent does something it can't undo.

As a Claude Code PreToolUse hook it reads the tool call on stdin. Harmless calls exit at
once. An irreversible or outward-facing one (git push, rm -r, sending mail, publishing,
prod database writes, payments, system packages) goes to MELCHIOR-1, BALTHASAR-2 and
CASPER-3, three parallel `claude -p` reviews. 2 of 3 approvals let it continue; otherwise
Claude Code asks you. A unit that times out or fails counts as a deny.

  magi-vote on | off | status      register the hook and turn voting on, or off
  magi-vote uninstall              remove the hook from ~/.claude/settings.json
  magi-vote classify CMD...        show what the rules make of a command (no model call)
  magi-vote try CMD...             run a real vote on a command, without running it
  magi-vote demo [--deny]          show the overlay with made-up votes (no model call)
  magi-vote replay [N|FILE]        play the Nth latest logged vote back with its real timings
  magi-vote log [N]                the last N votes
  magi-vote watch                  follow votes live in a terminal
  magi-vote config                 open (create) ~/.config/magi-vote/config.toml
  magi-vote hook                   the hook itself (stdin: PreToolUse JSON)

Works the same with --dangerously-skip-permissions: there the vote is the only check, and
a deny still makes Claude Code stop and ask.
"""
import json
import os
import sys
import time

T0 = float(os.environ.get("MAGI_VOTE_T0") or time.monotonic())
HERE = os.path.dirname(os.path.realpath(__file__))
import magi_vote as mv  # noqa: E402

SETTINGS = os.path.expanduser(os.environ.get("CLAUDE_SETTINGS", "~/.claude/settings.json"))
HOOK_CMD = "~/.local/bin/magi-vote hook"
MATCHER = "^(Bash|mcp__.*)$"
LIVE = mv.STATE_DIR + "/live.json"


# ------------------------------------------------------------------ display helpers

def wayland_env():
    """Environment for the overlay, or None without a Wayland session.

    A hook started from tmux can carry a stale WAYLAND_DISPLAY; fall back to the newest
    socket in XDG_RUNTIME_DIR."""
    if os.environ.get("MAGI_VOTE_NO_OVERLAY") == "1":
        return None
    rt = os.environ.get("XDG_RUNTIME_DIR") or "/run/user/%d" % os.getuid()
    env = dict(os.environ, XDG_RUNTIME_DIR=rt)
    wd = env.get("WAYLAND_DISPLAY")
    if wd and os.path.exists(wd if wd.startswith("/") else os.path.join(rt, wd)):
        return env
    try:
        socks = [s for s in os.listdir(rt) if s.startswith("wayland-") and not s.endswith(".lock")]
    except OSError:
        return None
    if not socks:
        return None
    socks.sort(key=lambda s: os.path.getmtime(os.path.join(rt, s)), reverse=True)
    env["WAYLAND_DISPLAY"] = socks[0]
    return env


class Display:
    """Feeds events to the overlay (JSON lines on its stdin) and to live.json for `watch`."""

    def __init__(self, cfg, call):
        import subprocess
        self.p = None
        self.state = {"t": time.time(), "text": call["text"], "labels": call["labels"], "tool": call["tool"],
                      "cwd": call.get("cwd", ""), "votes": [None, None, None], "verdict": None,
                      "quorum": cfg["quorum"], "bypass": call.get("permission_mode") == "bypassPermissions"}
        env = wayland_env() if cfg.get("overlay", True) else None
        if env:
            try:
                self.p = subprocess.Popen([os.path.join(HERE, "magi-vote-overlay"), "--hold", str(cfg.get("hold", 2.0))],
                                          stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                          env=env, start_new_session=True, text=True)
            except OSError:
                self.p = None
        self.send({"type": "start", **{k: self.state[k] for k in ("text", "labels", "tool", "cwd", "quorum", "bypass")}})

    def send(self, ev):
        if ev["type"] == "vote":
            self.state["votes"][ev["index"]] = {"vote": ev["vote"], "reason": ev["reason"]}
        elif ev["type"] == "verdict":
            self.state["verdict"] = ev
        try:
            os.makedirs(mv.STATE_DIR, exist_ok=True)
            with open(LIVE + ".tmp", "w") as f:
                json.dump(self.state, f, ensure_ascii=False)
            os.replace(LIVE + ".tmp", LIVE)
        except OSError:
            pass
        if self.p and self.p.stdin:
            try:
                self.p.stdin.write(json.dumps(ev, ensure_ascii=False) + "\n")
                self.p.stdin.flush()
            except (OSError, ValueError):
                self.p = None

    def close(self):
        if self.p and self.p.stdin:
            try:
                self.p.stdin.close()  # the overlay holds the verdict, then exits on its own
            except OSError:
                pass


def speak(rec):
    import shutil
    import subprocess
    if not shutil.which("hud-say"):
        return
    if rec["verdict"] == "approve":
        text = "MAGI: approved, %d to %d." % (rec["approvals"], 3 - rec["approvals"])
    else:
        who = [v["unit"].split("-")[0].title() for v in rec["votes"] if v["vote"] == "deny"]
        text = "MAGI: denied by %s. Asking you." % " and ".join(who)
    subprocess.Popen(["hud-say", text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


# ------------------------------------------------------------------ the hook

def emit(decision=None, reason="", message="", context=""):
    out = {}
    if decision:
        out["hookSpecificOutput"] = {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                     "permissionDecisionReason": reason}
    elif context:
        out["hookSpecificOutput"] = {"hookEventName": "PreToolUse", "additionalContext": context}
    if message:
        out["systemMessage"] = message
    if out:
        sys.stdout.write(json.dumps(out, ensure_ascii=False))
    sys.exit(0)


def hook(data=None):
    if os.environ.get("MAGI_VOTE_INSIDE") == "1":
        sys.exit(0)
    if data is None:
        try:
            data = json.load(sys.stdin)
        except ValueError:
            sys.exit(0)
    tool = data.get("tool_name", "")
    if tool != "Bash" and not tool.startswith("mcp__"):
        sys.exit(0)
    cfg = mv.load_config()
    if not cfg.get("enabled", True):
        sys.exit(0)
    hits, text = mv.classify_tool(tool, data.get("tool_input") or {}, cfg)
    action, counted = mv.triage(hits, cfg)
    if action == "pass":
        sys.exit(0)
    t_rules = time.monotonic() - T0
    cls_cost, cls_note = 0.0, ""
    if action == "unsure":
        irrev, cat, why, cls_cost, cached = mv.model_classify(text, cfg)
        if not irrev or (cat != "none" and cat not in cfg["triggers"]):
            mv.log_vote({"t": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "tool": tool, "text": text[:2000],
                         "cwd": data.get("cwd"), "labels": [h.label for h in counted], "verdict": "pass",
                         "classifier": {"irreversible": irrev, "category": cat, "reason": why, "cached": cached},
                         "cost": cls_cost, "latency": round(time.monotonic() - T0, 2)})
            sys.exit(0)
        cls_note = why
    cwd = data.get("cwd") or os.getcwd()
    call = {"tool": tool, "text": text, "cwd": cwd, "labels": [h.label for h in counted] + ([cls_note] if cls_note else []),
            "description": (data.get("tool_input") or {}).get("description", ""),
            "permission_mode": data.get("permission_mode", ""),
            "git": mv.git_context(cwd) if tool == "Bash" else "",
            "conversation": mv.recent_conversation(data.get("transcript_path"))}
    run_vote(call, cfg, data, cls_cost, t_rules)


def run_vote(call, cfg, data=None, cls_cost=0.0, t_rules=0.0, dry=False):
    disp = Display(cfg, call)
    rec = mv.vote(call, cfg, on_vote=lambda i, name, v, reason: disp.send(
        {"type": "vote", "index": i, "unit": name, "vote": v, "reason": reason}))
    disp.send({"type": "verdict", "verdict": rec["verdict"], "approvals": rec["approvals"], "quorum": rec["quorum"],
               "latency": rec["latency"]})
    disp.close()
    rec.update({"t": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "tool": call["tool"], "text": call["text"][:2000],
                "cwd": call.get("cwd"), "labels": call["labels"], "model": cfg["model"],
                "permission_mode": call.get("permission_mode", ""), "session": (data or {}).get("session_id", ""),
                "classifier_cost": cls_cost, "rules_ms": round(t_rules * 1000, 1),
                "total_latency": round(time.monotonic() - T0, 2), "dry_run": dry})
    rec["cost"] = round(rec["cost"] + cls_cost, 6)
    mv.log_vote(rec)
    if cfg.get("speak"):
        speak(rec)
    line = mv.verdict_line(rec)
    if dry:
        return rec
    if rec["verdict"] == "approve":
        if cfg.get("on_approve") == "allow":
            emit("allow", line, line)
        emit(None, message=line, context="The MAGI vote approved this action %d-%d." % (rec["approvals"], 3 - rec["approvals"]))
    # a majority did not approve: never a silent block, the human decides
    emit("ask", line + "\nRun it anyway?", line)


# ------------------------------------------------------------------ settings.json

def load_settings():
    try:
        with open(SETTINGS) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_settings(s):
    import shutil
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    if os.path.exists(SETTINGS):
        shutil.copy2(SETTINGS, SETTINGS + ".magi-vote.bak")
    with open(SETTINGS + ".tmp", "w") as f:
        json.dump(s, f, indent=2)
        f.write("\n")
    os.replace(SETTINGS + ".tmp", SETTINGS)


def hook_installed(s=None):
    s = load_settings() if s is None else s
    for m in s.get("hooks", {}).get("PreToolUse", []):
        for h in m.get("hooks", []):
            if "magi-vote" in h.get("command", ""):
                return True
    return False


def install():
    s = load_settings()
    if hook_installed(s):
        return False
    s.setdefault("hooks", {}).setdefault("PreToolUse", []).append(
        {"matcher": MATCHER, "hooks": [{"type": "command", "command": HOOK_CMD, "timeout": 120}]})
    save_settings(s)
    return True


def uninstall():
    s = load_settings()
    pre = s.get("hooks", {}).get("PreToolUse", [])
    keep = []
    for m in pre:
        m["hooks"] = [h for h in m.get("hooks", []) if "magi-vote" not in h.get("command", "")]
        if m["hooks"]:
            keep.append(m)
    if len(keep) == len(pre) and not hook_installed(s):
        return False
    if keep:
        s["hooks"]["PreToolUse"] = keep
    else:
        s["hooks"].pop("PreToolUse", None)
    save_settings(s)
    return True


def set_enabled(on):
    import re
    os.makedirs(mv.CONFIG_DIR, exist_ok=True)
    if not os.path.exists(mv.CONFIG):
        with open(mv.CONFIG, "w") as f:
            f.write(mv.CONFIG_TEMPLATE)
    with open(mv.CONFIG) as f:
        txt = f.read()
    new, n = re.subn(r"(?m)^enabled\s*=.*$", "enabled = %s" % ("true" if on else "false"), txt)
    if n == 0:
        new = "enabled = %s\n" % ("true" if on else "false") + txt
    with open(mv.CONFIG, "w") as f:
        f.write(new)


# ------------------------------------------------------------------ terminal display

def ansi():
    on = sys.stdout.isatty()
    return {k: (v if on else "") for k, v in dict(r="\033[0m", b="\033[1m", d="\033[2m", red="\033[31m", grn="\033[32m",
                                                     yel="\033[33m", acc="\033[38;5;208m").items()}


def render_tty(state):
    c = ansi()
    out = []
    w = 78
    out.append(c["acc"] + c["b"] + "━" * w + c["r"])
    out.append("%s%s MAGI SYSTEM · 決議%s   %s" % (c["acc"], c["b"], c["r"], c["d"] + "/".join(state.get("labels") or [])[:50] + c["r"]))
    out.append("  " + (state.get("text") or "").splitlines()[0][:w - 2] if state.get("text") else "")
    out.append(c["acc"] + "─" * w + c["r"])
    for i, (name, aspect, _) in enumerate(mv.UNITS):
        v = (state.get("votes") or [None] * 3)[i]
        if v is None:
            mark, reason = c["yel"] + "審議中 DELIBERATING" + c["r"], ""
        elif v["vote"] == "approve":
            mark, reason = c["grn"] + c["b"] + "承認 APPROVED" + c["r"], v["reason"]
        else:
            mark, reason = c["red"] + c["b"] + "否定 DENIED  " + c["r"], v["reason"]
        out.append("  %s%-12s%s %-9s %s" % (c["b"], name, c["r"], c["d"] + aspect + c["r"], mark))
        if reason:
            out.append("  %s└ %s%s" % (c["d"], reason[:w - 4], c["r"]))
    vd = state.get("verdict")
    out.append(c["acc"] + "─" * w + c["r"])
    if vd:
        a = vd["approvals"]
        if vd["verdict"] == "approve":
            out.append("  %s%s決議 承認  %d-%d  PROCEED%s   %.1fs" % (c["grn"], c["b"], a, 3 - a, c["r"], vd.get("latency", 0)))
        else:
            out.append("  %s%s決議 否定  %d-%d  ASKING THE OPERATOR%s   %.1fs" % (c["red"], c["b"], a, 3 - a, c["r"], vd.get("latency", 0)))
    else:
        out.append("  %s…%s" % (c["yel"], c["r"]))
    return "\n".join(out)


def watch():
    last = None
    print("magi-vote: waiting for votes (Ctrl+C to stop)")
    try:
        while True:
            try:
                st = os.stat(LIVE).st_mtime
            except OSError:
                st = None
            if st and st != last:
                last = st
                try:
                    with open(LIVE) as f:
                        state = json.load(f)
                    sys.stdout.write("\033[2J\033[H" if sys.stdout.isatty() else "\n")
                    print(render_tty(state), flush=True)
                except (OSError, ValueError):
                    pass
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass


def show_log(n=10):
    c = ansi()
    try:
        with open(mv.LOG) as f:
            lines = f.readlines()[-n:]
    except OSError:
        print("no votes yet (%s)" % mv.LOG)
        return
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("verdict") == "pass":
            print("%s %s pass  %s%s  (classifier: %s)" % (c["d"], r["t"][:19], r["text"][:60], c["r"], r.get("classifier", {}).get("reason", "")))
            continue
        col = c["grn"] if r["verdict"] == "approve" else c["red"]
        print("%s %s%s %d-%d%s %5.1fs $%.4f  %s" % (r["t"][:19], col, "承認" if r["verdict"] == "approve" else "否定",
                                                  r["approvals"], 3 - r["approvals"], c["r"], r["latency"], r["cost"], r["text"][:60].replace("\n", " ")))
        for v in r["votes"]:
            if v["vote"] == "deny":
                print("    %s%s: %s%s" % (c["d"], v["unit"], v["reason"], c["r"]))


def demo(deny=False, hold=None):
    """Overlay and terminal display with canned votes; no model calls."""
    cfg = mv.load_config()
    if hold is not None:
        cfg["hold"] = hold
    call = {"tool": "Bash", "text": "git push --force origin main", "cwd": os.getcwd(), "labels": ["git push --force"],
            "permission_mode": "bypassPermissions"}
    script = [(0.9, 0, "approve", "Fast-forward is not needed; branch and remote match the request."),
              (1.5, 2, "approve", "User asked to push this branch after the rebase."),
              (2.3, 1, "deny" if deny else "approve",
               "Force-push to main overwrites two commits nobody has pulled." if deny else "Nothing lost: the remote commits are in HEAD.")]
    disp = Display(cfg, call)
    t0 = time.monotonic()
    for at, i, v, reason in script:
        time.sleep(max(0, at - (time.monotonic() - t0)))
        disp.send({"type": "vote", "index": i, "unit": mv.UNITS[i][0], "vote": v, "reason": reason})
        if not disp.p:
            print(render_tty(disp.state) + "\n")
    a = sum(1 for _, _, v, _ in script if v == "approve")
    disp.send({"type": "verdict", "verdict": "approve" if a >= cfg["quorum"] else "deny", "approvals": a,
               "quorum": cfg["quorum"], "latency": round(time.monotonic() - t0, 1)})
    disp.close()
    print(render_tty(disp.state))


def replay(src, hold=None, speed=1.0):
    """Play a logged vote back on the overlay with its real timings (no model calls).

    src: a number (1 = the latest vote in the log) or a JSON file holding one record."""
    cfg = mv.load_config()
    if hold is not None:
        cfg["hold"] = hold
    if os.path.isfile(src):
        with open(src) as f:
            rec = json.load(f)
    else:
        with open(mv.LOG) as f:
            recs = [json.loads(x) for x in f if x.strip()]
        recs = [r for r in recs if r.get("votes")]
        rec = recs[-int(src)]
    call = {"tool": rec.get("tool", "Bash"), "text": rec["text"], "cwd": rec.get("cwd", ""), "labels": rec.get("labels", []),
            "permission_mode": rec.get("permission_mode", "")}
    disp = Display(cfg, call)
    t0 = time.monotonic()
    for i, v in sorted(enumerate(rec["votes"]), key=lambda x: x[1]["latency"]):
        time.sleep(max(0, v["latency"] / speed - (time.monotonic() - t0)))
        disp.send({"type": "vote", "index": i, "unit": v["unit"], "vote": v["vote"], "reason": v["reason"]})
    disp.send({"type": "verdict", "verdict": rec["verdict"], "approvals": rec["approvals"], "quorum": rec["quorum"],
               "latency": rec["latency"]})
    disp.close()
    print(render_tty(disp.state))


def main():
    a = sys.argv[1:]
    cmd = a[0] if a else "status"
    if cmd == "hook":
        hook()
    elif cmd in ("-h", "--help", "help"):
        print(__doc__[__doc__.index("magi-vote:"):].strip())
    elif cmd == "on":
        added = install()
        set_enabled(True)
        print("magi-vote: on%s. Config: %s" % (" (hook added to %s)" % SETTINGS if added else "", mv.CONFIG))
        print("New Claude Code sessions pick it up; running ones after /hooks or a restart.")
    elif cmd == "off":
        set_enabled(False)
        print("magi-vote: off (the hook stays registered and exits at once; `magi-vote uninstall` removes it)")
    elif cmd == "uninstall":
        print("magi-vote: hook removed from %s" % SETTINGS if uninstall() else "magi-vote: hook was not installed")
    elif cmd == "status":
        cfg = mv.load_config()
        print("enabled:   %s" % ("yes" if cfg["enabled"] else "no"))
        print("hook:      %s" % ("registered in %s" % SETTINGS if hook_installed() else "not registered (magi-vote on)"))
        print("model:     %s, quorum %d of 3, timeout %ss" % (cfg["model"], cfg["quorum"], cfg["timeout"]))
        print("triggers:  %s" % ", ".join(cfg["triggers"]))
        print("unclear:   %s;  on approve: %s;  overlay: %s;  speak: %s" % (cfg["ambiguous"], cfg["on_approve"], cfg["overlay"], cfg["speak"]))
        if cfg.get("config_error"):
            print("config error (defaults used): %s" % cfg["config_error"])
        print("log:       %s" % mv.LOG)
    elif cmd == "classify":
        text = " ".join(a[1:]) if len(a) > 1 else sys.stdin.read()
        cfg = mv.load_config()
        hits = mv.classify_command(text)
        action, counted = mv.triage(hits, cfg)
        print("%s  (%.1f ms)" % (action.upper(), (time.monotonic() - T0) * 1000))
        for h in hits:
            print("  %-9s %s%s" % (h.category, h.label, "" if h.sure else "  [unsure]"))
    elif cmd == "try":
        text = " ".join(a[1:])
        cfg = mv.load_config()
        hits, _ = mv.classify_tool("Bash", {"command": text}, cfg)
        call = {"tool": "Bash", "text": text, "cwd": os.getcwd(), "labels": [h.label for h in hits] or ["manual"],
                "git": mv.git_context(os.getcwd()), "conversation": os.environ.get("MAGI_VOTE_CONTEXT", "")}
        rec = run_vote(call, cfg, dry=True)
        print(mv.verdict_line(rec))
        print("cost $%.5f, %.1f s" % (rec["cost"], rec["latency"]))
    elif cmd == "demo":
        hold = None
        if "--hold" in a:
            hold = float(a[a.index("--hold") + 1])
        demo(deny="--deny" in a, hold=hold)
    elif cmd == "replay":
        hold = float(a[a.index("--hold") + 1]) if "--hold" in a else None
        src = next((x for x in a[1:] if not x.startswith("--") and (len(a) < 2 or x != (a[a.index("--hold") + 1] if "--hold" in a else None))), "1")
        replay(src, hold)
    elif cmd == "log":
        show_log(int(a[1]) if len(a) > 1 else 10)
    elif cmd == "watch":
        watch()
    elif cmd == "config":
        if not os.path.exists(mv.CONFIG):
            os.makedirs(mv.CONFIG_DIR, exist_ok=True)
            with open(mv.CONFIG, "w") as f:
                f.write(mv.CONFIG_TEMPLATE)
        ed = os.environ.get("EDITOR")
        if ed and sys.stdout.isatty():
            os.execvp(ed, [ed, mv.CONFIG])
        print(mv.CONFIG)
    else:
        print("magi-vote: unknown command %r (magi-vote --help)" % cmd, file=sys.stderr)
        sys.exit(2)


def run(argv, t0=None, data=None):
    global T0
    if t0 is not None:
        T0 = t0
    sys.argv = ["magi-vote"] + argv
    if argv[:1] == ["hook"] and data is not None:
        hook(data)
    main()
