#!/usr/bin/env python3
"""Agent adapters for the MAGI orchestrator layer.

MAGI runs terminal agents in tmux. tmux is the orchestration layer and works the same for
every agent (send-keys, capture-pane, windows). What differs per agent is handled here:

  install      the agent's own official installer, run at first login (nothing is bundled)
  sign-in      check and start the agent's own sign-in (the user's account)
  launch       the command for a new session, with MAGI's orchestrator instructions
  resume       the command that reopens a session after a reboot, or None (start fresh)
  state        idle / busy / asking for an agent running in a tmux pane, and for how long

State comes from the agent's own status files or API where it has them (Claude Code's
~/.claude/sessions/<pid>.json, opencode's local server API). Otherwise it is read from the
pane: a permission or menu prompt is "asking", a working indicator is "busy", anything else
with the agent's process alive is "idle". Idle time for pane-read agents is tracked in
~/.local/state/magi/agent/panes.json.

Settings, ~/.config/magi/agent.conf (shell syntax):
  AGENT=claude|opencode|codex|custom   the orchestrator's agent (default claude)
  SUBAGENT=...                         agents the orchestrator starts (default: AGENT)
  AGENT_CMD='...'                      custom: the command that starts it
  AGENT_PROCESS=name                   custom: its process name (default: first word of AGENT_CMD)
  AGENT_RESUME='... {id}'              custom: optional; without it a custom agent starts fresh
  AGENT_INSTALL='...'                  custom: optional install command
  AGENT_BUSY_RE / AGENT_ASK_RE         custom: regexes for its working / asking screens
  AGENT_PROMPT='> '                    custom: its prompt, so a typed draft is never reaped
  AGENT_ARGS='...'                     extra arguments for the orchestrator (any agent)

CLI (used by magi-agent and the setup window):
  magi_agents.py name [orchestrator|agents]     configured adapter name
  magi_agents.py label [NAME]                   its display name ("Claude Code", "opencode", ...)
  magi_agents.py info [NAME]                    JSON: label, installed, signed_in, ...
  magi_agents.py installed|signed-in [NAME]     exit status 0 = yes
  magi_agents.py install [NAME]                 run the official installer
  magi_agents.py login [NAME]                   run the agent's sign-in
  magi_agents.py run-orchestrator [--resume ID] exec the orchestrator agent (tmux window 1)
  magi_agents.py spawn WINDOW DIR [BRIEF] [--agent NAME]   start an agent in a new tmux window
  magi_agents.py rows                           one TSV line per agent pane (bar, AGENTS page)
  magi_agents.py list                           JSON for every agent pane
"""
import base64
import calendar
import hashlib
import json
import os
import pathlib
import re
import secrets
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
import uuid

HOME = pathlib.Path.home()
CONF = pathlib.Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config")) / "magi/agent.conf"
STATE = pathlib.Path(os.environ.get("XDG_STATE_HOME", HOME / ".local/state")) / "magi/agent"
ORCH_DIR = pathlib.Path(os.environ.get("XDG_STATE_HOME", HOME / ".local/state")) / "magi/orchestrator"
SESSION = "magi"
HERE = pathlib.Path(os.path.realpath(__file__)).parent
# extra places the official installers put binaries, so a fresh install works before a re-login
EXTRA_PATH = [HOME / ".local/bin", HOME / ".opencode/bin"]

# Prompts that wait on the user, common to most terminal agents.
ASK_COMMON = (r"Do you want|Allow this|Allow once|Allow always|Permission required|Would you like to|"
              r"Yes, proceed|Press enter to (continue|confirm)|Enter to select|Esc to cancel|"
              r"\[y/N\]|\[Y/n\]|\(y/n\)|Waiting for authorization|Sign in with")


def conf():
    c = {}
    try:
        for line in CONF.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            try:
                v = " ".join(shlex.split(v)) if v else ""
            except ValueError:
                pass
            c[k.strip()] = v
    except OSError:
        pass
    return c


def which(cmd):
    path = os.environ.get("PATH", "").split(":") + [str(p) for p in EXTRA_PATH]
    for d in path:
        if d and os.access(os.path.join(d, cmd), os.X_OK) and not os.path.isdir(os.path.join(d, cmd)):
            return os.path.join(d, cmd)
    return None


def orchestrator_md():
    for c in ("/usr/share/magi/agent/ORCHESTRATOR.md", HERE.parent / "ORCHESTRATOR.md",
              HOME / ".local/share/magi/agent/ORCHESTRATOR.md"):
        if os.path.isfile(c):
            return os.path.realpath(c)
    return None


def orchestrator_dir():
    """A folder whose AGENTS.md is MAGI's ORCHESTRATOR.md: opencode, Codex and most other
    agents read AGENTS.md from their working folder, so this works without agent flags."""
    ORCH_DIR.mkdir(parents=True, exist_ok=True)
    md = orchestrator_md()
    for name in ("AGENTS.md",):
        link = ORCH_DIR / name
        if md and (not link.is_symlink() or os.readlink(link) != md):
            try:
                link.unlink()
            except OSError:
                pass
            link.symlink_to(md)
    return str(ORCH_DIR)


def sh(*a, timeout=5):
    try:
        return subprocess.run(a, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def proc_argv(pid):
    try:
        return [a.decode(errors="replace") for a in pathlib.Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0") if a]
    except OSError:
        return []


def proc_start(pid):
    """Process start time, seconds since the epoch."""
    try:
        st = pathlib.Path(f"/proc/{pid}/stat").read_text()
        ticks = int(st.rsplit(")", 1)[1].split()[19])
        btime = next(int(l.split()[1]) for l in open("/proc/stat") if l.startswith("btime"))
        return btime + ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, StopIteration):
        return time.time()


def flag_value(argv, *names):
    for i, a in enumerate(argv):
        for n in names:
            if a == n and i + 1 < len(argv):
                return argv[i + 1]
            if a.startswith(n + "="):
                return a.split("=", 1)[1]
    return None


def keep_flags(argv, with_value, plain):
    flags, i = [], 1
    while i < len(argv):
        a = argv[i]
        key = a.split("=", 1)[0]
        if key in plain:
            flags.append(a)
        elif key in with_value:
            if "=" in a:
                flags.append(a)
            elif i + 1 < len(argv):
                flags += [a, argv[i + 1]]
                i += 1
        i += 1
    return flags


class Agent:
    name = "?"
    label = "?"
    process = ()           # process names (comm, argv[0] or a node script's argv[1] basename)
    binary = None
    install_cmd = None     # shell command: the agent's official installer
    install_note = ""
    install_shown = ""     # the official command as its docs show it
    licence = ""
    needs_network = True
    has_signin = True
    resumable = True
    state_source = "pane"
    busy_re = r"esc to interrupt"
    ask_re = ASK_COMMON
    prompt_char = None     # draft detection: text after this on the last prompt line

    def __init__(self, c=None):
        self.c = c or {}

    # ---- install and sign-in
    def path(self):
        return which(self.binary) if self.binary else None

    def installed(self):
        return self.path() is not None

    def signed_in(self):
        return True

    def login_argv(self):
        return None

    def install(self):
        if not self.install_cmd:
            print(f"{self.label}: no installer configured")
            return 1
        if self.installed():
            print(f"{self.label} already installed: {self.path()}")
            return 0
        print(f"Running the official installer: {self.install_cmd}", flush=True)
        return subprocess.call(["bash", "-c", "set -o pipefail; " + self.install_cmd])

    # ---- launch / resume
    def launch(self, role, prompt=None, resume=None):
        """(argv, cwd or None, extra env) for a new or resumed session."""
        raise NotImplementedError

    def mint_id(self):
        return None

    # ---- state, for one agent process in a tmux pane
    def native_state(self, pane, pid):
        """dict(state, since, session_id, cwd) from the agent's own files/API, or None."""
        return None

    def session_id(self, pane, pid):
        return None

    def resume_cmd(self, session_id, flags, cwd):
        """argv that reopens the session, or None (the agent can't resume: start fresh)."""
        return None

    def saved_flags(self, argv):
        return []

    def draft(self, pane):
        if not self.prompt_char:
            return False
        lines = [l for l in sh("tmux", "capture-pane", "-p", "-e", "-t", pane).splitlines() if self.prompt_char in l]
        if not lines:
            return False
        after = lines[-1].split(self.prompt_char, 1)[1]
        if "\x1b[2m" in after:  # dim text is a suggestion, not a draft
            return False
        return bool(re.sub(r"\x1b\[[0-9;]*m", "", after).strip())


class Claude(Agent):
    name, label, process, binary = "claude", "Claude Code", ("claude",), "claude"
    install_cmd = "curl -fsSL ${MAGI_CLAUDE_INSTALL_URL:-https://claude.ai/install.sh} | bash"
    install_shown = "curl -fsSL https://claude.ai/install.sh | bash"
    install_note = "Anthropic's official installer, into ~/.local/bin"
    licence = "https://code.claude.com/docs/en/legal-and-compliance"
    state_source = "~/.claude/sessions/<pid>.json"
    ask_re = r"Enter to select|Do you want|Allow this|Esc to cancel|\[y/N\]|\(y/n\)"
    prompt_char = "❯"
    KEEP_V = {"--permission-mode", "--model", "--effort", "--name", "--add-dir", "--append-system-prompt-file"}
    KEEP = {"--dangerously-skip-permissions", "--chrome", "--verbose"}

    def signed_in(self):
        try:
            out = sh(self.path() or "claude", "auth", "status", "--json", timeout=10)
            return bool(json.loads(out).get("loggedIn"))
        except (ValueError, AttributeError):
            return False

    def login_argv(self):
        return [self.path() or "claude", "auth", "login"]

    def launch(self, role, prompt=None, resume=None):
        argv = [self.path() or "claude"]
        if resume:
            argv += ["--resume", resume]
        md = orchestrator_md()
        if role == "orchestrator" and md:
            argv += ["--append-system-prompt-file", md]
        if prompt:
            argv.append(prompt)
        return argv, None, {}

    def _status_file(self, pid):
        try:
            return json.loads((HOME / ".claude/sessions" / f"{pid}.json").read_text())
        except (OSError, ValueError):
            return None

    def native_state(self, pane, pid):
        d = self._status_file(pid)
        if not d:
            return None
        st = {"busy": "busy", "idle": "idle", "waiting": "idle"}.get(d.get("status"), "idle")
        return {"state": st, "since": d.get("statusUpdatedAt", time.time() * 1000) / 1000,
                "session_id": d.get("sessionId"), "cwd": d.get("cwd")}

    def session_id(self, pane, pid):
        d = self._status_file(pid)
        return d and d.get("sessionId")

    def saved_flags(self, argv):
        return keep_flags(argv, self.KEEP_V, self.KEEP)

    def resume_cmd(self, session_id, flags, cwd):
        return [self.path() or "claude", "--resume", session_id, *flags] if session_id else None


class OpenCode(Agent):
    name, label, process, binary = "opencode", "opencode", ("opencode",), "opencode"
    install_cmd = "curl -fsSL ${MAGI_OPENCODE_INSTALL_URL:-https://opencode.ai/install} | bash -s -- --no-modify-path"
    install_shown = "curl -fsSL https://opencode.ai/install | bash"
    install_note = "opencode's official installer, into ~/.opencode/bin"
    licence = "https://github.com/anomalyco/opencode (MIT)"
    state_source = "opencode server API (active sessions, permission requests)"
    has_signin = False   # free models work without an account; providers are added with /connect
    busy_re = r"esc interrupt|esc to interrupt"
    KEEP_V = {"--model", "-m", "--agent"}
    KEEP = {"--auto"}

    def login_argv(self):
        return [self.path() or "opencode", "auth", "login"]

    def mint_id(self):
        # opencode creates a session with the id given to --session; MAGI picks it, so state
        # and resume map to the right session without guessing
        return "ses_magi" + secrets.token_hex(11)

    def launch(self, role, prompt=None, resume=None):
        sid = resume or self.mint_id()
        argv = [self.path() or "opencode", "--session", sid]
        if prompt:
            argv += ["--prompt", prompt]
        return argv, orchestrator_dir() if role == "orchestrator" else None, {}

    def _api(self, path):
        sj = pathlib.Path(os.environ.get("XDG_STATE_HOME", HOME / ".local/state")) / "opencode/service.json"
        s = json.loads(sj.read_text())
        hdr = {}
        if s.get("password"):
            hdr["Authorization"] = "Basic " + base64.b64encode(f"opencode:{s['password']}".encode()).decode()
        req = urllib.request.Request(s["url"].rstrip("/") + path, headers=hdr)
        return json.loads(urllib.request.urlopen(req, timeout=1.5).read())

    def session_id(self, pane, pid):
        return flag_value(proc_argv(pid), "--session", "-s")

    def native_state(self, pane, pid):
        # checked against opencode 2.2.5: /api/session/active lists running sessions (a run that
        # ends interrupted or declined never updates time.idle, so busy/idle times alone can't
        # tell), and pending permission prompts are per session
        sid = self.session_id(pane, pid)
        if not sid:
            return None
        try:
            info = self._api(f"/api/session/{sid}")["data"]
            running = sid in (self._api("/api/session/active").get("data") or {})
        except Exception:
            return None   # not created yet (first message not sent) or no server: read the pane
        try:
            perms = self._api(f"/api/session/{sid}/permission").get("data") or []
        except Exception:
            perms = []
        t = info.get("time", {})
        st = "asking" if perms else ("busy" if running else "idle")
        since = max(t.get("busy") or 0, t.get("idle") or 0, t.get("updated") or 0, t.get("created") or 0) / 1000 or None
        return {"state": st, "since": since, "session_id": sid, "cwd": (info.get("location") or {}).get("directory")}

    def saved_flags(self, argv):
        return keep_flags(argv, self.KEEP_V, self.KEEP)

    def resume_cmd(self, session_id, flags, cwd):
        return [self.path() or "opencode", "--session", session_id, *flags] if session_id else None


class Codex(Agent):
    name, label, process, binary = "codex", "Codex", ("codex",), "codex"
    install_cmd = "curl -fsSL ${MAGI_CODEX_INSTALL_URL:-https://chatgpt.com/codex/install.sh} | CODEX_NON_INTERACTIVE=1 sh"
    install_shown = "curl -fsSL https://chatgpt.com/codex/install.sh | sh"
    install_note = "OpenAI's official installer, into ~/.local/bin"
    licence = "https://github.com/openai/codex (Apache-2.0)"
    state_source = "Codex rollout file (task_started / task_complete) + tmux pane for approvals"
    busy_re = r"esc to interrupt"
    prompt_char = "›"
    KEEP_V = {"--model", "-m", "--sandbox", "-s", "--ask-for-approval", "-a", "--profile", "-p", "--add-dir"}
    KEEP = {"--search", "--dangerously-bypass-approvals-and-sandbox", "--approve-for-me"}

    def signed_in(self):
        # a provider other than OpenAI (config.toml model_provider, or --oss) needs no Codex login;
        # `codex login status` says "Not logged in" then
        if "--oss" in shlex.split(self.c.get("AGENT_ARGS", "")):
            return True
        try:
            import tomllib
            home = pathlib.Path(os.environ.get("CODEX_HOME", HOME / ".codex"))
            prov = tomllib.loads((home / "config.toml").read_text()).get("model_provider")
            if prov and prov != "openai":
                return True
        except Exception:
            pass
        try:
            return subprocess.run([self.path() or "codex", "login", "status"], capture_output=True, timeout=10).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def login_argv(self):
        return [self.path() or "codex", "login"]

    def launch(self, role, prompt=None, resume=None):
        argv = [self.path() or "codex"]
        if resume:
            argv += ["resume", resume]
        if prompt:
            argv.append(prompt)
        return argv, orchestrator_dir() if role == "orchestrator" else None, {}

    def _thread(self, pid):
        """(thread id, rollout file) of the Codex session running as pid."""
        argv = proc_argv(pid)
        want = None
        if "resume" in argv:
            k = argv.index("resume")
            if k + 1 < len(argv) and not argv[k + 1].startswith("-"):
                want = argv[k + 1]
        home = pathlib.Path(os.environ.get("CODEX_HOME", HOME / ".codex"))
        cwd = os.path.realpath(f"/proc/{pid}/cwd") if os.path.exists(f"/proc/{pid}/cwd") else None
        start = proc_start(pid)
        for db in sorted(home.glob("state_*.sqlite"), reverse=True):
            try:
                con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1)
                if want:
                    rows = con.execute("select id, created_at, rollout_path from threads where id = ?", (want,)).fetchall()
                else:
                    # the thread Codex created in this pane's folder after this process started
                    rows = con.execute("select id, created_at, rollout_path from threads where cwd = ? and archived = 0 "
                                       "order by created_at", (cwd,)).fetchall()
                con.close()
            except sqlite3.Error:
                continue
            for tid, created, rollout in rows:
                created = created / 1000 if created > 10**11 else created
                if want or created >= start - 5:
                    return tid, rollout
            if want:
                return want, None
        return want, None

    def session_id(self, pane, pid):
        return self._thread(pid)[0]

    def native_state(self, pane, pid):
        # checked against Codex 0.167: the rollout file gets task_started when a turn begins and
        # task_complete or turn_aborted when it ends; an approval prompt only shows in the pane
        tid, rollout = self._thread(pid)
        if not rollout or not os.path.exists(rollout):
            return None
        try:
            with open(rollout, "rb") as f:
                f.seek(max(0, os.path.getsize(rollout) - 262144))
                lines = f.read().decode(errors="replace").splitlines()
        except OSError:
            return None
        last, when = None, None
        for line in lines:
            if '"event_msg"' not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            t = (d.get("payload") or {}).get("type")
            if t in ("task_started", "task_complete", "turn_aborted"):
                last, when = t, d.get("timestamp")
        if last is None:
            return None
        since = os.path.getmtime(rollout)
        if when:
            try:
                since = calendar.timegm(time.strptime(when[:19], "%Y-%m-%dT%H:%M:%S"))
            except ValueError:
                pass
        st = "busy" if last == "task_started" else "idle"
        if re.search(self.ask_re, screen_tail(pane), re.I):
            st = "asking"
        return {"state": st, "since": since, "session_id": tid, "cwd": None}

    def saved_flags(self, argv):
        return keep_flags(argv, self.KEEP_V, self.KEEP)

    def resume_cmd(self, session_id, flags, cwd):
        # without a known thread id, the most recent session in the same folder
        return [self.path() or "codex", "resume", *([session_id] if session_id else ["--last"]), *flags]


class Custom(Agent):
    name, label = "custom", "custom command"
    has_signin = False
    needs_network = False
    state_source = "tmux pane"

    def __init__(self, c=None):
        super().__init__(c)
        self.cmd = self.c.get("AGENT_CMD", "")
        words = shlex.split(self.cmd) if self.cmd else []
        self.binary = words[0] if words else None
        self.process = (self.c.get("AGENT_PROCESS") or (os.path.basename(words[0]) if words else ""),)
        self.install_cmd = self.c.get("AGENT_INSTALL") or None
        self.resumable = bool(self.c.get("AGENT_RESUME"))
        # how its pane looks when working / waiting on the user, if the defaults don't fit
        self.busy_re = self.c.get("AGENT_BUSY_RE") or Agent.busy_re
        self.ask_re = self.c.get("AGENT_ASK_RE") or Agent.ask_re
        self.prompt_char = self.c.get("AGENT_PROMPT") or None
        if self.cmd:
            self.label = os.path.basename(words[0])

    def launch(self, role, prompt=None, resume=None):
        if resume and self.c.get("AGENT_RESUME"):
            argv = shlex.split(self.c["AGENT_RESUME"].replace("{id}", resume))
        else:
            argv = shlex.split(self.cmd)
        return argv, orchestrator_dir() if role == "orchestrator" else None, {}

    def resume_cmd(self, session_id, flags, cwd):
        tmpl = self.c.get("AGENT_RESUME")
        if tmpl and session_id:
            return shlex.split(tmpl.replace("{id}", session_id))
        return None


ADAPTERS = {"claude": Claude, "opencode": OpenCode, "codex": Codex, "custom": Custom}


def get(name=None, role="orchestrator", c=None):
    c = conf() if c is None else c
    if name is None:
        name = c.get("AGENT") or "claude"
        if role == "agents":
            name = c.get("SUBAGENT") or name
    return ADAPTERS.get(name, Claude)(c)


def all_adapters(c=None):
    c = conf() if c is None else c
    out = [cls(c) for cls in (Claude, OpenCode, Codex)]
    if c.get("AGENT_CMD"):
        out.append(Custom(c))
    return out


# ---------------------------------------------------------------- finding agents in tmux

def _children():
    kids = {}
    for p in pathlib.Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            ppid = int((p / "stat").read_text().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            continue
        kids.setdefault(ppid, []).append(int(p.name))
    return kids


def _names(pid):
    names = set()
    try:
        names.add(pathlib.Path(f"/proc/{pid}/comm").read_text().strip())
    except OSError:
        pass
    argv = proc_argv(pid)
    if argv:
        names.add(os.path.basename(argv[0]))
        if os.path.basename(argv[0]) in ("node", "bun", "python3", "python") and len(argv) > 1:
            names.add(os.path.basename(argv[1]).removesuffix(".js"))
    return names


def find_agent(root, kids, adapters):
    """The first agent process at or below root (breadth first), as (adapter, pid)."""
    queue = [root]
    while queue:
        pid = queue.pop(0)
        names = _names(pid)
        for a in adapters:
            if names & set(filter(None, a.process)):
                return a, pid
        queue += kids.get(pid, [])
    return None, None


def screen_tail(pane, n=15, screen=None):
    """The last n non-empty lines of a pane: where prompts and working indicators are."""
    screen = sh("tmux", "capture-pane", "-p", "-t", pane) if screen is None else screen
    return "\n".join([l for l in screen.splitlines() if l.strip()][-n:])


def pane_state(a, pane):
    """(state, screen hash) read from the pane."""
    screen = sh("tmux", "capture-pane", "-p", "-t", pane)
    tail = screen_tail(pane, screen=screen)
    h = hashlib.sha1(screen.encode()).hexdigest()[:16]
    if re.search(a.ask_re, tail, re.I):
        return "asking", h
    if re.search(a.busy_re, tail, re.I):
        return "busy", h
    return "idle", h


def _load_track():
    try:
        return json.loads((STATE / "panes.json").read_text())
    except (OSError, ValueError):
        return {}


def _save_track(t):
    STATE.mkdir(parents=True, exist_ok=True)
    tmp = STATE / f"panes.json.{os.getpid()}"
    tmp.write_text(json.dumps(t))
    os.replace(tmp, STATE / "panes.json")


def agents(c=None):
    """Every agent running in a tmux pane on the default server."""
    c = conf() if c is None else c
    adapters = all_adapters(c)
    fmt = "#{pane_id}\t#S\t#W\t#{pane_pid}\t#{pane_current_path}\t#{session_group}"
    kids = _children()
    track, out, now = _load_track(), [], time.time()
    seen = set()
    for line in sh("tmux", "list-panes", "-a", "-F", fmt).splitlines():
        p = line.split("\t")
        if len(p) != 6 or p[0] in seen:
            continue
        seen.add(p[0])
        pane, sess, win, ppid, cwd, group = p
        a, pid = find_agent(int(ppid), kids, adapters)
        if not a:
            continue
        nat = a.native_state(pane, pid)
        if nat:
            st, since = nat["state"], nat.get("since") or now
            sid, scwd = nat.get("session_id"), nat.get("cwd") or cwd
            source = a.state_source
            if st != "asking" and re.search(a.ask_re, screen_tail(pane), re.I):
                st = "asking"
        else:
            # idle time: how long the pane has shown the same idle screen; any change restarts it,
            # so output the patterns miss still counts as activity
            st, h = pane_state(a, pane)
            prev = track.get(pane) or {}
            same = prev.get("state") == st and prev.get("pid") == pid and prev.get("hash") in (None, h)
            since = prev["since"] if same else now
            sid, scwd, source = None, cwd, "tmux pane"
        track[pane] = {"state": st, "since": since, "pid": pid, **({"hash": h} if not nat else {})}
        if sid is None:
            sid = a.session_id(pane, pid)
        out.append({"pane": pane, "session": group or sess, "window": win, "agent": a.name, "pid": pid,
                    "state": st, "idle_min": int(max(0, now - since) / 60), "session_id": sid,
                    "cwd": scwd, "flags": a.saved_flags(proc_argv(pid)), "source": source})
    live = {r["pane"] for r in out}
    _save_track({k: v for k, v in track.items() if k in live})
    return out


# ---------------------------------------------------------------- CLI

def _tmux_new_window(window, cwd, argv, env, then_shell=True):
    cmd = " ".join(shlex.quote(x) for x in argv)
    if env:
        cmd = "env " + " ".join(shlex.quote(f"{k}={v}") for k, v in env.items()) + " " + cmd
    if then_shell:
        cmd += "; exec bash"
    if subprocess.run(["tmux", "has-session", "-t", f"={SESSION}"], capture_output=True).returncode:
        r = subprocess.run(["tmux", "new-session", "-d", "-s", SESSION, "-n", window, "-c", cwd, cmd])
    else:
        r = subprocess.run(["tmux", "new-window", "-d", "-t", f"={SESSION}:", "-n", window, "-c", cwd, cmd])
    if r.returncode == 0:
        subprocess.run(["tmux", "set-option", "-w", "-t", f"={SESSION}:={window}", "automatic-rename", "off"],
                       capture_output=True)
    return r.returncode


def main(argv):
    cmd = argv[0] if argv else "rows"
    rest = argv[1:]
    if cmd == "name":
        print(get(role=rest[0] if rest else "orchestrator").name)
        return 0
    if cmd in ("info", "label", "installed", "signed-in", "install", "login", "needs-network"):
        a = get(rest[0]) if rest else get()
        if cmd == "label":
            print(a.label)
            return 0
        if cmd == "info":
            print(json.dumps({"name": a.name, "label": a.label, "installed": a.installed(), "path": a.path(),
                              "has_signin": a.has_signin, "signed_in": a.installed() and a.signed_in(),
                              "resumable": a.resumable, "state_source": a.state_source,
                              "install_cmd": a.install_cmd, "install_shown": a.install_shown or a.install_cmd, "install_note": a.install_note, "licence": a.licence,
                              "needs_network": a.needs_network,
                              "login": " ".join(a.login_argv() or []) or None}))
            return 0
        if cmd == "installed":
            return 0 if a.installed() else 1
        if cmd == "signed-in":
            return 0 if a.installed() and a.signed_in() else 1
        if cmd == "needs-network":
            return 0 if a.needs_network else 1
        if cmd == "install":
            return a.install()
        if cmd == "login":
            la = a.login_argv()
            return subprocess.call(la) if la else 0
    if cmd == "run-orchestrator":
        a = get()
        resume = flag_value(["x", *rest], "--resume")
        largv, cwd, env = a.launch("orchestrator", resume=resume)
        largv += shlex.split(conf().get("AGENT_ARGS", ""))
        os.environ.update(env)
        md = orchestrator_md()
        if md:
            os.environ["MAGI_INSTRUCTIONS"] = md
        os.chdir(cwd or str(HOME))
        os.execvp(largv[0], largv)
    if cmd == "spawn":
        agent = flag_value(["x", *rest], "--agent")
        pos = []
        skip = False
        for x in rest:
            if skip:
                skip = False
                continue
            if x == "--agent":
                skip = True
                continue
            pos.append(x)
        if len(pos) < 2:
            print("usage: spawn WINDOW DIR [BRIEF_FILE] [--agent NAME]", file=sys.stderr)
            return 2
        window, cwd = pos[0], os.path.abspath(os.path.expanduser(pos[1]))
        brief = os.path.abspath(pos[2]) if len(pos) > 2 else None
        a = get(agent, role="agents")
        if not a.installed():
            print(f"{a.label} is not installed (magi-agent install {a.name})", file=sys.stderr)
            return 1
        prompt = f"Read the brief in {brief} and do what it says." if brief else None
        if a.name == "custom":
            largv, _, env = a.launch("agents")
            rc = _tmux_new_window(window, cwd, largv, env)
            if rc == 0 and prompt:   # a custom agent gets the brief typed into its prompt
                subprocess.Popen(["bash", "-c", "sleep 4; tmux send-keys -t \"$1\" -l \"$2\"; sleep 0.5; "
                                  "tmux send-keys -t \"$1\" Enter", "_", f"={SESSION}:={window}", prompt])
        else:
            largv, _, env = a.launch("agents", prompt=prompt)
            rc = _tmux_new_window(window, cwd, largv, env)
        if rc == 0:
            print(f"started {a.label} in ={SESSION}:={window} ({cwd})")
        return rc
    if cmd == "rows":
        for r in agents():
            print("\t".join([r["window"], r["state"], str(r["idle_min"]), r["session"], r["agent"], r["pane"]]))
        return 0
    if cmd == "list":
        print(json.dumps(agents(), indent=1))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
