"""The quality loop: inspect_job's findings, then only the flagged sections are sung again, a few times at most.
A fault of the whole take (the VAE's buzz, clipping) names Debuzz or Remaster and is not sung again.
inspect_fn and redo_fn are fakes. No torch and no model are imported. python3 lab/test_quality_loop.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import quality_loop  # noqa: E402

bad = 0


def check(name, ok, detail=None):
    global bad
    print(("  ok    " if ok else "  FAIL  ") + name + ("" if ok or detail is None else "  " + repr(detail)))
    bad += not ok


LYRICS = "[Verse]\none two three\n\n[Chorus]\nfour five six\n"
CLEAN = {"seconds": 60, "framebuzz": {"mean_db": 1.0}, "clipping": {"runs": 0}, "dropouts": [], "clicks": [], "tones": []}


def drive(findings_for, lyrics=LYRICS, iterations=2, seconds=60):
    calls = []

    def inspect(name):
        return findings_for(name)

    def redo(name, parts, lyrics_):
        calls.append({"name": name, "parts": parts, "lyrics": lyrics_})
        return {"takes": [name + "+" + "+".join(p["section"] for p in parts)]}

    out = quality_loop.run("take", lyrics, inspect, redo, iterations=iterations, seconds=seconds)
    return out, calls


check("two section tags share the take",
      quality_loop.section_spans(LYRICS, 60) == [
          {"section": "Verse", "a": 0.0, "b": 30.0}, {"section": "Chorus", "a": 30.0, "b": 60.0}])
check("no tag is the whole take", quality_loop.section_spans("just words", 40)[0]["section"] == "the song")

buzz = dict(CLEAN, framebuzz={"mean_db": 11.2})
out, calls = drive(lambda name: buzz)
check("frame buzz suggests Debuzz and sings nothing again", out["suggest"] == ["debuzz"] and calls == [] and out["takes"] == [], out)

clip = dict(CLEAN, clipping={"runs": 4}, loudness={"true_peak": 1.2})
out, calls = drive(lambda name: clip)
check("clipping suggests Remaster and sings nothing again", out["suggest"] == ["remaster"] and calls == [], out)

hum = dict(CLEAN, tones=[{"hz": 50, "mains": "50 Hz"}])
out, calls = drive(lambda name: hum)
check("mains hum suggests Remaster", "remaster" in out["suggest"] and calls == [])

drop = dict(CLEAN, dropouts=[{"at": 12, "ms": 40, "depth_db": 20}])
out, calls = drive(lambda name: drop if name == "take" else CLEAN, iterations=2)
check("a dropout sings the section it falls in, once", len(calls) == 1 and calls[0]["parts"] == [{"a": 0.0, "b": 30.0, "section": "Verse"}], calls)
check("the child is the take that is kept", out["take"] == "take+Verse" and out["iterations"] == 1)

both = dict(CLEAN, framebuzz={"mean_db": 9}, dropouts=[{"at": 40, "ms": 30, "depth_db": 12}])
out, calls = drive(lambda name: both if name == "take" else CLEAN)
check("buzz and a dropout: Debuzz is named, and only the flagged section is sung",
      out["suggest"] == ["debuzz"] and len(calls) == 1 and calls[0]["parts"][0]["section"] == "Chorus", (out["suggest"], calls))

clicks = dict(CLEAN, clicks=[{"at": 31, "strength": 8}, {"at": 32, "strength": 9}, {"at": 33, "strength": 7}])
out, calls = drive(lambda name: clicks if name == "take" else CLEAN)
check("three clicks in one section sing it again", len(calls) == 1 and calls[0]["parts"][0]["section"] == "Chorus")
few = dict(CLEAN, clicks=[{"at": 31, "strength": 8}, {"at": 32, "strength": 9}])
out, calls = drive(lambda name: few)
check("two clicks do not", calls == [])

gloss = {"segments": [{"start": 10, "end": 14, "score": 0.2, "text": "no"}]}
plan = quality_loop.classify(CLEAN, LYRICS, 60, gloss)
check("a gloss line under the line flags its section", plan["redo"] and plan["redo"][0]["section"] == "Verse" and "the words" in plan["redo"][0]["why"])

def always(name):
    return dict(CLEAN, dropouts=[{"at": 12, "ms": 20, "depth_db": 10}])

out, calls = drive(always, iterations=2)
check("still flagged: it stops after the bound, two sections sung", len(calls) == 2 and out["iterations"] == 2 and out["take"] == "take+Verse+Verse", out["take"])
try:
    quality_loop.iterations_of(9)
    check("more than four iterations is refused", False)
except ValueError:
    check("more than four iterations is refused", True)

quality_loop.POLL = 0
seen = {}

def inspect(name):
    return drop if name == "song" else CLEAN

def redo(name, parts, lyrics):
    return {"takes": ["song-child"], "section": "sec-1"}

code, body = quality_loop.begin({"name": "song", "iterations": 2}, lambda name: LYRICS, inspect, redo)
check("the lab start answers 202 and a job id", code == 202 and str(body["id"]).startswith("ql-"), body)
jid = body["id"]
for _ in range(50):
    st = quality_loop.job(jid) or {}
    if st.get("status") in ("done", "failed", "cancelled"):
        break
    time_sleep = __import__("time").sleep
    time_sleep(0.01)
check("the job records the pass and the section job", st.get("status") == "done" and st.get("takes") == ["song-child"]
      and st["passes"][0].get("section") == "sec-1", st)
code, err = quality_loop.begin({"name": ""}, lambda name: LYRICS, inspect, redo)
check("no name is 400", code == 400, err)
code, err = quality_loop.begin({"name": "song", "iterations": 0}, lambda name: LYRICS, inspect, redo)
check("zero iterations is 400 and starts nothing", code == 400, err)
code, err = quality_loop.begin({"name": "missing"}, lambda name: (_ for _ in ()).throw(FileNotFoundError(name)), inspect, redo)
check("a missing take is 404", code == 404, err)

print("ok" if not bad else f"{bad} failed")
sys.exit(1 if bad else 0)
