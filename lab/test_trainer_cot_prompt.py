"""Full and Melody prompts match the engine's yue2_build_prompt_ids (build/src/prompt.h), including where the
score comes from and which tokens the loss sees. python3 lab/test_trainer_cot_prompt.py"""
import json, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "trainer"))
sys.path.insert(1, str(Path(__file__).resolve().parent))
import prompt  # noqa: E402
import training  # noqa: E402

bad = []


def check(what, ok, got=""):
    print(("  ok    " if ok else "  FAIL  ") + what + ("" if ok else "  (" + str(got)[:300] + ")"))
    if not ok:
        bad.append(what)


# The instruction strings and the assembly, copied from build/src/prompt.h (yue2_instruction, yue2_request_text,
# yue2_build_prompt_ids). Not imported from the trainer, so a drift in either direction fails here.
ENGINE = {
    "off": "Generate music with codec tokens from the given conditions.",
    "melody": ("Generate a melody-only ABC transcription without chord symbols, then generate music with codec "
               "tokens from the given conditions."),
    "full": ("Generate a chord-annotated ABC transcription, then generate music with codec "
             "tokens from the given conditions."),
}
EOD, ABC_START, ABC_END, MUSIC_START = 151643, 151847, 151848, 151851


def inference_layout(encode, cot, style, lyrics, abc_ids):
    ids = [EOD]
    ids += encode(ENGINE[cot] + "\n[Tags]\n" + style + "\n[Lyrics]\n" + lyrics + "\n")
    ids.append(ABC_START)
    if cot == "off":
        ids += [ABC_END, MUSIC_START]
        return ids
    if abc_ids is None:
        return ids
    ids += list(abc_ids)
    ids += [ABC_END, MUSIC_START]
    return ids


def encode(text):
    """A stand-in for bpe_encode: one id per character, inside the ordinary text vocabulary."""
    return [(ord(ch) % 1000) + 1 for ch in text]


for cot in ("off", "full", "melody"):
    check(f"instruction {cot} is the engine's", prompt.INSTRUCTIONS[cot] == ENGINE[cot], prompt.INSTRUCTIONS[cot])

abc = encode("X:1\nK:C\nC2 D2 |\n")
for cot in ("off", "full", "melody"):
    for given in (None, abc):
        got = prompt.build_prompt_ids(encode, cot, "dark metal", "[Verse]\nhello", None if cot == "off" and given is None else given)
        # off must ignore a score even when one is passed, as inference drops it
        want = inference_layout(encode, cot, "dark metal", "[Verse]\nhello", None if cot == "off" else given)
        if cot == "off":
            got = prompt.build_prompt_ids(encode, cot, "dark metal", "[Verse]\nhello", given)
        check(f"layout {cot} abc={given is not None} matches inference", got == want, (got, want))

open_full = prompt.build_prompt_ids(encode, "full", "s", "l", None)
check("full with no score stops at ABC_START, the prefix the plan starts from", open_full[-1] == ABC_START and ABC_END not in open_full, open_full[-3:])
check("melody and full differ only by the instruction", 
      prompt.build_prompt_ids(encode, "melody", "s", "l", abc) != prompt.build_prompt_ids(encode, "full", "s", "l", abc))

closed = prompt.build_prompt_ids(encode, "full", "dark metal", "[Verse]\nhello", abc)
mask = prompt.closed_prompt_loss_mask(closed, "full")
at = closed.index(ABC_START)
check("the conditioning through ABC_START is masked", mask[:at + 1] == [False] * (at + 1) and closed[at] == ABC_START)
check("the score and ABC_END are supervised, MUSIC_START is not",
      mask[at + 1:] == [True] * len(abc) + [True, False] and closed[at + 1:at + 1 + len(abc)] == abc
      and closed[-2:] == [ABC_END, MUSIC_START], mask[at + 1:])
music, close_song = 4, True
targets = prompt.target_loss_mask(len(abc), music, True, close_song)
check("a score step's targets extend that mask with the codec and MUSIC_END",
      targets == mask[at + 1:] + [True] * music + [True], targets)
direct = prompt.target_loss_mask(0, music, False, close_song)
check("a Direct step supervises ABC_END, MUSIC_START and the music",
      direct == [True, True] + [True] * music + [True], direct)
try:
    prompt.target_loss_mask(3, 1, False, False)
    check("a Direct step refuses score tokens", False, "taken")
except ValueError:
    check("a Direct step refuses score tokens", True)


class Tok:
    def __init__(self):
        self.seen = []

    def encode(self, text):
        self.seen.append(text)
        return encode(text)


tok = Tok()
caption = "dark metal\n[Lyrics]\n[Verse]\nhello\n"
score = "X:1\nK:C\n"
ids = prompt.TextTokenizer.prefix_ids(tok, caption, "full", score=score)
want = inference_layout(encode, "full", "dark metal", "[Verse]\nhello", encode(score))
check("prefix_ids(full) is the inference prompt of the parsed caption and the score", ids == want, (ids, want))
check("the request text and the score are what gets encoded",
      set(tok.seen) == {ENGINE["full"] + "\n[Tags]\ndark metal\n[Lyrics]\n[Verse]\nhello\n", score}, tok.seen)

tok = Tok()
off = prompt.TextTokenizer.prefix_ids(tok, caption, "off", score="SHOULD NOT BE READ")
check("prefix_ids(off) closes an empty slot and ignores a score",
      off == inference_layout(encode, "off", "dark metal", "[Verse]\nhello", None) and len(tok.seen) == 1, tok.seen)
try:
    prompt.TextTokenizer.prefix_ids(Tok(), caption, "full")
    check("full without a score is refused", False, "taken")
except ValueError as e:
    check("full without a score is refused", "score" in str(e), e)
try:
    prompt.TextTokenizer.prefix_ids(Tok(), caption, "melody", abc_ids=[EOD])
    check("a score that leaves the text vocabulary is refused", False, "taken")
except ValueError as e:
    check("a score that leaves the text vocabulary is refused", "vocabulary" in str(e), e)

check("the full head equals build_prompt_ids with no score",
      prompt.TextTokenizer.prefix_head_ids(Tok(), "s", "l", "full") == prompt.build_prompt_ids(encode, "full", "s", "l", None))
check("the Direct head is the closed prompt without ABC_END and MUSIC_START",
      prompt.TextTokenizer.prefix_head_ids(Tok(), "s", "l", "off") + [ABC_END, MUSIC_START]
      == prompt.build_prompt_ids(encode, "off", "s", "l", None))

check("a mode-specific score wins", prompt.score_text({"scores": {"melody": "M:3/4\n", "full": "K:G\n"}}, "melody", "T:no\n") == ("M:3/4\n", "metadata"))
check("a plain score is metadata for either mode", prompt.score_text({"score": "X:1\n"}, "full", "T:sheet\n") == ("X:1\n", "metadata"))
check("abc is metadata too", prompt.score_text({"abc": "X:1\n"}, "full", None) == ("X:1\n", "metadata"))
check("otherwise the transcribed sheet", prompt.score_text({"stem": "001"}, "full", "T:sheet\n") == ("T:sheet\n", "transcribed"))
check("Direct carries no score", prompt.score_text({"score": "X:1\n"}, "off", "T:sheet\n") == (None, ""))
check("a blank score is not one", prompt.score_text({"score": "  \n"}, "melody", None) == (None, ""))

tracks = [{"stem": "001", "scores": {"full": "K:C\n"}}, {"stem": "002"}, {"stem": "003"}, {"stem": "004", "score": "X:1\n"}]
rep = prompt.score_coverage(tracks, "full", {"002"}, {"003"})
check("coverage: metadata, a sheet, a refusal, and one still missing",
      rep["metadata"] == 2 and rep["transcribed"] == 1 and rep["refused"] == ["003"] and rep["missing"] == [], (rep,))
# 001 and 004 are metadata; 002 has a sheet; 003 is refused. missing is empty. Good.
rep_m = prompt.score_coverage([{"stem": "009"}], "melody", set(), set())
check("a track with nothing is missing", rep_m["missing"] == ["009"] and rep_m["metadata"] == 0, rep_m)
check("missing scores block without a transcriber",
      (prompt.scores_block_reason(rep_m, False) or ("", ""))[0] == "transcriber")
check("missing scores do not block when the transcriber can make them", prompt.scores_block_reason(rep_m, True) is None)
refused = prompt.score_coverage([{"stem": "003"}], "full", set(), {"003"})
check("every track refused blocks, the run would teach Direct only",
      (prompt.scores_block_reason(refused, True) or ("", ""))[0] == "scores", prompt.scores_block_reason(refused, True))
check("a covered set does not block", prompt.scores_block_reason(rep, False) is None)

check("sheet names are the cache's",
      prompt.sheet_filename("001", "full", "sheetsage2") == "001_ruach.full.abc"
      and prompt.sheet_filename("001", "full", "sheetsage2-bf16") == "001_ruach.full.bf16.abc"
      and prompt.sheet_filename("001", "melody", "refined") == "001_ruach.melody.refined.abc"
      and prompt.stem_sheet_filename("001", "vocals", "full", "sheetsage2-bf16") == "001_ruach.vocals.full.bf16.abc")
try:
    prompt.sheet_filename("001", "full", "ss2")
    check("an unknown transcriber is refused", False, "taken")
except ValueError:
    check("an unknown transcriber is refused", True)

# A prepared set on disk: metadata, a transcribed sheet, a refusal, and a track with none.
root = Path(tempfile.mkdtemp())
set_dir = root / "prepared" / "demo"
cache = set_dir / "_ruach_cache"
cache.mkdir(parents=True)
(cache / "002_ruach.full.bf16.abc").write_text("T:transcribed\n", encoding="utf-8")
(cache / "003_ruach.full.bf16.abc.fail").write_text("no score\n", encoding="utf-8")
(set_dir / "dataset.json").write_text(json.dumps({"tracks": [
    {"stem": "001", "scores": {"full": "K:C\nC2|\n"}},
    {"stem": "002"},
    {"stem": "003"},
    {"stem": "004"},
]}), encoding="utf-8")
disk = training.score_report(set_dir, "full", "sheetsage2-bf16")
check("the set's report: one kept score, one sheet, one refusal, one missing",
      disk["metadata"] == 1 and disk["transcribed"] == 1 and disk["refused"] == ["003"] and disk["missing"] == ["004"], disk)
counts = training.score_counts(set_dir, json.loads((set_dir / "dataset.json").read_text())["tracks"], "sheetsage2-bf16")
check("the datasets answer counts them and does not list the stems",
      counts["full"] == {"metadata": 1, "transcribed": 1, "missing": 1, "refused": 1} and "004" not in json.dumps(counts["full"]), counts)

raw = root / "raw"
raw.mkdir()
(raw / "song.wav").write_bytes(b"")
(raw / "song.abc").write_text("X:1\nK:G\n", encoding="utf-8")
(raw / "other.wav").write_bytes(b"")
(raw / "other.melody.abc").write_text("M:4/4\n", encoding="utf-8")
check("a plain ABC beside the audio counts", training._score_beside(raw / "song.wav") and training._score_beside(raw / "other.wav"))
check("prepare keeps the file beside the audio",
      training._track_score_fields(raw / "song.wav", {}) == {"score": "X:1\nK:G\n"}, training._track_score_fields(raw / "song.wav", {}))
check("prepare keeps a mode file, and a score in the request wins",
      training._track_score_fields(raw / "other.wav", {"scores": {"full": "K:D\n"}})["scores"] == {"melody": "M:4/4\n", "full": "K:D\n"})

print("test_trainer_cot_prompt: " + ("all passed" if not bad else f"{len(bad)} FAILED"))
sys.exit(1 if bad else 0)
