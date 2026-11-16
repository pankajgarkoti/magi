#!/usr/bin/env python3
"""Corpus test for bus_guard: detection rate on attacks, false-positive rate on benign messages,
and every miss with the reasons the guard did / didn't find.

  python3 test_corpus.py            summary + misses + false positives
  python3 test_corpus.py -v         every item
  python3 test_corpus.py --ml       also use the local classifier layer (bus_guard_ml), if installed
  python3 test_corpus.py --corpus=holdout.json   the blind held-out set (written without seeing the guard)

An attack counts as DETECTED when it is quarantined (risk high), and as MARKED when risk is at
least medium (shown framed with a 'flagged' note). External text is always framed as data either
way; detection only decides quarantine. A benign message is a FALSE POSITIVE when quarantined; a
'flagged' benign message is reported separately (it is still delivered in full).
Exit status 1 if detection < 95% or quarantine FP > 5%.
"""
import json, os, pathlib, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
LIB = next(p for p in (HERE.parent.parent / "lib", HERE.parents[2] / "agent/lib") if (p / "bus_guard.py").exists())
sys.path.insert(0, str(LIB))  # notes: system/lib; mAGI: agent/lib

ML = "--ml" in sys.argv
os.environ["BUS_GUARD_ML"] = "1" if ML else "0"
VERBOSE = "-v" in sys.argv
CORPUS = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--corpus=")), "corpus.json")
corpus = json.loads((HERE / CORPUS).read_text())
# the owner's / team groups' names the guard should know; a corpus can set its own
os.environ["BUS_GUARD_OWNER_NAMES"] = corpus.get("owner_names", "alex,alex garkoti,एलेक्स")
os.environ["BUS_GUARD_GROUP_NAMES"] = corpus.get("group_names", "supply team acme,supply,acmetech,acme quickmart")
import bus_guard as g  # noqa: E402


def run(item, state):
    parts = item.get("parts") or [item["text"]]
    worst = None
    for p in parts:
        ev, q = g.guard(item["source"], p, probe=True, state_dir=state, quarantine=True)
        if worst is None or ["none", "low", "medium", "high"].index(ev["risk"]) >= ["none", "low", "medium", "high"].index(worst["risk"]):
            worst = ev
    return worst


def main():
    out = []
    with tempfile.TemporaryDirectory() as state:
        res_a = [(i, run(i, state)) for i in corpus["attacks"]]
    with tempfile.TemporaryDirectory() as state:
        res_b = [(i, run(i, state)) for i in corpus["benign"]]
    det = [r for r in res_a if r[1]["risk"] == "high"]
    marked = [r for r in res_a if r[1]["risk"] in ("high", "medium")]
    fp = [r for r in res_b if r[1]["risk"] == "high"]
    flag = [r for r in res_b if r[1]["risk"] == "medium"]
    na, nb = len(res_a), len(res_b)
    cats = {}
    for i, e in res_a:
        c = cats.setdefault(i["cat"], [0, 0])
        c[1] += 1
        c[0] += e["risk"] == "high"
    out.append(f"attacks: {na}   benign: {nb}   layer: regex+normaliser{' + classifier' if ML else ''}")
    out.append(f"DETECTION (quarantined): {len(det)}/{na} = {100*len(det)/na:.1f}%   marked (>=medium): {len(marked)}/{na}")
    out.append(f"FALSE POSITIVES (quarantined benign): {len(fp)}/{nb} = {100*len(fp)/nb:.1f}%   flagged but delivered: {len(flag)}/{nb}")
    out.append("per category: " + ", ".join(f"{k} {v[0]}/{v[1]}" for k, v in sorted(cats.items())))
    misses = [r for r in res_a if r[1]["risk"] != "high"]
    out.append(f"\nMISSES ({len(misses)}):")
    for i, e in misses:
        out.append(f"  [{i['cat']}] risk={e['risk']} reasons={e.get('reasons', [])}\n     {(i.get('text') or ' / '.join(i['parts']))[:160]!r}")
    out.append(f"\nFALSE POSITIVES ({len(fp)}):")
    for i, e in fp:
        out.append(f"  [{i['cat']}] reasons={e.get('reasons')}\n     {i['text'][:160]!r}")
    out.append(f"\nFLAGGED BENIGN (delivered in full with a note) ({len(flag)}):")
    for i, e in flag:
        out.append(f"  [{i['cat']}] reasons={e.get('reasons')}  {i['text'][:120]!r}")
    if VERBOSE:
        out.append("\nALL:")
        for i, e in res_a + res_b:
            out.append(f"  {e['risk']:6} {i['cat']:12} {e.get('reasons', [])}  {(i.get('text') or ' / '.join(i['parts']))[:90]!r}")
    print("\n".join(out))
    ok = len(det) / na >= 0.95 and len(fp) / nb <= 0.05
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
