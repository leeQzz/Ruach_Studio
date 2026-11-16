"""After /synth, score the take with gloss_core. A word error over the threshold
reseeds lm_seed and generates again, up to N attempts. The lowest error is kept.
Each attempt is written on the job. The gate is off unless a request asks for it.

    settings(body) -> {gloss_gate, gloss_threshold, gloss_attempts}
    run(req, synth, score, ...) -> the kept job, with gloss_attempts
    start(...) -> a job the page can poll; wait_synth talks to the engine
"""
import random, threading, time, urllib.parse

FIELDS = ("gloss_gate", "gloss_threshold", "gloss_attempts")
DEFAULT_THRESHOLD = 0.35
DEFAULT_ATTEMPTS = 3
MAX_ATTEMPTS = 8
POLL = 1.0

LIVE = {}
_ENGINE = {}
LOCK = threading.Lock()


def _seed(value):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def next_seed(used, rng):
    for _ in range(32):
        n = rng.randint(1000, 999999999)
        if n not in used:
            return n
    return rng.randint(1000, 999999999)


def settings(body):
    """The gate, its threshold and how many attempts. Off unless gloss_gate is true."""
    body = body or {}
    on = body.get("gloss_gate") in (True, 1, "1", "true", "on")
    raw_t = body.get("gloss_threshold")
    raw_n = body.get("gloss_attempts")
    try:
        threshold = float(DEFAULT_THRESHOLD if raw_t is None or raw_t == "" else raw_t)
    except (TypeError, ValueError):
        raise ValueError("gloss_threshold must be a number from 0 to 1")
    try:
        attempts = int(DEFAULT_ATTEMPTS if raw_n is None or raw_n == "" else raw_n)
    except (TypeError, ValueError):
        raise ValueError(f"gloss_attempts must be a whole number from 1 to {MAX_ATTEMPTS}")
    if not 0 <= threshold <= 1:
        raise ValueError("gloss_threshold must be a number from 0 to 1")
    if not 1 <= attempts <= MAX_ATTEMPTS:
        raise ValueError(f"gloss_attempts must be a whole number from 1 to {MAX_ATTEMPTS}")
    return {"gloss_gate": bool(on), "gloss_threshold": threshold, "gloss_attempts": attempts}


def strip(body):
    return {k: v for k, v in (body or {}).items() if k not in FIELDS}


def _public(st):
    out = {k: v for k, v in st.items() if k != "cancel"}
    out["gloss_attempts"] = [dict(a) for a in st.get("gloss_attempts") or []]
    return out


def job(jid):
    with LOCK:
        st = LIVE.get(str(jid))
        return _public(st) if st else None


def cancel(jid):
    with LOCK:
        st = LIVE.get(str(jid))
        eng = _ENGINE.get(str(jid))
        if not st:
            return False
        st["cancel"] = True
        eid = st.get("engine")
    if eng and eid:
        try:
            eng("/job?id=" + urllib.parse.quote(str(eid)) + "&cancel=1", {})
        except Exception:
            pass
    with LOCK:
        if st.get("status") in ("queued", "running"):
            st["status"] = "cancelled"
    return True


def wait_synth(engine, req, cancel=None, on_id=None):
    """POST /synth and poll /job until the engine finishes. cancel() stops the engine job."""
    started = engine("/synth", req)
    jid = started.get("id") if isinstance(started, dict) else None
    if not jid:
        return {"status": "failed", "error": (started or {}).get("error") or "the engine did not start a job",
                "takes": [], "lm_seed": req.get("lm_seed")}
    if on_id:
        on_id(jid)
    while True:
        if cancel and cancel():
            try:
                engine("/job?id=" + urllib.parse.quote(str(jid)) + "&cancel=1", {})
            except Exception:
                pass
            return {"id": jid, "status": "cancelled", "takes": [], "lm_seed": req.get("lm_seed")}
        time.sleep(POLL)
        try:
            st = engine("/job?id=" + urllib.parse.quote(str(jid)))
        except Exception as e:
            return {"id": jid, "status": "failed", "error": str(e), "takes": [], "lm_seed": req.get("lm_seed")}
        if st.get("status") in ("done", "failed", "cancelled"):
            st = dict(st)
            st["id"] = jid
            st.setdefault("takes", [])
            if _seed(st.get("lm_seed")) is None:
                st["lm_seed"] = req.get("lm_seed")
            return st


def run(req, synth, score, threshold=DEFAULT_THRESHOLD, attempts=DEFAULT_ATTEMPTS, rng=None, cancel=None, on_attempt=None):
    """synth(req) -> a finished job {id, takes, lm_seed, status}. score(take, lyrics) -> {wer}.
    The returned job is the attempt with the lowest WER. gloss_attempts lists every one."""
    rng = rng or random.Random()
    lyrics = str((req or {}).get("lyrics") or "")
    current = dict(req or {})
    used = set()
    seed = _seed(current.get("lm_seed"))
    if seed is None:
        seed = next_seed(used, rng)
        current["lm_seed"] = seed
    tries, best = [], None
    stopped = None
    for n in range(1, int(attempts) + 1):
        if cancel and cancel():
            stopped = "cancelled"
            break
        job = synth(dict(current)) or {}
        if job.get("status") == "cancelled" or (cancel and cancel()):
            row = {"n": n, "lm_seed": _seed(job.get("lm_seed")) or _seed(current.get("lm_seed")),
                   "job": job.get("id"), "take": None, "wer": None, "error": "cancelled"}
            tries.append(row)
            if on_attempt:
                on_attempt(dict(row))
            stopped = "cancelled"
            break
        take = (job.get("takes") or [None])[0]
        seed = _seed(job.get("lm_seed")) or _seed(current.get("lm_seed"))
        if seed is not None:
            used.add(seed)
        if not take or job.get("status") == "failed":
            row = {"n": n, "lm_seed": seed, "job": job.get("id"), "take": take, "wer": None,
                   "error": job.get("error") or "no take"}
            tries.append(row)
            if on_attempt:
                on_attempt(dict(row))
            if n >= attempts:
                break
            current = dict(current, lm_seed=next_seed(used, rng))
            continue
        try:
            measured = score(take, lyrics) or {}
            err = round(float(measured["wer"]), 4)
            row = {"n": n, "lm_seed": seed, "job": job.get("id"), "take": take, "wer": err}
        except Exception as e:
            row = {"n": n, "lm_seed": seed, "job": job.get("id"), "take": take, "wer": None, "error": str(e)}
            err = None
        tries.append(row)
        if on_attempt:
            on_attempt(dict(row))
        if err is not None and (best is None or err < best[0]):
            best = (err, job, row)
        if err is not None and err <= threshold:
            break
        if n < attempts:
            current = dict(current, lm_seed=next_seed(used, rng))
    if stopped == "cancelled":
        return {"status": "cancelled", "takes": [], "gloss_attempts": tries, "gloss_threshold": threshold,
                "gloss_kept": best[2]["take"] if best else None, "gloss_wer": best[0] if best else None}
    if best is None:
        return {"status": "failed", "error": (tries[-1].get("error") if tries else "no take"),
                "takes": [], "gloss_attempts": tries, "gloss_threshold": threshold}
    kept = dict(best[1])
    kept["takes"] = [best[2]["take"]]
    kept["lm_seed"] = best[2]["lm_seed"]
    kept["status"] = "done"
    kept["gloss_attempts"] = tries
    kept["gloss_threshold"] = threshold
    kept["gloss_wer"] = best[2]["wer"]
    kept["gloss_kept"] = best[2]["take"]
    return kept


def start(req, score, threshold, attempts, synth=None, engine=None, rng=None, on_done=None):
    """A job of its own. Poll job(id). engine is api._engine; tests pass synth instead."""
    if synth is None and engine is None:
        raise ValueError("a synth or an engine is needed")
    jid = "gg-" + time.strftime("%Y%m%d-%H%M%S") + "-" + str(random.randrange(1000, 9999))
    state = {"id": jid, "status": "queued", "gloss_attempts": [], "gloss_threshold": threshold,
             "gloss_wer": None, "takes": [], "cancel": False}
    with LOCK:
        LIVE[jid] = state
        if engine is not None:
            _ENGINE[jid] = engine

    def work():
        state["status"] = "running"

        def one(body):
            if state.get("cancel"):
                return {"status": "cancelled", "takes": [], "lm_seed": body.get("lm_seed")}
            if engine is not None:
                return wait_synth(engine, body, cancel=lambda: bool(state.get("cancel")),
                                  on_id=lambda i: state.update(engine=i))
            return synth(body)

        try:
            done = run(req, one, score, threshold=threshold, attempts=attempts, rng=rng,
                       cancel=lambda: bool(state.get("cancel")),
                       on_attempt=lambda row: state["gloss_attempts"].append(row))
        except Exception as e:
            state.update(status="failed", error=str(e))
            return
        if state.get("cancel"):
            state["status"] = "cancelled"
            state["gloss_attempts"] = done.get("gloss_attempts") or state["gloss_attempts"]
            state["takes"] = []
        else:
            for k, v in done.items():
                if k != "id":
                    state[k] = v
            state["id"] = jid
            state["engine_job"] = done.get("id")
        if on_done and not state.get("cancel"):
            try:
                on_done(state)
            except Exception as e:
                state["filing_error"] = str(e)
        threading.Timer(600, lambda: (LIVE.pop(jid, None), _ENGINE.pop(jid, None))).start()

    threading.Thread(target=work, daemon=True).start()
    return _public(state)
