# One-shot VM setup for bench_vm.py (what the M0.1 runs needed on the 2026-11 Colab image:
# torch 2.11+cu130, numpy 2.1). Run with `colab exec -f setup_vm.py`.
import os, subprocess
os.makedirs("/content/bench/out", exist_ok=True)
open("/tmp/cons.txt", "w").write("torch==2.11.0+cu130\ntorchaudio==2.11.0+cu130\n")
cmds = [
    "pip install -q uv",
    # pyannote 3.x breaks on torchaudio 2.11 (AudioMetaData removed); 4.x needs numpy>=2.2
    "uv pip install --system -c /tmp/cons.txt faster-whisper 'pyannote.audio>=4' nvidia-cublas-cu12 jiwer",
    "uv pip install --system -c /tmp/cons.txt 'nemo_toolkit[asr]'",
    # nemo 3.0 pins lightning 2.4 (2.6 drops NeptuneLogger); numba-cuda 0.28 breaks on numba 0.68
    "uv pip install --system -c /tmp/cons.txt 'lightning==2.4.0' 'numba-cuda>=0.30'",
]
for c in cmds:
    r = subprocess.run(c, shell=True, capture_output=True, text=True)
    print(c[:60], "->", r.returncode, r.stdout[-300:] if r.returncode else "")
