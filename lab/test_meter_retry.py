"""Syllables against the vocal phrase. The redo is a fake. No torch and no model are imported.
python3 lab/test_meter_retry.py"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import meter_retry  # noqa: E402

bad = 0


def check(name, ok, detail=None):
    global bad
    print(("  ok    " if ok else "  FAIL  ") + name + ("" if ok or detail is None else "  " + repr(detail)))
    bad += not ok


check("vowel groups, Latin and Cyrillic", meter_retry.syllables("the cat") == 2 and meter_retry.syllables("тихо") == 2)
check("a diphthong is one group", meter_retry.syllables("la la") == 2 and meter_retry.syllables("quiet") == 1 and meter_retry.syllables("audio") == 2)

ABC = """X:1
L:1/8
Q:1/4=96
M:4/4
K:C
V:Vocal
"C" C D E F | z2 G A |
V:Ins
"Am" C2 D2 | E2 F2 |
"""
check("the vocal line counts notes, not the chord, the rest, or the instruments",
      meter_retry.vocal_phrases(ABC) == [6], meter_retry.vocal_phrases(ABC))

LYRICS = "[Verse]\nla la la la\n\n[Chorus]\nno\n"
# la la la la = 4, the phrase has 6: off by 2, kept. "no" has no phrase: a miss.
found = meter_retry.compare(LYRICS, ABC)
check("within two syllables is kept, a line with no phrase is not",
      found["redo"] == ["Chorus"] and found["rows"][0]["miss"] is False and found["rows"][1]["miss"] is True, found)

WIDE = """V:Vocal
C D E F G A B c |
C D E F G A |
"""
found = meter_retry.compare("[Verse]\nla\n\n[Chorus]\nla la la la la la\n", WIDE)
check("off by more than two flags that section", found["redo"] == ["Verse"] and "Chorus" not in found["redo"], found)
check("no vocal line sings nothing again", meter_retry.compare(LYRICS, "V:Ins\nC D E F |\n")["why"] == "no vocal line in the score")

calls = []

def redo(name, parts, lyrics):
    calls.append(parts)
    return {"takes": ["child"], "section": "sec-9"}

out = meter_retry.run("take", LYRICS, ABC, redo, 60)
check("the mismatched section is sung again, and only that one",
      len(calls) == 1 and calls[0][0]["section"] == "Chorus" and out["takes"] == ["child"], calls)

calls.clear()
meter_retry.run("take", "[Verse]\nla la la la\n", ABC, redo, 60)
check("a line within two does not sing the section again", calls == [])

def load(name):
    if name == "missing":
        raise FileNotFoundError(name)
    return {"lyrics": "old", "abc": "V:Vocal\nC D |\n", "seconds": 40}

code, body = meter_retry.begin({"name": "song", "lyrics": LYRICS, "abc": ABC}, load, redo)
check("the lab start answers 202", code == 202 and str(body["id"]).startswith("mr-"), body)
st = {}
for _ in range(50):
    st = meter_retry.job(body["id"]) or {}
    if st.get("status") in ("done", "failed"):
        break
    time.sleep(0.01)
check("the job keeps the rows and the child", st.get("status") == "done" and st.get("takes") == ["child"] and st.get("rows"), st)
code, err = meter_retry.begin({"name": "song", "lyrics": LYRICS, "abc": ""}, load, redo)
check("an empty score is 400", code == 400, err)
code, err = meter_retry.begin({"name": "missing", "lyrics": LYRICS, "abc": ABC}, load, redo)
check("a missing take is 404", code == 404, err)
check("torch was not imported", "torch" not in sys.modules)

print("ok" if not bad else f"{bad} failed")
sys.exit(1 if bad else 0)
