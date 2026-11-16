"""After a take is inspected, sing again only the sections the findings flag, and only a few times.
A fault of the whole take (the VAE's frame buzz, clipping, mains hum) is not sung again: Debuzz or Remaster is named instead.

    classify(findings, lyrics, seconds, gloss)   which sections, and which tools, the findings ask for
    run(name, lyrics, inspect_fn, redo_fn, …)    bounded: inspect, redo the flagged sections, inspect the child
    begin(data, load, inspect_fn, redo_fn, …)    the lab's POST /quality/loop
"""
import threading
import time

BUZZ_DB = 8.0                 # framebuzz.mean_db at or over this: Debuzz, not a section
CLICKS = 3                    # that many clicks in one section before it is sung again
GLOSS_LINE = 0.5              # a heard line under this, when a gloss is already on the take, flags its section
DEFAULT_ITERS = 2
MAX_ITERS = 4
POLL = 1.0
LIVE = {}
LOCK = threading.Lock()


def section_spans(lyrics, seconds):
    """One span per sung section tag, the take shared evenly. No tag: the whole take."""
    tags = []
    for line in (lyrics or "").splitlines():
        s = line.strip()
        if len(s) >= 3 and s.startswith("[") and s.endswith("]") and not s.startswith("[["):
            name = s[1:-1].strip()
            if name and (not tags or tags[-1] != name):
                tags.append(name)
    if not tags:
        tags = ["the song"]
    seconds = max(float(seconds or 0), 0.01)
    step = seconds / len(tags)
    return [{"section": t, "a": round(i * step, 2), "b": round((i + 1) * step, 2)} for i, t in enumerate(tags)]


def _at(spans, t):
    t = float(t or 0)
    for s in spans:
        if s["a"] <= t < s["b"]:
            return s
    return spans[-1] if spans and t >= spans[-1]["a"] else None


def _flag(flagged, span, reason):
    if not span:
        return
    key = (span["section"], span["a"])
    row = flagged.get(key)
    if not row:
        row = {"section": span["section"], "a": span["a"], "b": span["b"], "why": []}
        flagged[key] = row
    if reason not in row["why"]:
        row["why"].append(reason)


def classify(findings, lyrics, seconds=None, gloss=None):
    """Local faults (a dropout, a run of clicks, a line the gloss already marked) name a section.
    A fault of the whole take names Debuzz or Remaster and does not name a section."""
    findings = findings or {}
    if seconds is None:
        seconds = findings.get("seconds")
    spans = section_spans(lyrics, seconds)
    suggest, why, flagged = [], [], {}
    buzz = (findings.get("framebuzz") or {}).get("mean_db")
    if buzz is not None and float(buzz) >= BUZZ_DB:
        suggest.append("debuzz")
        why.append("the VAE's frame buzz")
    clip = findings.get("clipping") or {}
    peak = (findings.get("loudness") or {}).get("true_peak")
    if int(clip.get("runs") or 0) >= 1 or (peak is not None and float(peak) > 0):
        suggest.append("remaster")
        why.append("clipping")
    if any(t.get("mains") for t in (findings.get("tones") or []) if isinstance(t, dict)):
        if "remaster" not in suggest:
            suggest.append("remaster")
        why.append("mains hum")
    for drop in findings.get("dropouts") or []:
        _flag(flagged, _at(spans, (drop or {}).get("at")), "a dropout")
    clicks = {}
    for click in findings.get("clicks") or []:
        span = _at(spans, (click or {}).get("at"))
        if not span:
            continue
        key = (span["section"], span["a"])
        clicks[key] = clicks.get(key, 0) + 1
        if clicks[key] == CLICKS:
            _flag(flagged, span, "clicks")
    for seg in (gloss or {}).get("segments") or []:
        score = seg.get("score") if isinstance(seg, dict) else None
        if score is not None and float(score) < GLOSS_LINE:
            _flag(flagged, _at(spans, seg.get("start", seg.get("at"))), "the words")
    redo = sorted(flagged.values(), key=lambda p: p["a"])
    return {"redo": redo, "suggest": suggest, "why": why}


def iterations_of(n):
    if n is None or n == "":
        n = DEFAULT_ITERS
    try:
        n = int(n)
    except (TypeError, ValueError):
        raise ValueError("iterations must be a whole number")
    if not 1 <= n <= MAX_ITERS:
        raise ValueError(f"iterations must be from 1 to {MAX_ITERS}")
    return n


def run(name, lyrics, inspect_fn, redo_fn, gloss_fn=None, iterations=DEFAULT_ITERS, seconds=None, cancel=None):
    """At most `iterations` times: inspect, and if a section is flagged, sing it again. A clean take, or a take
    whose only faults are of the whole song, stops without a section being sung again."""
    iterations = iterations_of(iterations)
    current, passes, suggest, redone = name, [], [], 0
    for i in range(iterations):
        if cancel and cancel():
            return _out("cancelled", name, current, suggest, passes, redone)
        findings = inspect_fn(current) or {}
        gloss = gloss_fn(current) if gloss_fn else None
        plan = classify(findings, lyrics, seconds if seconds is not None else findings.get("seconds"), gloss)
        for name_ in plan["suggest"]:
            if name_ not in suggest:
                suggest.append(name_)
        row = {"n": i + 1, "take": current, "redo": plan["redo"], "suggest": list(suggest), "why": plan["why"]}
        passes.append(row)
        if not plan["redo"]:
            break
        made = redo_fn(current, [{"a": p["a"], "b": p["b"], "section": p["section"]} for p in plan["redo"]], lyrics) or {}
        if made.get("section"):
            row["section"] = made["section"]
        child = (made.get("takes") or [None])[0]
        if not child:
            row["error"] = made.get("error") or "the section was not remade"
            return _out("failed" if current == name else "done", name, current, suggest, passes, redone)
        redone += 1
        current = child
    return _out("done", name, current, suggest, passes, redone)


def _out(status, name, current, suggest, passes, redone):
    return {"status": status, "name": name, "take": current, "takes": [] if current == name else [current],
            "suggest": suggest, "passes": passes, "iterations": redone}


def job(jid):
    with LOCK:
        st = LIVE.get(str(jid))
        return {k: v for k, v in st.items() if k != "cancel"} if st else None


def cancel(jid):
    with LOCK:
        st = LIVE.get(str(jid))
        if st and st.get("status") in ("queued", "running"):
            st["cancel"] = True
            st["status"] = "cancelled"


def start(name, lyrics, inspect_fn, redo_fn, gloss_fn=None, iterations=DEFAULT_ITERS, seconds=None):
    jid = "ql-" + time.strftime("%Y%m%d-%H%M%S-") + f"{time.time_ns() % 10000:04d}"
    state = {"id": jid, "name": name, "status": "queued", "suggest": [], "passes": [], "take": name, "takes": [], "cancel": False}

    def work():
        try:
            with LOCK:
                if state.get("cancel"):
                    state["status"] = "cancelled"
                    return
                state["status"] = "running"
            done = run(name, lyrics, inspect_fn, redo_fn, gloss_fn, iterations, seconds, cancel=lambda: state.get("cancel"))
            with LOCK:
                if state.get("cancel") and done.get("status") != "cancelled":
                    done["status"] = "cancelled"
                for k, v in done.items():
                    state[k] = v
        except Exception as e:
            with LOCK:
                state["status"] = "failed"
                state["error"] = str(e)
        finally:
            threading.Timer(600, lambda: LIVE.pop(jid, None)).start()

    with LOCK:
        LIVE[jid] = state
    threading.Thread(target=work, daemon=True).start()
    return job(jid)


def begin(data, load, inspect_fn, redo_fn, gloss_fn=None):
    """The POST body. load(name) is the take's lyrics, or raises FileNotFoundError / ValueError."""
    data = data or {}
    name = str(data.get("name") or "").strip()
    if not name:
        return 400, {"error": "name the take"}
    try:
        n = iterations_of(data.get("iterations"))
    except ValueError as e:
        return 400, {"error": str(e)}
    try:
        lyrics = load(name)
    except FileNotFoundError:
        return 404, {"error": "no such take"}
    except ValueError as e:
        return 400, {"error": str(e)}
    if not str(lyrics or "").strip():
        return 400, {"error": "the take has no lyrics"}
    started = start(name, lyrics, inspect_fn, redo_fn, gloss_fn, n)
    return 202, {"id": started["id"]}


def section_redo(name, parts, lyrics, cancel=None):
    """The real redo: lab/section.py, one version, only stretches it can sing (inside the take, two seconds at least)."""
    import section
    fitted = []
    for p in parts:
        a, b = float(p["a"]), float(p["b"])
        if a <= 0:
            a = 0.05
        if b - a < 2:
            continue
        fitted.append({"a": round(a, 2), "b": round(b, 2), "section": p["section"]})
    if not fitted:
        return {"takes": [], "error": "the flagged stretch is too short to sing again"}
    try:
        started = section.start(name, fitted[0]["a"], fitted[0]["b"], lyrics, 1, fitted[0]["section"], fitted)
    except ValueError as e:
        return {"takes": [], "error": str(e)}
    while True:
        if cancel and cancel():
            return {"takes": [], "error": "cancelled", "section": started["id"]}
        st = section.job(started["id"])
        if st and st.get("status") in ("done", "failed"):
            takes = st.get("takes") or []
            if takes:
                return {"takes": takes, "section": started["id"]}
            bad = [v.get("error") for v in (st.get("versions") or []) if v.get("error")]
            return {"takes": [], "error": (bad[0] if bad else "the section was not remade"), "section": started["id"]}
        time.sleep(POLL)
