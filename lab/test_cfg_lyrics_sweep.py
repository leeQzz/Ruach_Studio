"""cfg_lyrics per backbone. The sweep's synth and score are fakes. No torch and no model are imported.
python3 lab/test_cfg_lyrics_sweep.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cfg_lyrics_sweep as sweep  # noqa: E402

bad = 0


def check(name, ok, detail=None):
    global bad
    print(("  ok    " if ok else "  FAIL  ") + name + ("" if ok or detail is None else "  " + repr(detail)))
    bad += not ok


check("RS-TW's chosen lyrics guidance is 2.4", sweep.DEFAULTS["RS-TW"] == 2.4)
check("RS-FW's chosen lyrics guidance is 2.2", sweep.DEFAULTS["RS-FW"] == 2.2)
check("both chosen points sit on the grid, under 2.6",
      all(sweep.DEFAULTS[b] in sweep.GRID and sweep.DEFAULTS[b] <= 2.6 for b in sweep.BACKBONES))
check("a merge file names its backbone",
      sweep.backbone_of("RS-TW-2.0x0.75") == "RS-TW" and sweep.backbone_of("RS-FW-2.4x0.75.gguf") == "RS-FW")
check("an unknown backbone keeps 2.2", sweep.default_cfg("YuE2") == 2.2 and sweep.default_cfg("RS-TW-2.0x0.75") == 2.4)

seen = []

def synth(req):
    seen.append(dict(req))
    return {"take": f"{req['backbone']}-{req['cfg_lyrics']}-{req['lm_seed']}"}

def score(take, lyrics):
    return {"1.4": 0.5, "1.8": 0.4, "2.2": 0.3, "2.4": 0.22, "2.6": 0.35}[take.split("-")[2]]

rows = sweep.sweep("RS-TW", "[Verse]\nla la", "folk", synth, score, seeds=(7, 8))
check("every cell of the grid, for each seed, reports a word error",
      len(rows) == len(sweep.GRID) * 2 and all("wer" in r and r["backbone"] == "RS-TW" for r in rows), len(rows))
check("the request carried the guidance and the seed",
      seen[0]["cfg_lyrics"] == 1.4 and seen[0]["lm_seed"] == 7 and "torch" not in sys.modules)
text = sweep.format_rows(rows)
check("the report names the word error", "wer" in text and "0.22" in text, text)
check("the command's own report names both defaults and makes nothing",
      "RS-TW  2.4" in sweep.report() and "RS-FW  2.2" in sweep.report())
try:
    sweep.sweep("other", "la", "folk", synth, score)
    check("a backbone off the list is refused", False)
except ValueError:
    check("a backbone off the list is refused", True)

page = Path(__file__).resolve().parents[1].joinpath("build/tools/console/app.js").read_text(encoding="utf-8")
check("the Console holds the same defaults", ' "RS-TW": "2.4", "RS-FW": "2.2" ' in page, "missing")

print("ok" if not bad else f"{bad} failed")
sys.exit(1 if bad else 0)
