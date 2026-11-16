"""The 25 Hz frame buzz, taken out of a WAV take by default.

A synthetic high band whose gain repeats every 1920 samples (48000 / 25) is measured with
inspect_job.framebuzz_db before and after debuzz_file. The API sends debuzz true unless the
caller says false. The engine's default decoder is still the first --vae, and start.sh puts
YuE2-VAE-RS x3 v1 first when that file is installed: that choice does not remove this buzz.
The 25 Hz ripple is the decoder's frame clock (measured on standard, legacy and blend alike);
VAE-RS x3, the default since 2.0.7, was grown against a different fault (a 100 Hz envelope
ripple above 3 kHz, and lines at 6 and 12 kHz). Debuzz stays the cure either way.

    python3 lab/test_debuzz.py
"""
import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))
import api  # noqa: E402
import debuzz  # noqa: E402
import inspect_job  # noqa: E402

bad = []
ROOT = Path(__file__).resolve().parent.parent


def check(what, ok, got=""):
    print(("  ok    " if ok else "  FAIL  ") + what + ("" if ok else "  (" + str(got) + ")"))
    if not ok:
        bad.append(what)


def mean_db(x):
    return float(np.mean(inspect_job.framebuzz_db(x.mean(1), 48000)))


def band(n, rng, lo, hi):
    w = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1 / 48000)
    w[(f < lo) | (f >= hi)] = 0
    return np.fft.irfft(w, n)


def frame_gain(n, period):
    """A seam-peaked ripple, about ±2 dB, locked to `period` samples. Rich in 25 Hz harmonics at 1920."""
    t = np.arange(n) % period
    u = t / period
    return 0.8 + 0.4 * np.abs(u - 0.5) * 2


SR, P, SECS = 48000, 1920, 30
N = SR * SECS
rng = np.random.default_rng(7)
high = band(N, rng, 5000, 9000)
low = 0.15 * np.sin(2 * np.pi * 220 * np.arange(N) / SR)
buzzed = (high * frame_gain(N, P) + low)
buzzed *= 0.4 / np.max(np.abs(buzzed))
plain = (high + low)
plain *= 0.4 / np.max(np.abs(plain))
stereo = lambda m: np.stack([m, m * 0.97], 1)

with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    src, dst = tmp / "buzz.wav", tmp / "clean.wav"
    sf.write(src, stereo(buzzed).astype(np.float32), SR, subtype="PCM_24")
    before = mean_db(stereo(buzzed))
    report = debuzz.debuzz_file(src, dst)
    y, sr = sf.read(dst, dtype="float64", always_2d=True)
    after = mean_db(y)
    print(f"  buzz   framebuzz {before:.1f} dB -> {after:.1f} dB; fold {report['before']} -> {report['after']}; found {report['found']}")
    check("the synthetic ripple is a frame buzz (inspect, above 8 dB)", before > 8, round(before, 2))
    check("debuzz_file says the fold is the frame clock", report["found"] is True, report)
    check("inspect's buzz falls by more than 4 dB", after < before - 4, (round(before, 2), round(after, 2)))
    check("the fold at 1920 shrinks and stays under the strength's limit",
          report["after"]["frame"] < report["before"]["frame"] * 0.6 and max(abs(g) for g in report["gain_db"]) <= 3.01, report)

    src2, dst2 = tmp / "plain.wav", tmp / "plain-out.wav"
    sf.write(src2, stereo(plain).astype(np.float32), SR, subtype="PCM_24")
    plain_before = mean_db(stereo(plain))
    plain_report = debuzz.debuzz_file(src2, dst2)
    y2, _ = sf.read(dst2, dtype="float64", always_2d=True)
    plain_after = mean_db(y2)
    print(f"  plain  framebuzz {plain_before:.1f} dB -> {plain_after:.1f} dB; found {plain_report['found']}")
    check("a high band with no frame lock is not called a buzz", plain_report["found"] is False, plain_report)
    check("the locked ripple loses more buzz than that band", (before - after) > (plain_before - plain_after) + 2,
          (round(before - after, 2), round(plain_before - plain_after, 2)))

    # the engine's invocation: the same argv the server builds, one JSON line on stdout
    import subprocess
    cli = subprocess.run([sys.executable, str(ROOT / "lab" / "debuzz.py"), str(src), str(tmp / "cli.wav"),
                          "0.8", "2000", "50", "3", "1920", "48000", "PCM_24"],
                         capture_output=True, text=True)
    try:
        line = json.loads(cli.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        line = None
    check("the CLI the engine shells out still prints one JSON line", cli.returncode == 0 and line and line["found"] is True,
          (cli.returncode, cli.stderr[-200:], cli.stdout[-200:]))

# /api/v1 forwards debuzz, default true
asked = []
api._engine = lambda path, body=None, timeout=60: asked.append((path, body)) or {"id": 9}
ctx = {"collection": None, "outputs": ROOT, "config_dir": lambda: ROOT}


def post(payload):
    return api.handle("POST", "/api/v1/songs", {}, json.dumps(payload).encode(), {}, ctx)


code, body = post({"style": "folk ballad"})
check("a song with no debuzz field is sent true", code == 202 and asked[-1][0] == "/synth" and asked[-1][1]["debuzz"] is True, (code, body, asked[-1:]))
code, body = post({"style": "folk ballad", "debuzz": False})
check("debuzz false is forwarded", code == 202 and asked[-1][1]["debuzz"] is False, (code, asked[-1][1].get("debuzz")))
code, body = post({"style": "folk ballad", "debuzz": True})
check("debuzz true is forwarded", code == 202 and asked[-1][1]["debuzz"] is True, (code, asked[-1][1].get("debuzz")))
code, body = post({"style": "folk ballad", "debuzz": "no"})
check("debuzz that is not a bool is refused", code == 400 and "debuzz" in str(body), (code, body))
spec = api.openapi()["paths"]["/songs"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]
check("OpenAPI names debuzz as a boolean", spec.get("debuzz", {}).get("type") == "boolean", spec.get("debuzz"))
audio_params = [p["name"] for p in api.openapi()["paths"]["/takes/{name}/audio"]["get"].get("parameters", [])]
check("OpenAPI offers ?raw=1 on the take's audio", "raw" in audio_params, audio_params)

with tempfile.TemporaryDirectory() as raw_root:
    take = Path(raw_root) / "take-1"
    take.mkdir()
    (take / "meta.json").write_text("{}", encoding="utf-8")
    ctx_take = {"collection": None, "outputs": Path(raw_root), "config_dir": lambda: Path(raw_root)}
    try:
        api.handle("GET", "/api/v1/takes/take-1/audio", {"raw": "1"}, b"", {}, ctx_take)
        redirected = ""
    except api.Redirect as e:
        redirected = e.url
    check("?raw=1 redirects at the engine's raw file", redirected.endswith("raw=1") and "take-1" in redirected, redirected)

# default decoder: RS x3 v1 is first --vae when the weight is there; the first name is the default. Not a substitute for debuzz.
start = (ROOT / "start.sh").read_text(encoding="utf-8")
server = (ROOT / "build" / "tools" / "yue-server.cpp").read_text(encoding="utf-8")
page = (ROOT / "build" / "tools" / "console" / "app.js").read_text(encoding="utf-8")
rs_at = start.find("rsfirst+=(--vae")
std_at = start.find('--vae "standard=')
check("start.sh queues YuE2-VAE-RS x3 v1 (then v2) ahead of standard", 0 <= rs_at < std_at and "rs-x3-$rs" in start, (rs_at, std_at))
check("the engine's default decoder is the first --vae", "the first is the default" in server and 'default_vae' in server)
check("the page keeps that default when the decoder is still offered",
      "props.default_vae" in page and "/^rs-/" in page)

print("test_debuzz:", "FAILED " + ", ".join(bad) if bad else "all passed")
sys.exit(1 if bad else 0)
