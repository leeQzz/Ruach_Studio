"""Ruach Studio's own trainer (HERESY 1083): the caption and the AR's prompt.

A caption is AI-Toolkit's YuE2 form: the style, then a "[Lyrics]" line and the lyrics. The AR sees
    [EOD] "INSTRUCTION\n[Tags]\nSTYLE\n[Lyrics]\nLYRICS\n" [ABC_START]
and then, in the same order as the engine's yue2_build_prompt_ids (build/src/prompt.h):
    off:                  [ABC_END, MUSIC_START]
    full / melody:        the score's token ids, then [ABC_END, MUSIC_START]
The score is the ABC inference would be handed: one kept on the track (dataset.json "scores",
"score" or "abc"), or else the sheet the engine's transcriber wrote beside the latents.
parse_caption() and the caption form are AI-Toolkit's (MIT, Ostris):
extensions_built_in/audio_models/yue2/yue2_model.py and src/tokenizer.py; the BPE is the one the
Comfy-Org checkpoint carries (text_encoders.yue2_tokenizer_json). The ids below are prompt.h's.
"""
import re
import unicodedata
from typing import List, Optional

_SECTION = re.compile(r"^\s*\[(Tags|Lyrics|Duration)\]\s*$", re.IGNORECASE | re.MULTILINE)
_SONG_SECTION = re.compile(r"^\s*\[[^\]]+\]\s*$")

# Special token ids, frozen with build/src/prompt.h (YUE2_EOD, YUE2_ABC_*, YUE2_MUSIC_*)
EOD = 151643
ABC_START, ABC_END = 151847, 151848
MUSIC_START, MUSIC_END = 151851, 151852

# yue2_instruction(), one string a mode, including the spaces the engine bakes in
INSTRUCTIONS = {
    "off": "Generate music with codec tokens from the given conditions.",
    "melody": "Generate a melody-only ABC transcription without chord symbols, then generate music with codec tokens from the given conditions.",
    "full": "Generate a chord-annotated ABC transcription, then generate music with codec tokens from the given conditions.",
}

# Sheet files beside a set's latents (lab/trainer/cache.py). One formula, so a count of missing scores
# names the same files the transcriber writes.
SHEET_SUFFIX = {"sheetsage2": "", "sheetsage2-bf16": ".bf16", "refined": ".refined"}
_TRANSCRIBER_ERROR = "transcriber {0!r}: sheetsage2-bf16 (Heretical), refined (Refined) or sheetsage2 (Standard, the sheets before 2.0.7)"


def _normalize_section(line: str) -> str:
    """[BRIDGE] -> [Bridge], [pre-chorus] -> [Pre-Chorus]: YuE2's section tags are Title case."""
    if not _SONG_SECTION.match(line):
        return line
    lead, body, trail = line[: line.index("[") + 1], line[line.index("[") + 1: line.rindex("]")], line[line.rindex("]"):]
    body = " ".join(w.capitalize() if w.isupper() and len(w) > 1 else w for w in body.split(" "))
    body = body[:1].upper() + body[1:]
    body = re.sub(r"-([a-z])", lambda m: "-" + m.group(1).upper(), body)
    return lead + body + trail


def parse_caption(text: str) -> dict:
    """Caption -> {style, lyrics}: the style text, then [Lyrics] and the lyrics; without a [Lyrics] line
    the first song section ([Intro], [Verse]…) starts them."""
    sections, current = {"tags": []}, "tags"
    for line in str(text or "").splitlines():
        m = _SECTION.match(line)
        if m:
            current = m.group(1).lower()
            sections.setdefault(current, [])
            continue
        if current == "tags" and "lyrics" not in sections and _SONG_SECTION.match(line):
            current = "lyrics"
            sections["lyrics"] = []
        sections.setdefault(current, []).append(_normalize_section(line) if current == "lyrics" else line)
    return {"style": "\n".join(sections["tags"]).strip(), "lyrics": "\n".join(sections.get("lyrics", [])).strip()}


def request_text(cot: str, style: str, lyrics: str) -> str:
    """yue2_request_text: the instruction, then the tags and the lyrics, each exactly as the engine joins them."""
    if cot not in INSTRUCTIONS:
        raise ValueError(f"cot {cot!r}: off, full or melody")
    return f"{INSTRUCTIONS[cot]}\n[Tags]\n{style}\n[Lyrics]\n{lyrics}\n"


def build_prompt_ids(encode, cot: str, style: str, lyrics: str, abc_ids: Optional[List[int]] = None) -> List[int]:
    """The conditional token stream, the same layout as yue2_build_prompt_ids.

    encode(text) -> ids. The engine's bpe_encode applies NFC first; TextTokenizer.encode does too.
    off ignores a score and closes an empty slot: ABC_START, ABC_END, MUSIC_START.
    full and melody with abc_ids None stop at ABC_START, the prefix score generation starts from.
    A score's ids sit between ABC_START and ABC_END and do not include ABC_END: inference drops that
    stop token from the generated ids and the prompt builder appends it.
    """
    ids = [EOD] + list(encode(request_text(cot, style, lyrics))) + [ABC_START]
    if cot == "off":
        return ids + [ABC_END, MUSIC_START]
    if abc_ids is None:
        return ids
    return ids + list(abc_ids) + [ABC_END, MUSIC_START]


def _blank(text) -> bool:
    return not isinstance(text, str) or not text.strip()


def score_text(track, cot: str, sheet: Optional[str] = None):
    """The ABC a full or melody prompt is built from, and where it came from.

    A score kept on the track wins: dataset.json "scores"[cot], else "score", else "abc". That is the
    text inference would be handed. Otherwise the transcribed sheet. ("", off) never carries a score:
    Direct closes an empty slot. Returns (text or None, "metadata" | "transcribed" | "").
    """
    if cot not in ("full", "melody"):
        return None, ""
    if isinstance(track, dict):
        scores = track.get("scores")
        if isinstance(scores, dict) and not _blank(scores.get(cot)):
            return scores.get(cot), "metadata"
        for key in ("score", "abc"):
            if not _blank(track.get(key)):
                return track.get(key), "metadata"
    if not _blank(sheet):
        return sheet, "transcribed"
    return None, ""


def sheet_suffix(transcriber: str) -> str:
    if transcriber not in SHEET_SUFFIX:
        raise ValueError(_TRANSCRIBER_ERROR.format(transcriber))
    return SHEET_SUFFIX[transcriber]


def sheet_filename(stem: str, cot: str, transcriber: str = "sheetsage2") -> str:
    """The cache file of one track's sheet: NNN_ruach.{full|melody}[.bf16|.refined].abc."""
    if cot not in ("full", "melody"):
        raise ValueError(f"cot {cot!r}: full or melody")
    return f"{stem}_ruach.{cot}{sheet_suffix(transcriber)}.abc"


def stem_sheet_filename(stem: str, variant: str, cot: str, transcriber: str = "sheetsage2") -> str:
    if cot not in ("full", "melody"):
        raise ValueError(f"cot {cot!r}: full or melody")
    return f"{stem}_ruach.{variant}.{cot}{sheet_suffix(transcriber)}.abc"


def score_coverage(tracks, cot: str, sheets, fails) -> dict:
    """How a prepared set stands for one mode. sheets and fails are stems.

    metadata: a score kept on the track (used even when a sheet exists too).
    transcribed: a sheet file and no metadata score.
    refused: the transcriber already wrote .fail, and there is still no score.
    missing: nothing yet; the run transcribes these, or refuses to start when it cannot.
    """
    if cot not in ("full", "melody"):
        raise ValueError(f"cot {cot!r}: full or melody")
    sheets, fails = set(sheets), set(fails)
    meta = transcribed = 0
    missing, refused = [], []
    for t in tracks or []:
        stem = str((t or {}).get("stem"))
        if score_text(t, cot, None)[1] == "metadata":
            meta += 1
        elif stem in sheets:
            transcribed += 1
        elif stem in fails:
            refused.append(stem)
        else:
            missing.append(stem)
    return {"cot": cot, "tracks": len(tracks or []), "metadata": meta, "transcribed": transcribed,
            "missing": missing, "refused": refused}


def direct_only_reason(cot: str) -> str:
    return f"score mode {cot}: every track's score was refused, so the run would teach Direct only"


def none_covered_reason(cot: str, n_tracks: int, n_with_score: int):
    """After the sheets are written: None, or why a full or melody run must stop.

    Some scores still teach the mode, and the tracks without one stay Direct. None at all would teach Direct only.
    """
    if cot not in ("full", "melody") or n_tracks <= 0 or n_with_score > 0:
        return None
    return direct_only_reason(cot)


def stem_scores_missing(tracks, cot: str, names, transcriber: str = "sheetsage2") -> list:
    """Tracks whose voice or band still has no sheet and no refusal.

    A score kept on the mix does not stand in for either stem. names are the cache's filenames.
    """
    if cot not in ("full", "melody"):
        raise ValueError(f"cot {cot!r}: full or melody")
    names = set(names or ())
    missing = []
    for t in tracks or []:
        stem = str((t or {}).get("stem"))
        for variant in ("vocals", "music"):
            fn = stem_sheet_filename(stem, variant, cot, transcriber)
            if fn not in names and fn + ".fail" not in names:
                missing.append(stem)
                break
    return missing


def _needs_transcriber(cot: str, stems, what: str = "without one") -> str:
    shown = ", ".join(stems[:8])
    extra = f" and {len(stems) - 8} more" if len(stems) > 8 else ""
    return (f"score mode {cot} needs a score for {len(stems)} track(s) {what} ({shown}{extra}) "
            f"or the engine's transcriber (build/build/yue-transcribe and models/misc/SheetSage2-*.gguf)")


def scores_block_reason(report: dict, transcriber_ready: bool, stem_missing=None):
    """None when a full or melody run may start. Else ("transcriber"|"scores", why).

    Tracks with a metadata score or a transcribed sheet are covered. Tracks still missing a score
    need the engine's transcriber. A set whose every track was refused would teach Direct only.
    stem_missing: with separation, tracks whose voice or band sheet is still to be written. The mix's
    score does not cover them, and making those sheets needs the transcriber too.
    """
    cot = report.get("cot") or "full"
    covered = int(report.get("metadata") or 0) + int(report.get("transcribed") or 0)
    missing = list(report.get("missing") or [])
    stems = list(stem_missing or [])
    if int(report.get("tracks") or 0) and covered == 0 and not missing:
        return ("scores", direct_only_reason(cot))
    if missing and not transcriber_ready:
        return ("transcriber", _needs_transcriber(cot, missing))
    if stems and not transcriber_ready:
        return ("transcriber", _needs_transcriber(cot, stems, "whose voice or band has none"))
    return None


def closed_prompt_loss_mask(ids: List[int], cot: str) -> List[bool]:
    """One bool per id of a closed prompt (through MUSIC_START; the codec tokens are not in it yet).

    False through ABC_START: that prefix is given at inference.
    full / melody: True on the score and on ABC_END (the ABC phase writes them and stops at ABC_END);
    False on MUSIC_START (the pipeline inserts it; neither phase samples it).
    off: ABC_END and MUSIC_START stay supervised, as Direct has trained. The codec tokens the caller
    appends after this prompt are supervised in every mode; MUSIC_END, when the window is the whole song, too.
    """
    if cot not in INSTRUCTIONS:
        raise ValueError(f"cot {cot!r}: off, full or melody")
    if ABC_START not in ids:
        raise ValueError("a prompt carries ABC_START")
    at = ids.index(ABC_START)
    if cot == "off":
        return [False] * (at + 1) + [True] * (len(ids) - at - 1)
    if len(ids) < at + 3 or ids[-1] != MUSIC_START or ids[-2] != ABC_END:
        raise ValueError("a full or melody prompt closes with ABC_END, MUSIC_START")
    n_score = len(ids) - at - 3
    return [False] * (at + 1) + [True] * n_score + [True, False]


def target_loss_mask(n_score: int, n_music: int, score_step: bool, close_song: bool) -> List[bool]:
    """Mask over the targets that follow the prefix head (the head ends at ABC_START and is not a target).

    Targets are [score] * n_score + ABC_END + MUSIC_START + [codec] * n_music + optional MUSIC_END.
    A score step masks MUSIC_START only. A Direct step supervises every target, and carries no score.
    """
    if n_score < 0 or n_music < 0:
        raise ValueError("score and music lengths are counts")
    if score_step:
        mask = [True] * n_score + [True, False] + [True] * n_music
    else:
        if n_score:
            raise ValueError("a Direct step has no score tokens")
        mask = [True, True] + [True] * n_music
    if close_song:
        mask.append(True)
    return mask


def check_score_ids(ids: List[int], what: str = "the score") -> List[int]:
    """The ABC phase samples ordinary text, below EOD, and stops by emitting ABC_END itself."""
    if any(not 0 <= t < EOD for t in ids):
        raise ValueError(f"{what} leaves the ordinary text vocabulary")
    return list(ids)


class TextTokenizer:
    def __init__(self, tokenizer_json: bytes):
        from tokenizers import Tokenizer
        self.tokenizer = Tokenizer.from_str(tokenizer_json.decode("utf-8"))

    def encode(self, text: str) -> List[int]:
        return self.tokenizer.encode(unicodedata.normalize("NFC", text)).ids

    def prefix_head_ids(self, style: str, lyrics: str, cot: str = "off") -> List[int]:
        """The prompt through ABC_START. off's closed tail (ABC_END, MUSIC_START) is not part of the head."""
        ids = build_prompt_ids(self.encode, cot, style, lyrics, None)
        if cot == "off":
            return ids[:-2]
        return ids

    def prefix_ids(self, caption: str, cot: str = "off", score: Optional[str] = None,
                   abc_ids: Optional[List[int]] = None) -> List[int]:
        """Everything before the first music token.

        off closes an empty score slot and ignores a score (inference drops one given to Direct).
        full and melody need the score's text or its ids.
        """
        if cot not in INSTRUCTIONS:
            raise ValueError(f"cot {cot!r}: off, full or melody")
        c = parse_caption(caption)
        if cot == "off":
            return build_prompt_ids(self.encode, cot, c["style"], c["lyrics"], None)
        if abc_ids is None:
            if _blank(score):
                raise ValueError("full and melody need a score: a transcribed sheet or a score kept on the track")
            abc_ids = check_score_ids(self.encode(score))
        else:
            abc_ids = check_score_ids(list(abc_ids))
        return build_prompt_ids(self.encode, cot, c["style"], c["lyrics"], abc_ids)


def tokenizer_from_checkpoint(path: str) -> TextTokenizer:
    from safetensors import safe_open
    with safe_open(path, framework="pt") as f:
        blob = f.get_tensor("text_encoders.yue2_tokenizer_json")
    return TextTokenizer(bytes(blob.tolist()))
