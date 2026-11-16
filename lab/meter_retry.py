"""A lyric line's syllables against the vocal phrase's notes. A section that misses is sung again.

Syllables are vowel groups, Latin and Cyrillic. Notes are the V:Vocal music line: a rest (z, Z) is not a note,
and a quoted chord is not a note. A broken score still yields a count; this does not ask the full ABC reader.
Off by more than two, the section is sung again through the same redo a section replace uses.

    compare(lyrics, abc)                         the lines, and which sections miss
    run(name, lyrics, abc, redo_fn, seconds)     one pass: the mismatched sections, together
    begin(data, load, redo_fn)                   the lab's POST /meter/retry
"""
import re
import threading
import time

TOLERANCE = 2
VOWELS = set("aeiouyAEIOUYаеёиоуыэюяАЕЁИОУЫЭЮЯ")
NOTE = set("ABCDEFGabcdefg")
LIVE = {}
LOCK = threading.Lock()


def syllables(text):
    """Vowel groups in the line. A tag, a parenthetical, and a rest of punctuation add none of their own."""
    s = re.sub(r"\([^)]*\)", " ", str(text or ""))
    n, on = 0, False
    for ch in s:
        if ch in VOWELS:
            if not on:
                n += 1
                on = True
        else:
            on = False
    return n


def _notes(line):
    line = re.sub(r'"[^"]*"', "", line)
    n, i = 0, 0
    while i < len(line):
        c = line[i]
        if c in "^_=":
            i += 1
            continue
        if c in NOTE:
            n += 1
        i += 1
    return n


def vocal_phrases(abc):
    """Note counts, one per V:Vocal music line. No vocal line: an empty list."""
    voice, out = None, []
    for raw in str(abc or "").splitlines():
        s = raw.strip()
        if not s or s.startswith("%"):
            continue
        if s.upper().startswith("V:"):
            voice = s[2:].strip()
            continue
        if not voice or not voice.lower().startswith("vocal"):
            continue
        if re.match(r"^[A-Za-z]:", s):
            continue
        if not re.search(r"[A-Ga-gzZ|]", s):
            continue
        out.append(_notes(s))
    return out


def _lines(lyrics):
    section, rows = "the song", []
    for raw in str(lyrics or "").splitlines():
        s = raw.strip()
        if not s:
            continue
        if len(s) >= 3 and s.startswith("[") and s.endswith("]") and not s.startswith("[["):
            section = s[1:-1].strip() or section
            continue
        rows.append({"section": section, "line": s, "syllables": syllables(s)})
    return rows


def compare(lyrics, abc, tolerance=TOLERANCE):
    phrases = vocal_phrases(abc)
    rows = _lines(lyrics)
    if not phrases:
        return {"rows": rows, "redo": [], "why": "no vocal line in the score", "phrases": []}
    redo = []
    for i, row in enumerate(rows):
        notes = phrases[i] if i < len(phrases) else None
        row["notes"] = notes
        miss = notes is None or abs(row["syllables"] - notes) > tolerance
        row["miss"] = miss
        if miss and row["section"] not in redo:
            redo.append(row["section"])
    for row in rows:
        row.setdefault("notes", None)
        row.setdefault("miss", False)
    return {"rows": rows, "redo": redo, "why": "", "phrases": phrases}


def _parts(lyrics, names, seconds):
    import quality_loop
    spans = [s for s in quality_loop.section_spans(lyrics, seconds) if s["section"] in names]
    return [{"a": s["a"], "b": s["b"], "section": s["section"]} for s in spans]


def run(name, lyrics, abc, redo_fn, seconds, tolerance=TOLERANCE):
    found = compare(lyrics, abc, tolerance)
    parts = _parts(lyrics, found["redo"], seconds) if found["redo"] else []
    out = {"status": "done", "name": name, "take": name, "takes": [], "redo": found["redo"],
           "why": found["why"], "rows": found["rows"], "passes": []}
    if not parts:
        return out
    made = redo_fn(name, parts, lyrics) or {}
    child = (made.get("takes") or [None])[0]
    row = {"take": name, "redo": parts, "section": made.get("section")}
    if not child:
        row["error"] = made.get("error") or "the section was not remade"
        out["passes"] = [row]
        out["status"] = "failed"
        return out
    out["passes"] = [row]
    out["take"] = child
    out["takes"] = [child]
    return out


def job(jid):
    with LOCK:
        st = LIVE.get(str(jid))
        return dict(st) if st else None


def start(name, lyrics, abc, redo_fn, seconds, tolerance=TOLERANCE):
    jid = "mr-" + time.strftime("%Y%m%d-%H%M%S-") + f"{time.time_ns() % 10000:04d}"
    state = {"id": jid, "name": name, "status": "queued", "redo": [], "rows": [], "passes": [], "take": name, "takes": []}

    def work():
        try:
            with LOCK:
                state["status"] = "running"
            done = run(name, lyrics, abc, redo_fn, seconds, tolerance)
            with LOCK:
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


def begin(data, load, redo_fn):
    """load(name) -> {lyrics, abc, seconds}. lyrics and abc in the body win."""
    data = data or {}
    name = str(data.get("name") or "").strip()
    if not name:
        return 400, {"error": "name the take"}
    try:
        loaded = load(name) or {}
    except FileNotFoundError:
        return 404, {"error": "no such take"}
    except ValueError as e:
        return 400, {"error": str(e)}
    lyrics = data["lyrics"] if "lyrics" in data and data["lyrics"] is not None else loaded.get("lyrics") or ""
    abc = data["abc"] if "abc" in data and data["abc"] is not None else loaded.get("abc") or ""
    if not str(lyrics).strip():
        return 400, {"error": "the meter retry needs lyrics"}
    if not str(abc).strip():
        return 400, {"error": "the meter retry needs a score"}
    try:
        seconds = float(data.get("seconds") if data.get("seconds") is not None else loaded.get("seconds") or 180)
    except (TypeError, ValueError):
        return 400, {"error": "seconds must be a number"}
    started = start(name, str(lyrics), str(abc), redo_fn, seconds)
    return 202, {"id": started["id"]}
