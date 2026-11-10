#!/usr/bin/env bash
# Build the MAGI local voice runtime in DIR (default /usr/lib/magi/voice; used by the image
# build and by agent/setup.sh): a venv with faster-whisper (MIT) and piper-tts
# (GPL-3.0-or-later), the whisper base.en model (CTranslate2, MIT) and the Piper voice
# en_US-ljspeech-medium (LJ Speech dataset, public domain). Needs network once.
set -euo pipefail
DIR=${1:-/usr/lib/magi/voice}
FW_VERSION=${FW_VERSION:-1.2.1}
PIPER_VERSION=${PIPER_VERSION:-1.8.0}
mkdir -p "$DIR/models" "$DIR/voices"
[ -x "$DIR/venv/bin/python" ] || "${VOICE_PYTHON:-python3}" -m venv "$DIR/venv"
"$DIR/venv/bin/pip" install -q --no-cache-dir --upgrade pip
"$DIR/venv/bin/pip" install -q --no-cache-dir "faster-whisper==$FW_VERSION" "piper-tts==$PIPER_VERSION"
# models: plain HTTPS from pinned Hugging Face revisions, sha256 checked
WHISPER=https://huggingface.co/Systran/faster-whisper-base.en/resolve/3d3d5dee26484f91867d81cb899cfcf72b96be6c
PIPER=https://huggingface.co/rhasspy/piper-voices/resolve/cf910fb59f1d79ab3b625a63993a108751c6c25d/en/en_US/ljspeech/medium
fetch() {  # fetch URL DEST SHA256
  [ -s "$2" ] && echo "$3  $2" | sha256sum -c --quiet - 2>/dev/null && return 0
  curl -fsSL --retry 3 -o "$2.part" "$1"
  echo "$3  $2.part" | sha256sum -c --quiet -
  mv "$2.part" "$2"
}
mkdir -p "$DIR/models/whisper-base.en"
fetch "$WHISPER/config.json"    "$DIR/models/whisper-base.en/config.json"    f3bc3821e9fc76a27bae538e11ae5b677dcdd352b4600429ce7951d398569aeb
fetch "$WHISPER/model.bin"      "$DIR/models/whisper-base.en/model.bin"      2a166925539a16005f14ff328359f9b9adb9dc4fb631bb3b227526862e93e2ef
fetch "$WHISPER/tokenizer.json" "$DIR/models/whisper-base.en/tokenizer.json" 929c5252409436dce1b38a75d1abbcb5e132d170d8e324e4e04ed915fa2d22df
fetch "$WHISPER/vocabulary.txt" "$DIR/models/whisper-base.en/vocabulary.txt" ff77588746d3a2595d32ab5b69ffd7b95ce2441ac57533cb66fc3eb575a115cf
fetch "$PIPER/en_US-ljspeech-medium.onnx"      "$DIR/voices/en_US-ljspeech-medium.onnx"      6f52a751e2349abe7a76735eb09dc1875298c77ea2342ffd2fef79ff81b87f22
fetch "$PIPER/en_US-ljspeech-medium.onnx.json" "$DIR/voices/en_US-ljspeech-medium.onnx.json" 141d612cc0a95ed7efc1ca936b845c2364967f2e9217c5dbfcf69fc4d6c65860
fetch "$PIPER/MODEL_CARD"                      "$DIR/voices/en_US-ljspeech-medium.MODEL_CARD" fbee1529c89d36b3fe76d7e9f3f832dce17f44900a52d76a9bda735654766b4d
cat > "$DIR/README" <<TXT
MAGI local voice runtime. Nothing here sends audio anywhere.
  venv/      faster-whisper $FW_VERSION (MIT), piper-tts $PIPER_VERSION (GPL-3.0-or-later,
             source: https://github.com/OHF-Voice/piper1-gpl), and their dependencies
  models/whisper-base.en/   Systran/faster-whisper-base.en (MIT; OpenAI Whisper base.en)
  voices/en_US-ljspeech-medium.*   Piper voice trained on LJ Speech (public domain)
TXT
find "$DIR/venv" -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
du -sh "$DIR" | sed 's/^/voice runtime: /'
