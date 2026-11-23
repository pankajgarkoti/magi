"""bus_guard_ml: optional ML layer for bus_guard (local prompt-injection classifier).

Runs protectai/deberta-v3-base-prompt-injection-v2 (Apache-2.0) through onnxruntime on CPU.
Nothing leaves the machine: the model is read from the local Hugging Face cache, never fetched.

  available() -> bool            venv + model present (cheap; loads nothing)
  score(texts) -> list[float]    injection probability per text, max over 512-token chunks;
                                 None if the layer is unavailable or inference fails
  body(text) -> str              message body without the "Chat · 10:12 am Sender: " header

Score the full event text, header included (it measured better, and chat titles / sender names
are attacker-controlled too). On its own the classifier has a high false-positive rate on Hindi /
Hinglish / promo messages, so use it to escalate bus_guard's medium risk, not to quarantine alone.

Heavy imports (numpy, onnxruntime, tokenizers) happen on the first score() call, from the
venv at ~/.local/share/bus-guard/venv (override: BUS_GUARD_VENV). Everything fails soft.
BUS_GUARD_MODEL=/abs/path.onnx swaps in another export of the same model (e.g. an int8 one).

Setup (once):
  python3 -m venv ~/.local/share/bus-guard/venv
  ~/.local/share/bus-guard/venv/bin/pip install onnxruntime tokenizers numpy huggingface_hub
  ~/.local/share/bus-guard/venv/bin/python -c "from huggingface_hub import snapshot_download as d; \
      d('protectai/deberta-v3-base-prompt-injection-v2', allow_patterns=['onnx/*', 'LICENSE'])"

CLI:  python3 bus_guard_ml.py "text" [...]     prints the score per text (text as given)
      python3 bus_guard_ml.py --body "text"    strip the bus header first
"""
import os, pathlib, re, sys

VENV = pathlib.Path(os.environ.get("BUS_GUARD_VENV", "~/.local/share/bus-guard/venv")).expanduser()
REPO = "protectai/deberta-v3-base-prompt-injection-v2"
MODEL_FILE = "onnx/model.onnx"
TOKENIZER_FILE = "onnx/tokenizer.json"
INJECTION_LABEL = 1        # config.json id2label: 0 SAFE, 1 INJECTION
THRESHOLD = 0.9            # see tests/bus_guard/classifier-eval.md (use to escalate regex-medium only)
MAX_TOKENS = 512           # model limit, incl. [CLS] and [SEP]
STRIDE = 384               # window step in tokens -> 126-token overlap between chunks
BATCH = 1                  # batching padded chunks raised peak RSS ~1.3 -> 1.7 GB and was slower

_state = {}                # "sess", "tok" once loaded; "error" once loading failed


def _hf_cache():
    if os.environ.get("HF_HUB_CACHE"):
        return pathlib.Path(os.environ["HF_HUB_CACHE"]).expanduser()
    home = os.environ.get("HF_HOME") or os.path.join(os.environ.get("XDG_CACHE_HOME", "~/.cache"), "huggingface")
    return pathlib.Path(home).expanduser() / "hub"


def hf_file(repo, rel):
    """Path of a file in the local HF cache (refs/main first, then any snapshot), or None."""
    d = _hf_cache() / ("models--" + repo.replace("/", "--"))
    try:
        ref = (d / "refs" / "main").read_text().strip()
        p = d / "snapshots" / ref / rel
        if p.is_file():
            return p
    except OSError:
        pass
    for p in sorted(d.glob("snapshots/*/" + rel)):
        if p.is_file():
            return p
    return None


def _venv_site():
    if pathlib.Path(sys.prefix).resolve() == VENV.resolve():
        return "in-venv"
    p = VENV / "lib" / f"python{sys.version_info[0]}.{sys.version_info[1]}" / "site-packages"
    return str(p) if p.is_dir() else None


def _add_venv():
    site = _venv_site()
    if site and site != "in-venv" and site not in sys.path:
        sys.path.append(site)      # appended, so it never shadows the system's own packages
    return site is not None


def model_path():
    """The ONNX file to run: BUS_GUARD_MODEL (absolute path) if set, else the HF-cache copy."""
    o = os.environ.get("BUS_GUARD_MODEL")
    if o:
        return pathlib.Path(o).expanduser() if pathlib.Path(o).expanduser().is_file() else None
    return hf_file(REPO, MODEL_FILE)


def available():
    """True if the venv (for this Python version) and the model files are present."""
    if "error" in _state:
        return False
    if "sess" in _state:
        return True
    if not (_add_venv() and model_path() and hf_file(REPO, TOKENIZER_FILE)):
        return False
    import importlib.util
    return all(importlib.util.find_spec(m) for m in ("numpy", "onnxruntime", "tokenizers"))


def _load():
    if "sess" in _state or "error" in _state:
        return "sess" in _state
    try:
        if not available():
            raise RuntimeError("venv or model missing")
        import onnxruntime as ort
        from tokenizers import Tokenizer
        tok = Tokenizer.from_file(str(hf_file(REPO, TOKENIZER_FILE)))
        tok.no_truncation()
        tok.no_padding()
        so = ort.SessionOptions()
        so.log_severity_level = 3
        sess = ort.InferenceSession(str(model_path()), so, providers=["CPUExecutionProvider"])
        cls, sep, pad = (tok.token_to_id(t) for t in ("[CLS]", "[SEP]", "[PAD]"))
        _state.update(sess=sess, tok=tok, ids=(cls, sep, 0 if pad is None else pad),
                      inputs={i.name for i in sess.get_inputs()})
        return True
    except Exception as e:  # noqa: BLE001 - fail soft, whatever went wrong
        _state["error"] = f"{type(e).__name__}: {e}"
        return False


def _chunks(ids):
    body = MAX_TOKENS - 2
    if len(ids) <= body:
        return [ids]
    out, i = [], 0
    while True:
        out.append(ids[i:i + body])
        if i + body >= len(ids):
            return out
        i += STRIDE


def score(texts):
    """Injection probability per text (max over overlapping 512-token chunks), or None."""
    if not _load():
        return None
    try:
        import numpy as np
        tok, sess = _state["tok"], _state["sess"]
        cls, sep, pad = _state["ids"]
        items = []                                   # (text index, token ids with specials)
        for n, t in enumerate(texts):
            ids = tok.encode(str(t or ""), add_special_tokens=False).ids
            items += [(n, [cls] + c + [sep]) for c in _chunks(ids)]
        items.sort(key=lambda x: len(x[1]))
        best = [0.0] * len(texts)
        for b in range(0, len(items), BATCH):
            batch = items[b:b + BATCH]
            L = max(len(x[1]) for x in batch)
            inp = np.full((len(batch), L), pad, dtype=np.int64)
            mask = np.zeros((len(batch), L), dtype=np.int64)
            for r, (_, ids) in enumerate(batch):
                inp[r, :len(ids)] = ids
                mask[r, :len(ids)] = 1
            feed = {"input_ids": inp, "attention_mask": mask}
            if "token_type_ids" in _state["inputs"]:
                feed["token_type_ids"] = np.zeros_like(inp)
            logits = sess.run(None, feed)[0].astype(np.float64)
            logits -= logits.max(axis=1, keepdims=True)
            p = np.exp(logits)
            p = p[:, INJECTION_LABEL] / p.sum(axis=1)
            for (n, _), v in zip(batch, p):
                best[n] = max(best[n], float(v))
        return best
    except Exception as e:  # noqa: BLE001
        _state["last_error"] = f"{type(e).__name__}: {e}"
        return None


# "Chat · 10:12 am Sender: msg" (WhatsApp probe), "Page title · msg", "new comment by u/x: msg"
_HDR = [
    re.compile(r"^.{1,80}? · \d{1,2}:\d{2}\s*(?:[ap]\.?m\.?)?\s+[^\n]{1,60}?:\s", re.I),
    re.compile(r"^new comment by u/[\w-]+:\s"),
    re.compile(r"^[^\n·]{1,80} · "),
]


def body(text):
    """Message body without the bus header prefix (unchanged if there is no header)."""
    t = str(text or "")
    for rx in _HDR:
        m = rx.match(t)
        if m:
            return t[m.end():]
    return t


def error():
    return _state.get("error") or _state.get("last_error")


if __name__ == "__main__":
    args = sys.argv[1:]
    strip = "--body" in args
    args = [a for a in args if a != "--body"]
    if not args:
        print(__doc__.split("CLI:")[1].strip())
        sys.exit(2)
    s = score([body(a) if strip else a for a in args])
    if s is None:
        print(f"unavailable ({error() or 'venv or model missing'})", file=sys.stderr)
        sys.exit(1)
    for a, v in zip(args, s):
        flag = "INJECTION" if v >= THRESHOLD else "safe"
        print(f"{v:.4f}  {flag}" + (f"  {a[:70]!r}" if len(args) > 1 else ""))
