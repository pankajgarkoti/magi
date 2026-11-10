"""magi_vote: the MAGI vote. Three reviewers vote before an agent does something it can't undo.

Used by `magi-vote` (the Claude Code PreToolUse hook and CLI). It sits next to it in
~/.local/bin, so Python finds it through sys.path[0].

  classify(tool_name, tool_input)  -> Verdict-free Classification (rules first, model for the unclear)
  vote(call, cfg, on_vote)         -> three parallel reviews, 2-of-3 majority
  log_vote(record)                 -> ~/.local/state/magi-vote/votes.jsonl

Only the standard library is imported at module load; the hook's fast path (a harmless
command) costs one Python start and a few regexes.
"""
import os
import re
import shlex

HOME = os.path.expanduser("~")
CONFIG_DIR = os.environ.get("MAGI_VOTE_CONFIG_DIR", HOME + "/.config/magi-vote")
CONFIG = CONFIG_DIR + "/config.toml"
STATE_DIR = os.environ.get("MAGI_VOTE_STATE_DIR", HOME + "/.local/state/magi-vote")
LOG = STATE_DIR + "/votes.jsonl"
CACHE = STATE_DIR + "/classify-cache.json"

CATEGORIES = {
    "push": "git push, force-push, deleting remote branches or tags",
    "history": "rewriting or discarding git history and uncommitted work",
    "delete": "deleting files and data (rm -r, find -delete, shred, mkfs, dd, cloud/k8s/docker deletes)",
    "send": "sending messages or email, POST/PUT/DELETE to remote APIs",
    "publish": "publishing packages, releases, PRs, issues and comments, container pushes",
    "database": "writes to a production database, prod migrations",
    "payment": "paying, charging, refunding, transferring money",
    "packages": "installing or removing system packages",
    "system": "other privileged commands (sudo), power off and reboot",
}

DEFAULTS = {
    "enabled": True,
    "triggers": list(CATEGORIES),
    "model": "claude-haiku-5-5",        # the three units
    "classifier_model": "claude-haiku-5-5",
    "effort": "low",
    "quorum": 2,                        # approvals needed out of 3
    "timeout": 30,                      # seconds per unit; a timeout counts as a deny
    "ambiguous": "model",               # model | vote | pass: what to do with unclear commands
    "on_approve": "defer",              # defer: normal permission flow continues; allow: skip the prompt
    "overlay": True,
    "hold": 2.0,                        # seconds the verdict stays on screen
    "speak": False,                     # hud-say the verdict
    "mcp": True,                        # also vote on outward-facing MCP tools (send, post, delete...)
}

CONFIG_TEMPLATE = """\
# MAGI vote. Before an agent does something it can't undo, three reviewers vote.
# `magi-vote on|off` flips `enabled`; everything else is read on each tool call.

enabled = true

# Which kinds of action call a vote. Remove a name to let it through unvoted.
#   push      git push, force-push, deleting remote branches or tags
#   history   reset --hard, rebase, amend, filter-branch, clean -f, discarding changes
#   delete    rm -r, find -delete, shred, mkfs, dd, docker/kubectl/terraform/cloud deletes
#   send      mail, curl/wget/http POST/PUT/DELETE to a remote host
#   publish   npm/cargo/pypi publish, gh pr/issue/release, docker push
#   database  writes to a production database, prod migrations
#   payment   stripe and other money-moving calls
#   packages  dnf/apt/rpm/flatpak/pip system installs and removals
#   system    other sudo commands, poweroff, reboot
triggers = ["push", "history", "delete", "send", "publish", "database", "payment", "packages", "system"]

model = "claude-haiku-5-5"            # model for MELCHIOR, BALTHASAR and CASPER
classifier_model = "claude-haiku-5-5" # model for commands the rules can't place
effort = "low"
quorum = 2                            # approvals needed out of 3 (3 = unanimous)
timeout = 30                          # seconds per unit; no answer counts as a deny

# Commands the rules can't place (a deploy script, `make release`, plain rm):
#   "model" asks the classifier model once (cached), "vote" always votes, "pass" lets them through
ambiguous = "model"

# After a majority approves: "defer" leaves the normal permission flow in charge (with
# --dangerously-skip-permissions that means it runs), "allow" also skips the prompt.
on_approve = "defer"

overlay = true    # the GTK overlay on Wayland; the verdict also shows in Claude Code
hold = 2.0        # seconds the verdict stays on screen
speak = false     # speak the verdict with hud-say
mcp = true        # also vote on outward-facing MCP tools (send, post, publish, delete, pay)
"""


def load_config(path=CONFIG):
    cfg = dict(DEFAULTS)
    try:
        import tomllib
        with open(path, "rb") as f:
            user = tomllib.load(f)
        for k, v in user.items():
            if k in cfg:
                cfg[k] = v
    except FileNotFoundError:
        pass
    except Exception as e:  # a broken config must not disable the safety net
        cfg["config_error"] = str(e)
    try:
        cfg["quorum"] = max(1, min(3, int(cfg["quorum"])))
    except (TypeError, ValueError):
        cfg["quorum"] = 2
    return cfg


# ------------------------------------------------------------------ classification

class Hit:
    """One rule match: category, a short label, and how sure the rules are."""
    __slots__ = ("category", "label", "sure")

    def __init__(self, category, label, sure=True):
        self.category, self.label, self.sure = category, label, sure

    def __repr__(self):
        return "Hit(%s, %r%s)" % (self.category, self.label, "" if self.sure else ", unsure")


WRAPPERS = {"sudo", "doas", "env", "nohup", "time", "timeout", "nice", "ionice", "stdbuf",
            "command", "exec", "builtin", "xargs", "unbuffer", "chronic", "flock", "watch",
            "run0", "pkexec", "systemd-run", "caffeinate", "strace", "torsocks", "proxychains"}
# options of the wrappers that take a value
WRAP_OPTS = {
    "sudo": {"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U", "--user", "--group"},
    "doas": {"-u", "-C"},
    "env": {"-u", "-C", "-S", "--unset", "--chdir"},
    "timeout": {"-s", "-k", "--signal", "--kill-after"},
    "nice": {"-n", "--adjustment"},
    "ionice": {"-c", "-n", "-p"},
    "xargs": {"-I", "-i", "-n", "-P", "-L", "-d", "-E", "-s", "-a", "--max-args", "--max-procs"},
    "flock": {"-w", "-E", "--timeout"},
    "watch": {"-n", "-d", "--interval"},
    "systemd-run": {"-u", "-p", "--unit", "--property", "-E"},
    "stdbuf": {"-i", "-o", "-e"},
}
SHELLS = {"sh", "bash", "zsh", "dash", "fish", "ksh"}
# commands whose arguments are just text: `echo "git push"` is not a push
INERT = {"echo", "printf", "grep", "egrep", "fgrep", "rg", "ag", "ack", "man", "which", "type",
         "whatis", "apropos", "tldr", "help", "history", "true", ":", "test", "[", "[[", "cd",
         "pushd", "popd", "export", "alias", "unalias", "set", "unset", "read", "sleep", "wait",
         "jq", "yq", "awk", "sed", "less", "more", "head", "tail", "wc", "sort", "uniq", "cut", "tr",
         "cat", "bat", "ls", "stat", "file", "pwd", "date", "whoami", "id", "uname", "notify-send",
         "hud-say", "wl-copy", "tee", "diff", "comm", "column", "fold", "fmt", "nl", "hyprctl"}
LOCAL_HOSTS = re.compile(r"^(https?://)?(localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0|\[::1\]|::1)([:/]|$)", re.I)
PROD = re.compile(r"(^|[^a-z])(prod|production|live|primary|master-db)([^a-z]|$)", re.I)
SQL_WRITE = re.compile(r"\b(insert\s+into|update\s+\w+\s+set|delete\s+from|drop\s+(table|database|schema|index|view)|"
                       r"truncate|alter\s+table|create\s+(table|index)|grant|revoke|flushall|flushdb)\b", re.I)
SQL_DESTROY = re.compile(r"\b(drop\s+(table|database|schema)|truncate\s+(table\s+)?\w|delete\s+from\s+\w+\s*(;|$|where\s+1\s*=\s*1))", re.I)
DB_CLIS = {"psql", "mysql", "mariadb", "sqlite3", "mongosh", "mongo", "redis-cli", "sqlcmd", "clickhouse-client",
           "cockroach", "pgcli", "mycli", "litecli", "duckdb", "supabase", "neonctl", "turso", "planetscale", "pscale"}
MIGRATE = re.compile(r"\b(migrate|migration|db:migrate|db:push|db push|alembic upgrade|flyway|liquibase|prisma\s+db\s+push|"
                     r"prisma\s+migrate\s+deploy|knex\s+migrate|sequelize\s+db:migrate|db:drop|db:reset|db:seed)\b", re.I)
SECRETS_ENV = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
PKG_MANAGERS = {
    "dnf": {"install", "remove", "erase", "upgrade", "update", "downgrade", "reinstall", "swap", "distro-sync",
            "autoremove", "group", "module", "copr", "config-manager", "history"},
    "dnf5": None, "yum": None, "microdnf": None,
    "apt": {"install", "remove", "purge", "upgrade", "full-upgrade", "dist-upgrade", "autoremove", "reinstall"},
    "apt-get": None, "aptitude": None,
    "pacman": {"-S", "-R", "-U", "-Syu", "-Rs", "-Rns", "-Sy", "-Su"},
    "zypper": {"install", "in", "remove", "rm", "update", "up", "dup", "patch"},
    "rpm-ostree": {"install", "uninstall", "upgrade", "rebase", "override", "reset"},
    "flatpak": {"install", "uninstall", "update", "remove"},
    "snap": {"install", "remove", "refresh"},
    "brew": {"install", "uninstall", "remove", "upgrade", "reinstall"},
}
for _k in ("dnf5", "yum", "microdnf"):
    PKG_MANAGERS[_k] = PKG_MANAGERS["dnf"]
for _k in ("apt-get", "aptitude"):
    PKG_MANAGERS[_k] = PKG_MANAGERS["apt"]
SEND_CLIS = {"mail", "mailx", "sendmail", "mutt", "neomutt", "msmtp", "ssmtp", "swaks", "s-nail", "telegram-send",
             "slack", "slack-cli", "signal-cli", "twurl", "toot", "wacli", "ntfy", "apprise", "pushover", "matrix-commander"}
PUBLISH = {
    "npm": {"publish", "unpublish", "deprecate", "dist-tag"}, "pnpm": {"publish"}, "yarn": {"publish", "npm"},
    "bun": {"publish"}, "cargo": {"publish", "yank", "owner"}, "twine": {"upload"}, "gem": {"push", "yank"},
    "poetry": {"publish"}, "uv": {"publish"}, "hatch": {"publish"}, "flit": {"publish"},
    "docker": {"push"}, "podman": {"push"}, "buildah": {"push"}, "skopeo": {"copy", "delete"},
    "vercel": {"--prod", "deploy", "promote", "rollback", "remove", "rm", "env", "domains", "alias"},
    "netlify": {"deploy"}, "fly": {"deploy", "destroy", "secrets"}, "flyctl": {"deploy", "destroy"},
    "heroku": {"releases:rollback", "apps:destroy", "pg:reset", "config:set", "run"},
    "wrangler": {"deploy", "publish", "delete", "secret"}, "firebase": {"deploy"},
    "helm": {"install", "upgrade", "uninstall", "delete", "rollback"},
    "terraform": {"apply", "destroy", "import", "taint", "state"}, "tofu": {"apply", "destroy"},
    "pulumi": {"up", "destroy", "refresh"}, "ansible-playbook": None, "kubectl": None,
    "mvn": {"deploy", "release:perform"}, "gradle": {"publish"}, "dotnet": {"nuget"},
}
GH_WRITE = {
    "pr": {"create", "merge", "close", "reopen", "comment", "review", "edit", "ready"},
    "issue": {"create", "close", "reopen", "comment", "edit", "delete", "transfer", "lock"},
    "release": {"create", "delete", "edit", "upload", "delete-asset"},
    "repo": {"create", "delete", "archive", "rename", "edit", "fork"},
    "gist": {"create", "delete", "edit"}, "secret": {"set", "delete"}, "variable": {"set", "delete"},
    "workflow": {"run", "enable", "disable"}, "run": {"rerun", "cancel", "delete"}, "label": {"create", "delete", "edit"},
    "cache": {"delete"}, "ssh-key": {"add", "delete"}, "gpg-key": {"add", "delete"}, "discussion": {"create", "comment"},
}
RO_SUDO = {"-v", "-k", "-l", "-n", "true", "cat", "ls", "journalctl", "dmesg", "lsblk", "fdisk -l", "id", "whoami",
           "systemctl status", "less", "head", "tail", "stat", "find", "du", "df", "lsof", "ss", "netstat", "ip",
           "iotop", "htop", "top", "smartctl", "nvme", "dmidecode", "lspci", "lsusb", "grep", "rg", "file", "test"}
UNCLEAR_RUNNERS = re.compile(r"(^|/)(deploy|release|publish|ship|rollout|migrate|teardown|destroy|nuke|wipe|cleanup|"
                             r"reset|purge|send|notify|post)[\w.-]*(\.sh|\.py|\.js|\.ts|\.rb)?$", re.I)
MAKE_TARGETS = re.compile(r"^(deploy|release|publish|ship|push|upload|prod|production|destroy|clean-all|distclean|"
                          r"nuke|purge|migrate|rollout)([-:_]\w+)?$", re.I)
SCRIPT_BODY = re.compile(r"(requests\.(post|put|delete|patch)|httpx\.(post|put|delete)|smtplib|sendmail|"
                         r"shutil\.rmtree|os\.(remove|unlink|rmdir)|\.unlink\(|rmSync|unlinkSync|fetch\([^)]*method|"
                         r"DROP\s+TABLE|DELETE\s+FROM|subprocess.*(rm|push))", re.I)


def split_segments(cmd):
    """Split a shell command into simple-command token lists (on ; && || | & and newlines).

    Returns (segments, ok). ok is False when shlex can't parse it (heredocs, unbalanced
    quotes); the caller then falls back to scanning the raw text."""
    subs = []
    # command substitutions and backticks are commands too
    for m in re.finditer(r"\$\(([^()]*(?:\([^()]*\)[^()]*)*)\)|`([^`]*)`", cmd):
        subs.append(m.group(1) or m.group(2) or "")
    body = cmd
    # heredoc bodies are data, not commands; keep only the line that starts them
    hd = re.search(r"<<-?\s*['\"]?(\w+)['\"]?", body)
    if hd:
        tag = hd.group(1)
        end = re.search(r"^\s*%s\s*$" % re.escape(tag), body[hd.end():], re.M)
        heredoc = body[hd.end():hd.end() + end.start()] if end else body[hd.end():]
        rest = body[hd.end() + end.end():] if end else ""
        first_line_end = body.find("\n", hd.end())
        head = body[:first_line_end] if first_line_end != -1 else body
        body = head + "\n" + rest
        subs.append("__HEREDOC__ " + heredoc)
    try:
        lex = shlex.shlex(body, posix=True, punctuation_chars=";&|<>()")
        lex.whitespace_split = True
        lex.commenters = "#"
        lex.whitespace = " \t\r"
        toks = list(lex)
    except ValueError:
        return [], False
    segs, cur = [], []
    for t in toks:
        if t in (";", "&&", "||", "|", "&", "\n", "|&", ";;", "(", ")") or (t and set(t) <= set(";&|\n")):
            if cur:
                segs.append(cur)
            cur = []
        elif "\n" in t and not t.strip():
            if cur:
                segs.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        segs.append(cur)
    # newlines arrive inside tokens only when quoted; split unquoted ones apart
    out = []
    for s in segs:
        out.append([t for t in s if t != ""])
    for s in subs:
        if s.startswith("__HEREDOC__ "):
            out.append(["__heredoc__", s[len("__HEREDOC__ "):]])
            continue
        inner, ok = split_segments(s)
        if ok:
            out.extend(inner)
    return [s for s in out if s], True


def _strip_redirects(argv):
    out, skip = [], False
    for t in argv:
        if skip:
            skip = False
            continue
        if t in (">", ">>", "<", "2>", "&>", ">&", "2>&1", "<<<", ">|"):
            skip = t not in ("2>&1",)
            continue
        if re.match(r"^\d?[<>]{1,2}&?\S+$", t) or t in ("<", ">"):
            continue
        out.append(t)
    return out


def unwrap(argv):
    """Drop env assignments and wrappers (sudo, env, timeout...). Returns (argv, privileged)."""
    priv = False
    argv = _strip_redirects(argv)
    i = 0
    while i < len(argv):
        t = argv[i]
        if SECRETS_ENV.match(t):
            i += 1
            continue
        base = os.path.basename(t)
        if base in WRAPPERS:
            if base in ("sudo", "doas", "run0", "pkexec"):
                priv = True
            i += 1
            opts = WRAP_OPTS.get(base, set())
            if base == "timeout":  # timeout [opts] DURATION cmd
                while i < len(argv) and argv[i].startswith("-"):
                    i += 2 if argv[i] in opts else 1
                i += 1
                continue
            if base in ("nice", "ionice", "watch", "flock") and i < len(argv) and argv[i].startswith("-"):
                pass
            while i < len(argv) and argv[i].startswith("-") and argv[i] != "--":
                if argv[i] in opts:
                    i += 1
                i += 1
            if i < len(argv) and argv[i] == "--":
                i += 1
            if base == "flock" and i < len(argv) and not argv[i].startswith("-"):
                i += 1  # the lock file
            continue
        break
    return argv[i:], priv


def _flags(args):
    """Set of short and long flags in args: -rf -> {-r, -f, -rf}."""
    out = set()
    for a in args:
        if a == "--":
            break
        if a.startswith("--"):
            out.add(a.split("=", 1)[0])
        elif a.startswith("-") and len(a) > 1:
            out.add(a)
            for ch in a[1:]:
                out.add("-" + ch)
    return out


def _git(args, hits):
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in ("-C", "-c", "--git-dir", "--work-tree", "--namespace") else 1
    if i >= len(args):
        return
    sub, rest = args[i], args[i + 1:]
    f = _flags(rest)
    pos = [a for a in rest if not a.startswith("-")]
    if sub == "push":
        if "--dry-run" in f or "-n" in f:
            return
        force = f & {"--force", "-f", "--force-with-lease", "--force-if-includes", "--mirror"} or any(p.startswith("+") for p in pos)
        delete = "--delete" in f or "-d" in f or any(p.startswith(":") and len(p) > 1 for p in pos)
        label = "git push" + (" --force" if force else "") + (" --delete" if delete else "")
        if "--tags" in f or "--all" in f or "--mirror" in f:
            label += " (all refs)" if "--tags" not in f else " --tags"
        hits.append(Hit("push", label))
    elif sub == "reset" and ("--hard" in f or "--merge" in f or "--keep" in f):
        hits.append(Hit("history", "git reset --hard"))
    elif sub == "clean" and (f & {"-f", "--force"}) and not (f & {"-n", "--dry-run"}):
        hits.append(Hit("history", "git clean -f (deletes untracked files)"))
    elif sub in ("checkout", "restore") and ("--" in rest or "." in pos or "-f" in f or "--force" in f) and \
            not (sub == "restore" and "--staged" in f and "--worktree" not in f and "-W" not in f):
        if sub == "checkout" and "--" not in rest and "." not in pos and "-f" not in f and "--force" not in f:
            return
        hits.append(Hit("history", "git %s (discards uncommitted changes)" % sub))
    elif sub == "branch" and ("-D" in f or ("--delete" in f and ("--force" in f or "-f" in f))):
        hits.append(Hit("history", "git branch -D"))
    elif sub == "stash" and pos and pos[0] in ("drop", "clear"):
        hits.append(Hit("history", "git stash " + pos[0]))
    elif sub == "rebase" and not (f & {"--abort", "--continue", "--skip", "--quit", "--edit-todo", "--show-current-patch"}):
        hits.append(Hit("history", "git rebase"))
    elif sub == "commit" and "--amend" in f:
        hits.append(Hit("history", "git commit --amend"))
    elif sub in ("filter-branch", "filter-repo"):
        hits.append(Hit("history", "git " + sub))
    elif sub == "reflog" and pos and pos[0] in ("expire", "delete"):
        hits.append(Hit("history", "git reflog " + pos[0]))
    elif sub == "gc" and any(a.startswith("--prune") for a in rest):
        hits.append(Hit("history", "git gc --prune"))
    elif sub == "update-ref" and "-d" in f:
        hits.append(Hit("history", "git update-ref -d"))
    elif sub == "send-email":
        hits.append(Hit("send", "git send-email"))
    elif sub == "lfs" and pos[:1] == ["push"]:
        hits.append(Hit("push", "git lfs push"))
    elif sub == "worktree" and pos[:1] == ["remove"] and (f & {"-f", "--force"}):
        hits.append(Hit("history", "git worktree remove --force"))


def _curl(prog, args, hits):
    f = _flags(args)
    urls = [a for a in args if re.match(r"^(https?://|[\w.-]+\.[a-z]{2,}(/|$)|localhost|127\.)", a, re.I)]
    local = urls and all(LOCAL_HOSTS.match(u) for u in urls)
    method = None
    for j, a in enumerate(args):
        if a in ("-X", "--request") and j + 1 < len(args):
            method = args[j + 1].upper()
        elif a.startswith("-X") and len(a) > 2:
            method = a[2:].upper()
        elif a.startswith("--request="):
            method = a.split("=", 1)[1].upper()
        elif prog in ("http", "https", "xh", "httpie") and a.upper() in ("POST", "PUT", "PATCH", "DELETE") and method is None:
            method = a.upper()
    if prog in ("http", "https", "xh", "httpie") and method is None:
        if any(re.match(r"^[\w.-]+(=|:=|@)", a) for a in args if not a.startswith("-")):
            method = "POST"
    data = f & {"-d", "--data", "--data-raw", "--data-binary", "--data-urlencode", "--json", "-F", "--form",
                "-T", "--upload-file", "--post-data", "--post-file", "--body-data", "--body-file", "--method"}
    if method in ("GET", "HEAD", "OPTIONS"):
        return
    if (method or data) and not local:
        target = urls[0] if urls else "a remote host"
        m = method or "POST"
        if re.search(r"stripe\.com|paypal|braintree|adyen|razorpay|wise\.com|plaid|squareup|/payments?|/payouts?|/transfers?|/charges?|/refunds?",
                     " ".join(urls), re.I):
            hits.append(Hit("payment", "%s %s" % (m, target)))
        elif m == "DELETE":
            hits.append(Hit("delete", "HTTP DELETE %s" % target))
        else:
            hits.append(Hit("send", "%s %s" % (m, target)))


def _rm(args, hits):
    f = _flags(args)
    targets = [a for a in args if not a.startswith("-") or a == "-"]
    rec = f & {"-r", "-R", "--recursive"}
    risky = [t for t in targets if t in ("/", "~", "~/", "$HOME", "*", ".", "..", "/*", "~/*") or t.startswith(("/etc", "/usr", "/boot", "/var", "/home", "/opt"))
             or t.endswith("/*")]
    scratch = targets and all(re.match(r"^(/tmp/|/var/tmp/|\$TMPDIR|\$\{?TMP|/run/user/\d+/|(.*/)?(node_modules|__pycache__|\.pytest_cache|\.cache|dist|build|target|\.next|\.venv|venv|coverage|\.mypy_cache|\.ruff_cache)/?$|[\w.-]*\.(o|pyc|log|tmp)$)", t) for t in targets)
    if scratch and not risky and not any(".." in t for t in targets):
        return
    if rec or risky or "*" in " ".join(targets):
        hits.append(Hit("delete", "rm " + " ".join(sorted(x for x in f if x.startswith("-") and len(x) == 2)) + " " + " ".join(targets[:3])))
    elif targets:
        hits.append(Hit("delete", "rm " + " ".join(targets[:3]), sure=False))


def classify_argv(argv, hits, depth=0):
    if depth > 4 or not argv:
        return
    if argv[0] == "__heredoc__":
        body = argv[1]
        if SQL_DESTROY.search(body) or SQL_WRITE.search(body) and PROD.search(body):
            hits.append(Hit("database", "SQL write in a heredoc", sure=bool(SQL_DESTROY.search(body))))
        return
    argv, priv = unwrap(argv)
    if not argv:
        return
    prog = os.path.basename(argv[0])
    args = argv[1:]
    f = _flags(args)
    if prog in INERT and not priv:
        return
    if prog in SHELLS:
        for j, a in enumerate(args):
            if a == "-c" or (a.startswith("-") and "c" in a[1:] and not a.startswith("--")):
                if j + 1 < len(args):
                    segs, ok = split_segments(args[j + 1])
                    for s in segs:
                        classify_argv(s, hits, depth + 1)
                    if not ok:
                        scan_raw(args[j + 1], hits)
                return
        script = next((a for a in args if not a.startswith("-")), "")
        if script and UNCLEAR_RUNNERS.search(script):
            hits.append(Hit("publish", "runs %s" % script, sure=False))
        return
    if prog in ("ssh", "mosh"):
        # ssh [opts] host [command...]
        rest, i = [], 0
        with_val = {"-p", "-i", "-l", "-o", "-F", "-J", "-L", "-R", "-D", "-W", "-b", "-c", "-E", "-m", "-O", "-Q", "-S", "-w", "-e", "-B", "-I"}
        while i < len(args) and args[i].startswith("-"):
            i += 2 if args[i] in with_val else 1
        rest = args[i + 1:]
        if rest:
            segs, _ = split_segments(" ".join(rest) if len(rest) > 1 else rest[0])
            for s in segs:
                classify_argv(s, hits, depth + 1)
        return
    if prog in ("eval",):
        segs, _ = split_segments(" ".join(args))
        for s in segs:
            classify_argv(s, hits, depth + 1)
        return
    if prog == "git":
        _git(args, hits)
    elif prog in ("rm", "unlink", "srm", "trash-empty"):
        if prog == "rm":
            _rm(args, hits)
        else:
            hits.append(Hit("delete", prog + " " + " ".join(args[:2])))
    elif prog in ("shred", "wipe", "wipefs", "mkfs", "blkdiscard", "sgdisk", "parted", "fdisk", "sfdisk", "cryptsetup") or prog.startswith("mkfs."):
        if prog in ("fdisk", "parted", "sfdisk") and (f & {"-l", "--list"}) or prog == "cryptsetup" and args[:1] in (["status"], ["luksDump"], ["open"]):
            return
        hits.append(Hit("delete", "%s %s" % (prog, " ".join(args[:2]))))
    elif prog == "dd" and any(a.startswith("of=/dev/") for a in args):
        hits.append(Hit("delete", "dd to a block device"))
    elif prog == "find" and ("-delete" in args or any(a in ("-exec", "-execdir", "-ok") and j + 1 < len(args) and os.path.basename(args[j + 1]) in ("rm", "shred", "unlink")
                                                          for j, a in enumerate(args))):
        hits.append(Hit("delete", "find ... -delete"))
    elif prog == "truncate" and ("-s" in f or "--size" in f) and any(re.match(r"^(0|--size=0)$", a) for a in args):
        hits.append(Hit("delete", "truncate to zero", sure=False))
    elif prog == "rsync" and (f & {"--delete", "--delete-before", "--delete-after", "--delete-during", "--remove-source-files"}):
        hits.append(Hit("delete", "rsync --delete"))
    elif prog in ("mv",) and any(a in ("/dev/null",) for a in args):
        hits.append(Hit("delete", "mv to /dev/null"))
    elif prog == "chmod" and ("-R" in f) and any(a in ("/", "~", "/home", "/etc", "/usr") for a in args):
        hits.append(Hit("system", "chmod -R on a system tree"))
    elif prog in ("curl", "wget", "http", "https", "xh", "httpie", "curlie"):
        _curl(prog, args, hits)
    elif prog in SEND_CLIS:
        hits.append(Hit("send", "%s %s" % (prog, " ".join(args[:2]))))
    elif prog == "gh":
        pos = [a for a in args if not a.startswith("-")]
        if len(pos) >= 2 and pos[1] in GH_WRITE.get(pos[0], ()):
            cat = "delete" if pos[1] in ("delete", "delete-asset") else "publish"
            hits.append(Hit(cat, "gh %s %s" % (pos[0], pos[1])))
        elif pos[:1] == ["api"]:
            meth = None
            for j, a in enumerate(args):
                if a in ("-X", "--method") and j + 1 < len(args):
                    meth = args[j + 1].upper()
            if meth in ("POST", "PUT", "PATCH", "DELETE") or (meth is None and f & {"-f", "-F", "--field", "--raw-field", "--input"}):
                hits.append(Hit("delete" if meth == "DELETE" else "publish", "gh api %s" % (meth or "POST")))
    elif prog in ("glab", "tea", "hub") and len(args) >= 2 and args[1] in ("create", "merge", "close", "delete", "note", "comment"):
        hits.append(Hit("publish", "%s %s %s" % (prog, args[0], args[1])))
    elif prog in ("npm", "pnpm", "yarn", "bun") and args[:1] == ["run"] and len(args) > 1 and MAKE_TARGETS.match(args[1]):
        hits.append(Hit("publish", "%s run %s" % (prog, args[1]), sure=False))
    elif prog in ("npm", "pnpm", "yarn") and priv:
        hits.append(Hit("packages", "sudo %s %s" % (prog, " ".join(args[:2]))))
    elif prog in PUBLISH:
        subs = PUBLISH[prog]
        if prog == "kubectl":
            if args and args[0] in ("delete", "apply", "replace", "patch", "scale", "drain", "cordon", "rollout", "edit", "set", "create", "exec"):
                if args[0] == "delete":
                    hits.append(Hit("delete", "kubectl delete"))
                elif args[0] == "exec":
                    hits.append(Hit("system", "kubectl exec", sure=False))
                else:
                    hits.append(Hit("publish", "kubectl " + args[0], sure=PROD.search(" ".join(args)) is not None))
        elif prog == "ansible-playbook":
            hits.append(Hit("publish", "ansible-playbook", sure=False))
        elif subs and (set(args) & subs):
            sub = next(a for a in args if a in subs)
            destroy = sub in ("destroy", "delete", "remove", "rm", "uninstall", "unpublish", "yank", "apps:destroy", "pg:reset")
            if prog in ("docker", "podman") and sub == "push":
                hits.append(Hit("publish", "%s push" % prog))
            elif prog == "terraform" and sub == "state" and not (set(args) & {"rm", "mv", "push", "replace-provider"}):
                return
            elif prog in ("yarn",) and sub == "npm" and "publish" not in args:
                return
            else:
                hits.append(Hit("delete" if destroy else "publish", "%s %s" % (prog, sub)))
        elif prog in ("docker", "podman"):
            pass
        if prog in ("docker", "podman"):
            joined = " ".join(args)
            if re.search(r"\b(rm|rmi|prune|kill)\b", joined) and re.search(r"\b(volume\s+(rm|prune)|system\s+prune|rmi|rm\s+-f|image\s+prune|network\s+prune|container\s+prune)\b", joined):
                hits.append(Hit("delete", "%s %s" % (prog, " ".join(args[:3]))))
            elif re.search(r"\bvolume\s+rm\b|\bsystem\s+prune\b", joined):
                hits.append(Hit("delete", "%s %s" % (prog, " ".join(args[:3]))))
    elif prog in ("aws", "gcloud", "gsutil", "az", "doctl", "s3cmd", "rclone", "b2", "mc", "oci", "linode-cli", "hcloud"):
        joined = " ".join(args)
        if re.search(r"\b(rm|rb|delete|destroy|purge|terminate|remove|del|deletefile|purge-queue|delete-\w+|terminate-\w+)\b", joined):
            hits.append(Hit("delete", "%s %s" % (prog, " ".join(args[:3]))))
        elif re.search(r"\b(ses\s+send|sns\s+publish|sqs\s+send|pubsub\s+topics\s+publish|deploy|apply|create|put-\w+|update-\w+|sync|cp|mv|copy|move)\b", joined):
            pos = [a for a in args if not a.startswith("-")]
            remote_write = not re.search(r"\b(cp|sync|copy|mv|move)\b", joined) or (len(pos) > 1 and re.match(r"^(s3|gs|az|b2|r2|\w+):", pos[-1]))
            if remote_write:
                cat = "send" if re.search(r"\b(ses|sns|sqs|pubsub)\b", joined) else "publish"
                hits.append(Hit(cat, "%s %s" % (prog, " ".join(args[:3])), sure=cat == "send" or bool(PROD.search(joined))))
    elif prog in DB_CLIS:
        joined = " ".join(args)
        if SQL_DESTROY.search(joined) and not re.search(r"\b(localhost|127\.0\.0\.1|test|dev|local)\b", joined):
            hits.append(Hit("database", "%s: destructive SQL" % prog))
        elif SQL_WRITE.search(joined) and PROD.search(joined):
            hits.append(Hit("database", "%s: write to production" % prog))
        elif PROD.search(joined):
            hits.append(Hit("database", "%s on a production host" % prog, sure=False))
        elif SQL_DESTROY.search(joined):
            hits.append(Hit("database", "%s: destructive SQL" % prog, sure=False))
    elif prog in ("stripe",):
        if args and not (set(args) & {"listen", "logs", "login", "status", "version", "help", "samples", "open", "config"}) and \
                re.search(r"\b(create|confirm|capture|pay|refund|transfer|payout|charge|cancel|delete|update)\b", " ".join(args)):
            hits.append(Hit("payment", "stripe " + " ".join(args[:2])))
    elif prog in PKG_MANAGERS and args:
        verbs = PKG_MANAGERS[prog]
        if prog == "pacman":
            if args[0][:2] in ("-S", "-R", "-U") and not re.match(r"^-S[sigl]", args[0]) and args[0] not in ("-Ss", "-Si", "-Sl", "-Sg"):
                hits.append(Hit("packages", "pacman " + args[0]))
        else:
            pos = [a for a in args if not a.startswith("-")]
            if pos and pos[0] in verbs:
                if prog in ("dnf", "dnf5", "yum") and pos[0] == "history" and (len(pos) < 2 or pos[1] not in ("undo", "rollback", "redo")):
                    return
                if prog in ("dnf", "dnf5") and pos[0] in ("group", "module", "copr") and (len(pos) < 2 or pos[1] in ("list", "info", "search")):
                    return
                hits.append(Hit("packages", "%s %s" % (prog, " ".join(pos[:3]))))
    elif prog in ("rpm",) and (f & {"-i", "-U", "-e", "-F", "--install", "--upgrade", "--erase", "--freshen"}) and not (f & {"-q", "--query"}):
        hits.append(Hit("packages", "rpm " + " ".join(args[:3])))
    elif prog in ("pip", "pip3", "uv") and priv and args and args[0] in ("install", "uninstall"):
        hits.append(Hit("packages", "sudo %s %s" % (prog, args[0])))
    elif prog in ("systemctl", "loginctl", "shutdown", "reboot", "poweroff", "halt", "init", "telinit"):
        if prog in ("shutdown", "reboot", "poweroff", "halt") or args[:1] in (["poweroff"], ["reboot"], ["halt"], ["kexec"], ["soft-reboot"],
                                                                               ["terminate-session"], ["kill-session"], ["terminate-user"]) \
                or (prog in ("init", "telinit") and args[:1] in (["0"], ["6"])):
            hits.append(Hit("system", "%s %s" % (prog, " ".join(args[:1]))))
        elif priv and args and args[0] in ("disable", "mask", "stop", "kill", "isolate", "set-default", "enable"):
            hits.append(Hit("system", "sudo systemctl " + " ".join(args[:2])))
    elif prog in ("make", "just", "task", "mage", "rake", "invoke", "nox", "tox") and any(MAKE_TARGETS.match(a) for a in args if not a.startswith("-")):
        t = next(a for a in args if not a.startswith("-") and MAKE_TARGETS.match(a))
        hits.append(Hit("publish", "%s %s" % (prog, t), sure=False))
    elif prog in ("python", "python3", "node", "deno", "bun", "ruby", "perl", "php") and (f & {"-c", "-e", "--eval"}):
        body = " ".join(args)
        if SCRIPT_BODY.search(body):
            hits.append(Hit("delete" if re.search(r"rmtree|remove|unlink|rmSync|DROP|DELETE", body, re.I) else "send",
                            "%s inline script" % prog, sure=False))
    elif UNCLEAR_RUNNERS.search(prog) and argv[0].startswith(("./", "/", "~")) or UNCLEAR_RUNNERS.search(argv[0]) and "/" in argv[0]:
        hits.append(Hit("publish", "runs %s" % argv[0], sure=False))
    elif prog in ("wl-paste", "xdotool", "ydotool", "wtype"):
        pass
    elif prog in ("crontab",) and "-r" in f:
        hits.append(Hit("delete", "crontab -r"))
    elif prog in ("userdel", "groupdel", "deluser", "passwd", "usermod", "chown", "chattr", "setenforce", "firewall-cmd", "iptables", "nft", "ufw"):
        if priv or prog in ("userdel", "groupdel", "deluser"):
            hits.append(Hit("system", "%s %s" % (prog, " ".join(args[:2]))))
    if priv and not hits and prog not in RO_SUDO and " ".join([prog] + args[:1]) not in RO_SUDO:
        hits.append(Hit("system", "sudo " + " ".join([prog] + args[:2])))


RAW_PATTERNS = [
    (re.compile(r"\bgit\s+(-C\s+\S+\s+)?push\b"), "push", "git push"),
    (re.compile(r"\brm\s+-[a-zA-Z]*[rR]"), "delete", "rm -r"),
    (re.compile(r"\bgit\s+reset\s+--hard\b"), "history", "git reset --hard"),
    (re.compile(r"\b(drop\s+(table|database)|truncate\s+table)\b", re.I), "database", "destructive SQL"),
    (re.compile(r"\b(dnf|apt|apt-get|yum|zypper|pacman)\s+(install|remove|erase|-S|-R)\b"), "packages", "package install"),
    (re.compile(r"\b(npm|cargo|twine|gem)\s+(publish|upload|push)\b"), "publish", "package publish"),
    (re.compile(r"\bcurl\b.*\s(-X\s*(POST|PUT|DELETE|PATCH)|-d|--data)\b"), "send", "HTTP write"),
    (re.compile(r"\b(sendmail|mailx?|msmtp)\b"), "send", "mail"),
]


def scan_raw(text, hits):
    for rx, cat, label in RAW_PATTERNS:
        if rx.search(text):
            hits.append(Hit(cat, label, sure=False))


def classify_command(cmd):
    """Rule hits for a shell command string."""
    hits = []
    segs, ok = split_segments(cmd)
    for s in segs:
        classify_argv(s, hits)
    if not ok:
        scan_raw(cmd, hits)
    return hits


MCP_OUTWARD = re.compile(r"(send|post|publish|tweet|toot|reply|comment|email|mail|message|dm|notify|invite|share|"
                         r"create_(issue|pr|pull_request|release|post|page|event|draft|comment)|merge|"
                         r"delete|remove|destroy|drop|purge|archive|trash|"
                         r"pay|charge|refund|transfer|payout|invoice|purchase|order|book|"
                         r"deploy|promote|rollback|execute_sql|apply_migration|push)", re.I)
MCP_READ = re.compile(r"(^|_)(get|list|read|search|query|find|fetch|describe|view|show|lookup|status|whoami|"
                      r"check|count|preview|navigate|screenshot|snapshot|inspect|analy[sz]e|context|"
                      r"tabs_context|take_|wait|hover|resize)", re.I)


def classify_tool(tool_name, tool_input, cfg=None):
    """(hits, text) for one tool call. text is what the units are shown."""
    cfg = cfg or DEFAULTS
    tool_input = tool_input or {}
    if tool_name == "Bash" or tool_name == "PowerShell":
        cmd = tool_input.get("command", "")
        return classify_command(cmd), cmd
    if tool_name.startswith("mcp__") and cfg.get("mcp", True):
        short = tool_name.split("__")[-1]
        text = "%s %s" % (tool_name, _brief_json(tool_input))
        if short in ("execute_sql", "query", "run_query", "sql") or "sql" in short.lower():
            sql = str(tool_input.get("query") or tool_input.get("sql") or "")
            if SQL_DESTROY.search(sql) or SQL_WRITE.search(sql):
                return [Hit("database", "%s: %s" % (short, sql[:60]), sure=bool(SQL_DESTROY.search(sql)))], text
            return [], text
        # browser automation: clicks and form input are judged elsewhere; only explicit sends count
        if re.search(r"claude-in-chrome|chrome-devtools|playwright|puppeteer|Claude_Browser|computer-use", tool_name):
            return [], text
        if MCP_READ.search(short) and not MCP_OUTWARD.search(short.split("_", 1)[-1] if MCP_READ.match(short) else short):
            return [], text
        m = MCP_OUTWARD.search(short)
        if m:
            w = m.group(1).lower()
            if re.match(r"delete|remove|destroy|drop|purge|trash|archive", w):
                cat = "delete"
            elif re.match(r"pay|charge|refund|transfer|payout|invoice|purchase|order|book", w):
                cat = "payment"
            elif re.match(r"execute_sql|apply_migration", w):
                cat = "database"
            elif re.match(r"deploy|promote|rollback|push|merge|create_|publish|tweet|toot|post|comment|share", w):
                cat = "publish"
            else:
                cat = "send"
            return [Hit(cat, "%s (MCP)" % short)], text
        return [], text
    return [], ""


def _brief_json(obj, limit=600):
    import json
    try:
        s = json.dumps(obj, ensure_ascii=False)
    except (TypeError, ValueError):
        s = str(obj)
    return s if len(s) <= limit else s[:limit] + "…"


def triage(hits, cfg):
    """Decide from rule hits: ('pass'|'vote'|'unsure', hits that count)."""
    enabled = set(cfg.get("triggers", CATEGORIES))
    counted = [h for h in hits if h.category in enabled]
    if not counted:
        return "pass", []
    if any(h.sure for h in counted):
        return "vote", [h for h in counted if h.sure]
    mode = cfg.get("ambiguous", "model")
    if mode == "vote":
        return "vote", counted
    if mode == "pass":
        return "pass", []
    return "unsure", counted


# ------------------------------------------------------------------ the three units

UNITS = [
    ("MELCHIOR-1", "scientist",
     "You are MELCHIOR-1, the scientist. Judge correctness and technical risk only: is this command "
     "technically right for its apparent purpose, does it target the right repo/branch/host/path, could it "
     "break something (force-push over others' work, wrong remote, wiping the wrong directory, a migration "
     "on the wrong database)? Approve when the command is correct and its blast radius is what it looks like."),
    ("BALTHASAR-2", "mother",
     "You are BALTHASAR-2, the mother. Protect the user and other people: data loss with no backup, leaked "
     "secrets or private data, money spent, messages or posts other people will see, damage to reputation. "
     "Approve when nobody is hurt if this goes ahead and anything lost can be recovered or was meant to go."),
    ("CASPER-3", "woman",
     "You are CASPER-3, the woman: you judge intent. Is this what the user actually asked for, in their own "
     "words in the recent conversation? Approve when the user asked for this action or it clearly follows "
     "from what they asked. Deny when the agent is going further than asked (pushing when asked only to "
     "commit, deleting more than asked, sending what the user didn't approve) or when there's no request for it."),
]
COMMON = (
    " You are one of three independent reviewers. An AI coding agent wants to run the action below, "
    "which may be hard to undo. You cannot run commands or read files; judge only from what you are given. "
    "Answer with a vote, approve or deny, and one short reason (at most 12 words, no preamble). "
    "If a deny leads to the user being asked, that is fine; don't deny out of mere caution when the action is "
    "plainly correct and requested."
)
VOTE_SCHEMA = '{"type":"object","properties":{"vote":{"type":"string","enum":["approve","deny"]},"reason":{"type":"string"}},"required":["vote","reason"],"additionalProperties":false}'
CLASSIFY_SCHEMA = ('{"type":"object","properties":{"irreversible":{"type":"boolean"},"category":{"type":"string","enum":'
                   '["push","history","delete","send","publish","database","payment","packages","system","none"]},'
                   '"reason":{"type":"string"}},"required":["irreversible","category","reason"],"additionalProperties":false}')
CLASSIFY_PROMPT = (
    "Classify one action an AI coding agent wants to take. irreversible=true only if it is hard or impossible "
    "to undo, or reaches outside this machine: pushing or rewriting shared git history, deleting data that "
    "isn't scratch/build output, sending messages or email, posting or publishing, production database writes, "
    "moving money, installing or removing system packages, privileged system changes. Local, recoverable "
    "edits, builds, tests and reads are not. Give one short reason."
)


def _claude_argv(cfg, system, schema, model=None):
    return ["claude", "-p", "--model", model or cfg["model"], "--effort", str(cfg.get("effort", "low")),
            "--tools", "", "--setting-sources", "", "--settings", '{"disableAllHooks":true}',
            "--strict-mcp-config", "--no-chrome", "--no-session-persistence", "--disable-slash-commands",
            "--system-prompt", system, "--output-format", "json", "--json-schema", schema]


def run_claude(argv, prompt, timeout):
    """Run one `claude -p`; returns (structured dict or None, cost, error string or None)."""
    import json
    import subprocess
    env = dict(os.environ, MAGI_VOTE_INSIDE="1")
    env.pop("CLAUDECODE", None)
    cwd = STATE_DIR if os.path.isdir(STATE_DIR) else "/tmp"
    try:
        p = subprocess.run(argv, input=prompt, capture_output=True, text=True, timeout=timeout, env=env, cwd=cwd)
    except subprocess.TimeoutExpired:
        return None, 0.0, "NO RESPONSE (%ds)" % timeout
    except OSError as e:
        return None, 0.0, "ERROR: %s" % e
    try:
        d = json.loads(p.stdout)
    except ValueError:
        return None, 0.0, "ERROR: %s" % ((p.stderr or p.stdout).strip().splitlines() or ["no output"])[-1][:80]
    cost = float(d.get("total_cost_usd") or 0)
    so = d.get("structured_output")
    if not isinstance(so, dict):
        try:
            so = json.loads(d.get("result") or "")
        except ValueError:
            so = None
    if d.get("is_error") or not isinstance(so, dict):
        return None, cost, "ERROR: %s" % str(d.get("result") or d.get("subtype") or "no verdict")[:80]
    return so, cost, None


def model_classify(text, cfg):
    """Ask the classifier model about a command the rules couldn't place. Cached by text."""
    import hashlib
    import json
    key = hashlib.sha256((cfg["classifier_model"] + "\0" + text).encode()).hexdigest()[:24]
    cache = {}
    try:
        with open(CACHE) as f:
            cache = json.load(f)
    except (OSError, ValueError):
        pass
    if key in cache:
        c = cache[key]
        return c["irreversible"], c.get("category", "none"), c.get("reason", ""), 0.0, True
    so, cost, err = run_claude(_claude_argv(cfg, CLASSIFY_PROMPT, CLASSIFY_SCHEMA, cfg["classifier_model"]),
                               "Action:\n" + text[:4000], min(int(cfg.get("timeout", 30)), 20))
    if err:  # can't tell: treat as irreversible so it gets voted on
        return True, "none", "classifier failed: " + err, cost, False
    res = {"irreversible": bool(so.get("irreversible")), "category": so.get("category", "none"), "reason": so.get("reason", "")}
    cache[key] = res
    if len(cache) > 2000:
        cache = dict(list(cache.items())[-1000:])
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(CACHE + ".tmp", "w") as f:
            json.dump(cache, f)
        os.replace(CACHE + ".tmp", CACHE)
    except OSError:
        pass
    return res["irreversible"], res["category"], res["reason"], cost, False


def git_context(cwd):
    """A few lines about the repo at cwd: branch, upstream, unpushed commits, dirty files."""
    import subprocess

    def g(*a):
        try:
            p = subprocess.run(["git", "-C", cwd] + list(a), capture_output=True, text=True, timeout=2)
            return p.stdout.strip() if p.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            return ""
    if not g("rev-parse", "--is-inside-work-tree"):
        return ""
    lines = ["branch: %s" % (g("branch", "--show-current") or "(detached)")]
    up = g("rev-parse", "--abbrev-ref", "@{u}")
    if up:
        lines.append("upstream: %s" % up)
        ahead = g("log", "--oneline", "-8", "@{u}..HEAD")
        if ahead:
            lines.append("commits not on upstream:\n" + ahead)
        behind = g("rev-list", "--count", "HEAD..@{u}")
        if behind and behind != "0":
            lines.append("upstream has %s commit(s) not in HEAD" % behind)
    else:
        last = g("log", "--oneline", "-3")
        if last:
            lines.append("recent commits:\n" + last)
    remotes = g("remote", "-v")
    if remotes:
        lines.append("remotes: " + "; ".join(sorted({" ".join(r.split()[:2]) for r in remotes.splitlines()})))
    st = g("status", "--short")
    if st:
        s = st.splitlines()
        lines.append("uncommitted: %d file(s)%s" % (len(s), ("\n" + "\n".join(s[:8])) if s else ""))
    return "\n".join(lines)


def recent_conversation(transcript_path, max_chars=3500):
    """The user's recent messages and the agent's last words, from the session transcript."""
    import json
    if not transcript_path or not os.path.exists(transcript_path):
        return ""
    try:
        with open(transcript_path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 400_000))
            raw = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return ""
    turns = []
    for line in raw:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        t = d.get("type")
        msg = d.get("message") or {}
        if t not in ("user", "assistant") or d.get("isMeta") or d.get("isSidechain"):
            continue
        content = msg.get("content")
        text = ""
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = " ".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
        text = text.strip()
        if not text or text.startswith(("<command-", "<local-command", "<system-reminder", "Caveat:")):
            continue
        text = re.sub(r"<system-reminder>.*?</system-reminder>", "", text, flags=re.S).strip()
        if text:
            turns.append(("USER" if t == "user" else "AGENT", text))
    users = [x for x in turns if x[0] == "USER"][-4:]
    agent = [x for x in turns if x[0] == "AGENT"][-2:]
    keep = [x for x in turns if x in users or x in agent]
    out, total = [], 0
    for who, text in reversed(keep):
        text = text if len(text) < 900 else text[:900] + "…"
        total += len(text)
        if total > max_chars:
            break
        out.append("%s: %s" % (who, text))
    return "\n".join(reversed(out))


def build_prompt(call):
    parts = ["ACTION (%s tool):\n%s" % (call["tool"], call["text"])]
    if call.get("description"):
        parts.append("Agent's description of it: " + call["description"])
    parts.append("Flagged by the rules as: " + "; ".join(call.get("labels") or ["unclear"]))
    parts.append("Working directory: " + call.get("cwd", "?"))
    if call.get("git"):
        parts.append("Git state:\n" + call["git"])
    if call.get("permission_mode") == "bypassPermissions":
        parts.append("Note: the session skips permission prompts; this vote is the only check before it runs.")
    if call.get("conversation"):
        parts.append("Recent conversation (oldest first):\n" + call["conversation"])
    else:
        parts.append("Recent conversation: not available.")
    return "\n\n".join(parts) + "\n\nVote."


def vote(call, cfg, on_vote=None):
    """Run the three units in parallel. on_vote(index, name, vote, reason) fires as each answers.

    Returns a record dict with votes, verdict ('approve'|'deny'), approvals, cost and latency."""
    import threading
    import time
    t0 = time.monotonic()
    prompt = build_prompt(call)
    results = [None] * 3
    lock = threading.Lock()

    def unit(i):
        name, aspect, lens = UNITS[i]
        so, cost, err = run_claude(_claude_argv(cfg, lens + COMMON, VOTE_SCHEMA), prompt, int(cfg.get("timeout", 30)))
        if err:
            v, reason = "deny", err
        else:
            v = "approve" if str(so.get("vote", "")).lower() == "approve" else "deny"
            reason = " ".join(str(so.get("reason", "")).split())[:160] or "(no reason given)"
        r = {"unit": name, "aspect": aspect, "vote": v, "reason": reason, "error": bool(err),
             "cost": cost, "latency": round(time.monotonic() - t0, 2)}
        with lock:
            results[i] = r
        if on_vote:
            try:
                on_vote(i, name, v, reason)
            except Exception:
                pass

    threads = [threading.Thread(target=unit, args=(i,), daemon=True) for i in range(3)]
    for t in threads:
        t.start()
    deadline = t0 + int(cfg.get("timeout", 30)) + 5
    for t in threads:
        t.join(max(0.1, deadline - time.monotonic()))
    for i in range(3):
        if results[i] is None:  # thread stuck past the deadline
            results[i] = {"unit": UNITS[i][0], "aspect": UNITS[i][1], "vote": "deny", "reason": "NO RESPONSE",
                          "error": True, "cost": 0.0, "latency": round(time.monotonic() - t0, 2)}
            if on_vote:
                on_vote(i, UNITS[i][0], "deny", "NO RESPONSE")
    approvals = sum(1 for r in results if r["vote"] == "approve")
    quorum = int(cfg.get("quorum", 2))
    return {"votes": results, "approvals": approvals, "quorum": quorum,
            "verdict": "approve" if approvals >= quorum else "deny",
            "cost": round(sum(r["cost"] for r in results), 6), "latency": round(time.monotonic() - t0, 2)}


def log_vote(record):
    import json
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(LOG, "a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def verdict_line(rec):
    """One line for Claude Code's transcript and the terminal."""
    marks = " · ".join("%s %s" % (v["unit"].split("-")[0], "承認" if v["vote"] == "approve" else "否定") for v in rec["votes"])
    head = "MAGI %d-%d %s" % (rec["approvals"], 3 - rec["approvals"],
                              "承認 APPROVED" if rec["verdict"] == "approve" else "否定 DENIED → asking you")
    denies = ["%s: %s" % (v["unit"], v["reason"]) for v in rec["votes"] if v["vote"] == "deny"]
    s = "%s  [%s]  %.1fs" % (head, marks, rec["latency"])
    if denies:
        s += "\n" + "\n".join(denies)
    return s
