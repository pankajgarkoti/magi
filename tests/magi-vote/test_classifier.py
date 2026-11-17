#!/usr/bin/env python3
"""Classifier table for magi-vote: which commands call a vote and which pass untouched.

    python3 -I tests/magi-vote/test_classifier.py        (or: python3 -m pytest tests/magi-vote)

VOTE   = the rules are sure: three units vote.
UNSURE = the rules flag it but aren't sure: the classifier model decides (ambiguous = "model").
PASS   = no vote, no model call, no added latency.
"""
import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "packages", "magi-vote", ".local", "bin"))
sys.dont_write_bytecode = True
import magi_vote as mv  # noqa: E402

CFG = dict(mv.DEFAULTS)

VOTE = [
    # push
    ("git push", "push"),
    ("git push origin main", "push"),
    ("git push --force origin main", "push"),
    ("git push -f", "push"),
    ("git push --force-with-lease origin feature", "push"),
    ("git push origin +main", "push"),
    ("git push origin :old-branch", "push"),
    ("git push --delete origin v1.0", "push"),
    ("git -C ~/src/app push --tags", "push"),
    ("git add -A && git commit -m 'wip' && git push", "push"),
    ("cd repo; git push origin HEAD", "push"),
    ("bash -c 'git push origin main'", "push"),
    ("ssh build@ci 'cd app && git push'", "push"),
    ("timeout 60 git push", "push"),
    ("GIT_SSH_COMMAND='ssh -i k' git push", "push"),
    # history
    ("git reset --hard HEAD~3", "history"),
    ("git reset --hard origin/main", "history"),
    ("git rebase -i HEAD~5", "history"),
    ("git rebase main", "history"),
    ("git commit --amend --no-edit", "history"),
    ("git filter-branch --tree-filter 'rm secrets' HEAD", "history"),
    ("git filter-repo --path secrets.env --invert-paths", "history"),
    ("git clean -fdx", "history"),
    ("git checkout -- .", "history"),
    ("git restore .", "history"),
    ("git branch -D feature/old", "history"),
    ("git stash drop", "history"),
    ("git stash clear", "history"),
    ("git reflog expire --expire=now --all", "history"),
    ("git gc --prune=now", "history"),
    # delete
    ("rm -rf node_modules/../src", "delete"),
    ("rm -rf ~/projects/app", "delete"),
    ("rm -r data/", "delete"),
    ("rm -rf /", "delete"),
    ("sudo rm -rf /var/lib/postgres", "delete"),
    ("rm *.csv", "delete"),
    ("find . -name '*.bak' -delete", "delete"),
    ("find /data -mtime +30 -exec rm {} \\;", "delete"),
    ("shred -u secrets.txt", "delete"),
    ("sudo mkfs.ext4 /dev/sdb1", "delete"),
    ("sudo dd if=image.iso of=/dev/sdb bs=4M", "delete"),
    ("rsync -a --delete src/ backup/", "delete"),
    ("docker volume rm pgdata", "delete"),
    ("docker system prune -af", "delete"),
    ("kubectl delete namespace staging", "delete"),
    ("terraform destroy -auto-approve", "delete"),
    ("aws s3 rm s3://bucket/ --recursive", "delete"),
    ("gh repo delete owner/repo --yes", "delete"),
    ("curl -X DELETE https://api.example.com/v1/users/42", "delete"),
    ("crontab -r", "delete"),
    ("echo $(rm -rf build/../data)", "delete"),
    # send
    ("echo hi | mail -s test boss@example.com", "send"),
    ("sendmail -t < message.eml", "send"),
    ("curl -X POST https://hooks.slack.com/services/T0/B0/xyz -d '{\"text\":\"hi\"}'", "send"),
    ("curl -d 'msg=hello' https://api.example.com/notify", "send"),
    ("curl --json '{\"a\":1}' https://example.com/api", "send"),
    ("wget --post-data 'a=1' https://example.com/form", "send"),
    ("http POST api.example.com/messages text=hi", "send"),
    ("git send-email --to list@example.org 0001.patch", "send"),
    ("aws ses send-email --from a@b.c --to d@e.f", "send"),
    # publish
    ("npm publish", "publish"),
    ("cargo publish", "publish"),
    ("twine upload dist/*", "publish"),
    ("docker push ghcr.io/me/app:latest", "publish"),
    ("gh pr create --fill", "publish"),
    ("gh pr merge 12 --squash", "publish"),
    ("gh issue comment 5 --body 'done'", "publish"),
    ("gh release create v1.0 dist/*", "publish"),
    ("gh api -X POST repos/o/r/issues -f title=x", "publish"),
    ("vercel --prod", "publish"),
    ("helm upgrade app ./chart", "publish"),
    ("terraform apply", "publish"),
    # database
    ("psql postgres://u@prod-db.internal/app -c 'DELETE FROM users'", "database"),
    ("psql -h db.example.com -c 'DROP TABLE orders'", "database"),
    ("mysql -h production.db -e \"UPDATE users SET plan='free'\"", "database"),
    ("redis-cli -h prod-cache FLUSHALL", "database"),
    # payment
    ("stripe refunds create --charge ch_123", "payment"),
    ("curl -X POST https://api.stripe.com/v1/payouts -d amount=5000", "payment"),
    # packages
    ("sudo dnf install -y htop", "packages"),
    ("sudo dnf remove firefox", "packages"),
    ("dnf upgrade --refresh", "packages"),
    ("sudo apt-get install -y build-essential", "packages"),
    ("sudo rpm -i pkg.rpm", "packages"),
    ("flatpak install flathub org.gimp.GIMP", "packages"),
    ("sudo pacman -Syu", "packages"),
    ("sudo pip install requests", "packages"),
    ("sudo npm install -g pnpm", "packages"),
    ("sudo dnf copr enable sdegler/hyprland", "packages"),
    # system
    ("sudo systemctl disable sshd", "system"),
    ("systemctl reboot", "system"),
    ("sudo shutdown -h now", "system"),
    ("sudo tee /etc/hosts < hosts", "system"),
    ("sudo chown -R root /home/user", "system"),
    ("sudo userdel bob", "system"),
]

UNSURE = [
    "rm notes.txt",
    "rm -f old.json",
    "make deploy",
    "npm run release",
    "./deploy.sh production",
    "bash scripts/publish.sh",
    "python -c 'import shutil; shutil.rmtree(\"data\")'",
    "python3 -c \"import requests; requests.post('https://x.io', json={})\"",
    "kubectl apply -f k8s/",
    "psql -h prod-db -c 'select 1'",
    "ansible-playbook site.yml",
]

PASS = [
    "ls -la",
    "git status",
    "git diff HEAD~1",
    "git log --oneline -20",
    "git add -A",
    "git commit -m 'Fix the parser'",
    "git commit -m 'git push later; rm -rf is bad'",
    "git checkout -b feature/x",
    "git checkout main",
    "git switch -c topic",
    "git restore --staged file.py",
    "git fetch origin",
    "git pull --rebase",
    "git push --dry-run",
    "git stash",
    "git stash pop",
    "git rebase --continue",
    "git rebase --abort",
    "git branch -d merged-branch",
    "git clean -n",
    "echo 'git push --force' >> notes.md",
    "grep -rn 'rm -rf' scripts/",
    "rg 'DROP TABLE' migrations/",
    "printf '%s\\n' 'sudo dnf install x'",
    "cat README.md | head -20",
    "npm install",
    "npm test",
    "npm run build",
    "pip install -r requirements.txt",
    "uv pip install ruff",
    "pytest -x tests/",
    "cargo build --release",
    "make",
    "make test",
    "python3 script.py",
    "rm -rf node_modules",
    "rm -rf build/ dist/",
    "rm -rf /tmp/scratch-123",
    "rm -f /tmp/out.log",
    "rm app.pyc",
    "rm -rf __pycache__ .pytest_cache",
    "mkdir -p build && cd build && cmake ..",
    "curl https://example.com",
    "curl -s https://api.github.com/repos/o/r | jq .stargazers_count",
    "curl -X POST http://localhost:8080/api/test -d '{}'",
    "curl -d 'a=1' http://127.0.0.1:3000/form",
    "curl -X GET https://api.example.com/items",
    "wget https://example.com/file.tar.gz",
    "http GET api.example.com/users",
    "gh pr list",
    "gh pr view 12",
    "gh issue list --state open",
    "gh api repos/o/r/pulls",
    "gh run view 123 --log",
    "docker ps",
    "docker build -t app .",
    "docker run --rm -it app",
    "kubectl get pods -A",
    "kubectl logs deploy/app",
    "terraform plan",
    "terraform state list",
    "aws s3 ls s3://bucket/",
    "aws s3 cp s3://bucket/file.txt .",
    "psql -h localhost -c 'select count(*) from users'",
    "sqlite3 dev.db 'select * from t'",
    "dnf search hyprland",
    "dnf list installed",
    "rpm -qa | grep hypr",
    "sudo -n true",
    "sudo journalctl -u sshd -n 50",
    "sudo dmesg | tail",
    "systemctl --user restart waybar",
    "systemctl status sshd",
    "hyprctl reload",
    "find . -name '*.py' -newer setup.py",
    "stripe listen --forward-to localhost:3000",
    "ssh host 'uptime'",
    "bash -c 'ls -la && pwd'",
    "tmux send-keys -t 1 'git push' Enter" if False else "tmux list-windows",
    "eva-theme nerv",
    "for f in *.md; do wc -l \"$f\"; done",
    "cat <<'EOF' > notes.txt\nrun git push --force later\nrm -rf everything\nEOF",
]


def _launcher():
    from importlib.machinery import SourceFileLoader
    return SourceFileLoader("magi_vote_launcher", os.path.join(ROOT, "packages", "magi-vote", ".local", "bin", "magi-vote")).load_module()


class TestClassifier(unittest.TestCase):
    def test_prefilter_superset(self):
        """Every command the rules flag must get past the launcher's keyword filter."""
        L = _launcher()
        import json
        flagged = [c for c, _ in VOTE] + UNSURE
        missed = [c for c in flagged if not L.candidate("Bash", {"command": c})]
        self.assertEqual(missed, [])
        # the hook scans the raw stdin JSON (escaped newlines and quotes), on one line or a new one
        for c in flagged:
            for cmd in (c, "cd /srv/app\n" + c, "true && " + c):
                for raw in (json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}}),
                            json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}}, separators=(",", ":"))):
                    self.assertTrue(L.raw_candidate(raw), raw)
        skipped = [c for c in PASS if not L.candidate("Bash", {"command": c})]
        print("\n  prefilter: %d of %d harmless commands exit before the classifier loads" % (len(skipped), len(PASS)),
              file=sys.stderr)
        for c in PASS:  # whatever the prefilter lets through, the rules still pass
            if L.candidate("Bash", {"command": c}):
                self.assertEqual(mv.triage(mv.classify_command(c), CFG)[0], "pass", c)

    def test_vote(self):
        bad = []
        for cmd, cat in VOTE:
            hits = mv.classify_command(cmd)
            action, counted = mv.triage(hits, CFG)
            if action != "vote" or cat not in {h.category for h in counted}:
                bad.append("%-60s want vote/%s, got %s %s" % (cmd[:60], cat, action, hits))
        self.assertEqual(bad, [], "\n" + "\n".join(bad))

    def test_unsure(self):
        bad = []
        for cmd in UNSURE:
            action, _ = mv.triage(mv.classify_command(cmd), CFG)
            if action != "unsure":
                bad.append("%-60s want unsure, got %s %s" % (cmd[:60], action, mv.classify_command(cmd)))
        self.assertEqual(bad, [], "\n" + "\n".join(bad))

    def test_pass(self):
        bad = []
        for cmd in PASS:
            hits = mv.classify_command(cmd)
            action, _ = mv.triage(hits, CFG)
            if action != "pass":
                bad.append("%-60s want pass, got %s %s" % (cmd[:60].replace("\n", " "), action, hits))
        self.assertEqual(bad, [], "\n" + "\n".join(bad))

    def test_triggers_config(self):
        cfg = dict(CFG, triggers=["delete"])
        self.assertEqual(mv.triage(mv.classify_command("git push"), cfg)[0], "pass")
        self.assertEqual(mv.triage(mv.classify_command("rm -rf ~/x"), cfg)[0], "vote")
        self.assertEqual(mv.triage(mv.classify_command("rm notes.txt"), dict(CFG, ambiguous="pass"))[0], "pass")
        self.assertEqual(mv.triage(mv.classify_command("rm notes.txt"), dict(CFG, ambiguous="vote"))[0], "vote")

    def test_mcp(self):
        cases = [
            ("mcp__slack__send_message", {"channel": "C1", "text": "hi"}, "vote"),
            ("mcp__gmail__send_email", {"to": "a@b.c"}, "vote"),
            ("mcp__github__create_pull_request", {"title": "x"}, "vote"),
            ("mcp__github__merge_pull_request", {"n": 1}, "vote"),
            ("mcp__stripe__create_refund", {"charge": "ch_1"}, "vote"),
            ("mcp__linear__delete_issue", {"id": "X-1"}, "vote"),
            ("mcp__supabase__execute_sql", {"query": "DROP TABLE users"}, "vote"),
            ("mcp__slack__list_channels", {}, "pass"),
            ("mcp__github__get_pull_request", {"n": 1}, "pass"),
            ("mcp__gmail__search_threads", {"q": "x"}, "pass"),
            ("mcp__supabase__execute_sql", {"query": "select 1"}, "pass"),
            ("mcp__claude-in-chrome__computer", {"action": "left_click"}, "pass"),
            ("mcp__chrome-devtools__take_screenshot", {}, "pass"),
        ]
        bad = []
        for name, inp, want in cases:
            hits, _ = mv.classify_tool(name, inp, CFG)
            got = mv.triage(hits, CFG)[0]
            if got != want:
                bad.append("%s want %s got %s %s" % (name, want, got, hits))
        self.assertEqual(bad, [], "\n" + "\n".join(bad))
        self.assertEqual(mv.triage(mv.classify_tool("mcp__slack__send_message", {}, dict(CFG, mcp=False))[0], CFG)[0], "pass")

    def test_other_tools_pass(self):
        for name, inp in (("Read", {"file_path": "/etc/passwd"}), ("Edit", {"file_path": "x"}), ("Write", {"file_path": "x"}),
                          ("WebFetch", {"url": "https://x"}), ("Glob", {"pattern": "*"})):
            self.assertEqual(mv.classify_tool(name, inp, CFG)[0], [])

    def test_fast(self):
        """The rules alone must be cheap: well under a millisecond per command on average."""
        cmds = [c for c, _ in VOTE] + UNSURE + PASS
        t = time.perf_counter()
        for _ in range(20):
            for c in cmds:
                mv.classify_command(c)
        per = (time.perf_counter() - t) / (20 * len(cmds)) * 1000
        self.assertLess(per, 1.0, "%.3f ms per command" % per)
        print("\n  rules: %.3f ms per command over %d commands" % (per, len(cmds)), file=sys.stderr)

    def test_config_quorum(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write("quorum = 3\nmodel = 'x'\nbogus = 1\n")
        cfg = mv.load_config(f.name)
        os.unlink(f.name)
        self.assertEqual(cfg["quorum"], 3)
        self.assertEqual(cfg["model"], "x")
        self.assertNotIn("bogus", cfg)
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write("enabled = [broken\n")
        cfg = mv.load_config(f.name)
        os.unlink(f.name)
        self.assertTrue(cfg["enabled"], "a broken config must keep the safety net on")
        self.assertIn("config_error", cfg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
