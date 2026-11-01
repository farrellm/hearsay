"""M0.3: timeline drift between an original file and its prepared 16 kHz mono Opus copy.

For each episode: transcode orig -> prepared.opus (the §3 recipe), decode both to 16 kHz mono
float32 with ffmpeg, and measure the lag of the prepared copy against the original every
5 minutes by cross-correlating 10 s windows (±250 ms search, 1-sample = 62.5 µs resolution).
"""
# /// script
# dependencies = ["numpy"]
# ///
import json, pathlib, subprocess, sys
import numpy as np

SR = 16000
DATA = pathlib.Path(__file__).parent / "data"
PREP = ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "libopus", "-b:a", "32k", "-application", "voip"]


def run(*a):
    subprocess.run(a, check=True, capture_output=True)


def decode(path: pathlib.Path) -> np.ndarray:
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(SR),
                          "-f", "f32le", "-"], check=True, capture_output=True).stdout
    return np.frombuffer(out, dtype=np.float32)


def lag_at(a: np.ndarray, b: np.ndarray, t: float, win=10.0, search=0.25) -> tuple[float, float]:
    """Lag (s) of b relative to a around time t: b[i + lag] ~ a[i]. Returns (lag, peak corr)."""
    s, w, m = int(t * SR), int(win * SR), int(search * SR)
    x = a[s:s + w]
    y = b[max(0, s - m):s + w + m]
    if len(x) < w or len(y) < w + 2 * m or np.std(x) < 1e-4:
        return float("nan"), 0.0
    n = 1 << int(np.ceil(np.log2(len(y) + len(x))))
    c = np.fft.irfft(np.fft.rfft(y, n) * np.conj(np.fft.rfft(x, n)), n)[: 2 * m + 1]
    # normalise by local energy of y so the peak is a correlation coefficient
    e = np.sqrt(np.convolve(y.astype(np.float64) ** 2, np.ones(w), "valid")[: 2 * m + 1])
    c = c / (e * np.linalg.norm(x) + 1e-9)
    k = int(np.argmax(c))
    # parabolic sub-sample refinement
    if 0 < k < len(c) - 1:
        d = c[k - 1] - 2 * c[k] + c[k + 1]
        k_f = k + (0.5 * (c[k - 1] - c[k + 1]) / d if d else 0)
    else:
        k_f = k
    return (k_f - m) / SR, float(c[k])


def main(ids):
    rows = []
    for d in sorted(DATA.iterdir()):
        if ids and d.name not in ids:
            continue
        meta = json.loads((d / "meta.json").read_text())
        orig = d / meta["file"]
        prep = d / "prepared.opus"
        if not prep.exists():
            run("ffmpeg", "-v", "error", "-y", "-i", str(orig), *PREP, str(prep))
        a, b = decode(orig), decode(prep)
        dur = len(a) / SR
        lags = []
        for t in np.arange(5.0, dur - 15, 300.0):
            lag, corr = lag_at(a, b, float(t))
            if corr > 0.8:
                lags.append((round(float(t)), lag * 1000, corr))
        ms = np.array([l for _, l, _ in lags])
        row = {"id": d.name, "codec": orig.suffix, "dur_s": round(dur, 2),
               "len_diff_ms": round((len(b) - len(a)) / SR * 1000, 2),
               "orig_MB": round(orig.stat().st_size / 1e6, 1), "prep_MB": round(prep.stat().st_size / 1e6, 1),
               "n": len(lags), "lag_first_ms": round(ms[0], 2), "lag_last_ms": round(ms[-1], 2),
               "drift_ms": round(ms.max() - ms.min(), 2), "max_abs_ms": round(np.abs(ms).max(), 2)}
        rows.append(row)
        print(json.dumps(row), flush=True)
        (d / "drift.json").write_text(json.dumps({"summary": row, "points": lags}, indent=1))
    return rows


if __name__ == "__main__":
    main(set(sys.argv[1:]))
