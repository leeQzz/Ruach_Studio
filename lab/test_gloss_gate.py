"""The lyrics gate: after /synth, gloss_core's word error. Over the threshold, a new lm_seed and another
take, up to N attempts, the lowest kept, each attempt on the job. The scorer is a fake. No torch and no
model are imported. python3 lab/test_gloss_gate.py"""
import json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import api  # noqa: E402
import gloss_core  # noqa: E402
import gloss_gate  # noqa: E402

bad = []


def check(what, ok, got=""):
    print(("  ok    " if ok else "  FAIL  ") + what + ("" if ok else "  (" + str(got) + ")"))
    if not ok:
        bad.append(what)


check("identical words: WER 0", gloss_core.wer("the cat sat", "The cat sat.") == 0.0)
check("one word swapped: 1/3", gloss_core.wer("the cat sat", "the dog sat") == round(1 / 3, 4), gloss_core.wer("the cat sat", "the dog sat"))
check("empty reference, nothing heard: 0; a word heard: 1", gloss_core.wer("", "") == 0.0 and gloss_core.wer("", "hello") == 1.0)
measured = gloss_core.measure({"segments": [{"start": 0, "end": 1, "text": "the cat sat"}]}, "the cat sat")
check("measure() carries wer, and a perfect hearing is 0", measured["wer"] == 0.0, measured.get("wer"))

off = gloss_gate.settings({})
check("the gate is off unless asked", off["gloss_gate"] is False and off["gloss_threshold"] == 0.35 and off["gloss_attempts"] == 3, off)
try:
    gloss_gate.settings({"gloss_threshold": 2})
    check("a threshold past 1 is refused", False)
except ValueError:
    check("a threshold past 1 is refused", True)
try:
    gloss_gate.settings({"gloss_attempts": 0})
    check("zero attempts is refused", False)
except ValueError:
    check("zero attempts is refused", True)


def drive(wers, threshold=0.35, attempts=3, seed=7, cancel_after=None):
    calls = []

    def synth(req):
        n = len(calls) + 1
        calls.append(dict(req))
        if cancel_after and n > cancel_after:
            return {"id": f"e{n}", "status": "cancelled", "takes": [], "lm_seed": req["lm_seed"]}
        return {"id": f"e{n}", "status": "done", "takes": [f"take-{n}"], "lm_seed": req["lm_seed"]}

    plan = list(wers)

    def score(take, lyrics):
        return {"wer": plan.pop(0)}

    rng = __import__("random").Random(seed)
    out = gloss_gate.run({"style": "folk", "lyrics": "one two", "lm_seed": 1000}, synth, score,
                         threshold=threshold, attempts=attempts, rng=rng,
                         cancel=(lambda: len(calls) >= cancel_after) if cancel_after else None)
    return out, calls


out, calls = drive([0.5, 0.2])
check("over, then under: two takes, the second kept", len(calls) == 2 and out["gloss_kept"] == "take-2" and out["takes"] == ["take-2"] and out["gloss_wer"] == 0.2, out)
check("each attempt is on the job, with its seed and its error",
      [a["n"] for a in out["gloss_attempts"]] == [1, 2] and [a["wer"] for a in out["gloss_attempts"]] == [0.5, 0.2]
      and out["gloss_attempts"][0]["lm_seed"] == 1000 and out["gloss_attempts"][1]["lm_seed"] != 1000, out["gloss_attempts"])
check("the music seed changed", calls[0]["lm_seed"] != calls[1]["lm_seed"], [c["lm_seed"] for c in calls])

out, calls = drive([0.1, 0.0])
check("under the line on the first take: no second song", len(calls) == 1 and out["gloss_kept"] == "take-1", len(calls))

out, calls = drive([0.9, 0.4, 0.5], attempts=3)
check("none under the line: three attempts, the lowest kept", len(calls) == 3 and out["gloss_kept"] == "take-2" and out["gloss_wer"] == 0.4, (out.get("gloss_kept"), out.get("gloss_wer")))

out, calls = drive([0.5, 0.5], attempts=2)
check("a tie keeps the earlier take", out["gloss_kept"] == "take-1", out.get("gloss_kept"))

out, calls = drive([0.9, 0.1], cancel_after=1)
check("cancelled after the first attempt: nothing further is kept as the winner", out["status"] == "cancelled" and len(calls) == 1, (out.get("status"), len(calls)))


# The API: a fake engine and a fake scorer. torch is not loaded.
gloss_gate.POLL = 0
engine_calls = []
jobs = {}


def fake_engine(path, body=None, timeout=60):
    if path == "/synth":
        engine_calls.append(dict(body))
        jid = "e" + str(len(engine_calls))
        jobs[jid] = {"status": "done", "takes": ["lib-" + jid], "lm_seed": body.get("lm_seed", 1000)}
        return {"id": jid}
    if "cancel=1" in path:
        return {"status": "cancelled"}
    jid = path.split("id=")[1].split("&")[0]
    return dict(jobs[jid])


scores = [0.62, 0.11]


def fake_score(take, lyrics):
    return {"wer": scores.pop(0), "lines": 1}


real_engine, real_score = api._engine, api._gloss_score
api._engine = fake_engine
api.gloss_score(fake_score)
ctx = {"collection": None, "outputs": None, "config_dir": lambda: None}
try:
    code, body = api.handle("POST", "/api/v1/songs", {}, json.dumps({"style": "folk", "lyrics": "one two", "gloss_gate": False}).encode(), {}, ctx)
    plain = len(engine_calls)
    check("gate off: one /synth, the engine's own job", code == 202 and plain == 1 and not str(body["job"]).startswith("gg-"), (code, body))
    code, body = api.handle("POST", "/api/v1/songs", {}, json.dumps({
        "style": "folk", "lyrics": "one two", "gloss_gate": True, "gloss_threshold": 0.35, "gloss_attempts": 3, "music_seed": 4242
    }).encode(), {}, ctx)
    check("gate on: a gloss-gate job, not the engine's first id", code == 202 and str(body["job"]).startswith("gg-") and body.get("gloss_gate") is True, body)
    jid = body["job"]
    st = {}
    for _ in range(50):
        st = gloss_gate.job(jid) or {}
        if st.get("status") in ("done", "failed", "cancelled"):
            break
        time.sleep(0.02)
    check("the job records both attempts and keeps the lower error",
          st.get("status") == "done" and [a.get("wer") for a in st.get("gloss_attempts") or []] == [0.62, 0.11]
          and st.get("takes") == ["lib-e3"] and st.get("gloss_wer") == 0.11, st)
    code, polled = api.handle("GET", f"/api/v1/jobs/{jid}", {}, None, {}, ctx)
    check("GET /jobs returns the same record, with a take url", code == 200 and polled.get("gloss_attempts") and polled.get("urls"), (code, polled.get("urls")))
    code, err = api.handle("POST", "/api/v1/songs", {}, json.dumps({"style": "folk", "lyrics": "x", "gloss_gate": True, "gloss_threshold": 4}).encode(), {}, ctx)
    check("a bad threshold is 400 and does not start a song", code == 400 and "gloss_threshold" in err.get("error", ""), (code, err))
    before = len(engine_calls)
    code, err = api.handle("POST", "/api/v1/songs", {}, json.dumps({"style": "folk", "instrumental": True, "gloss_gate": True}).encode(), {}, ctx)
    check("an instrumental is refused by the gate", code == 400 and len(engine_calls) == before, (code, err))
finally:
    api._engine = real_engine
    api._gloss_score = real_score

print("FAIL " + str(len(bad)) if bad else "ok")
sys.exit(1 if bad else 0)
