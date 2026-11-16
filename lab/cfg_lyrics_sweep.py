"""Sweep cfg_lyrics for one backbone and report the word error.

The grid is the same for RS-TW and RS-FW. The defaults are the chosen working
points, not a number a sweep just invented: RS-TW is pulled harder toward the
words (its error runs higher) and stays under Viktor's 2.6 ceiling; RS-FW keeps
the page's long-standing 2.2. Both sit on the grid, so a sweep measures them.

    python3 lab/cfg_lyrics_sweep.py --report

A real sweep is sweep(backbone, lyrics, style, synth, score). synth(req) makes
one take, score(take, lyrics) is gloss_core's word error. This file does not
load a model. Tests pass fakes.
"""
import argparse
import sys

BACKBONES = ("RS-TW", "RS-FW")
GRID = (1.4, 1.8, 2.2, 2.4, 2.6)
DEFAULTS = {"RS-TW": 2.4, "RS-FW": 2.2}


def backbone_of(model):
    name = str(model or "")
    if "RS-TW" in name:
        return "RS-TW"
    if "RS-FW" in name:
        return "RS-FW"
    return ""


def default_cfg(model):
    """The lyrics guidance for this backbone. Unknown backbones keep RS-FW's 2.2."""
    return DEFAULTS.get(backbone_of(model) or "RS-FW", DEFAULTS["RS-FW"])


def sweep(backbone, lyrics, style, synth, score, grid=GRID, seeds=(1000,)):
    """One row per guidance and seed: backbone, cfg_lyrics, lm_seed, take, wer."""
    if backbone not in BACKBONES:
        raise ValueError("backbone is RS-TW or RS-FW")
    if not str(lyrics or "").strip():
        raise ValueError("the sweep needs lyrics")
    rows = []
    for cfg in grid:
        for seed in seeds:
            req = {"style": style, "lyrics": lyrics, "cfg_lyrics": float(cfg), "lm_seed": int(seed), "backbone": backbone}
            made = synth(req) or {}
            take = made.get("take")
            rows.append({"backbone": backbone, "cfg_lyrics": float(cfg), "lm_seed": int(seed),
                         "take": take, "wer": float(score(take, lyrics))})
    return rows


def format_rows(rows):
    lines = ["backbone  cfg_lyrics  lm_seed  wer     take"]
    for r in rows:
        lines.append(f"{r['backbone']:<9} {r['cfg_lyrics']:<11} {r['lm_seed']:<8} {r['wer']:<7} {r.get('take') or ''}")
    return "\n".join(lines)


def report():
    lines = ["cfg_lyrics defaults (chosen, under 2.6):"]
    for name in BACKBONES:
        lines.append(f"  {name}  {DEFAULTS[name]}")
    lines.append("grid: " + " ".join(str(c) for c in GRID))
    lines.append("a sweep reports word error per cell; pass synth and score to sweep(). This command makes nothing.")
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--report", action="store_true", help="print the chosen defaults and the grid")
    p.add_argument("--backbone", choices=BACKBONES)
    args = p.parse_args(argv)
    print(report())
    if args.backbone and not args.report:
        print("a measured sweep is sweep() with a synth and a scorer; this command does not load a model", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
