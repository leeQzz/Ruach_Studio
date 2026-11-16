"""Exclude styles on POST /api/v1/songs: forwarded as one line, omitted when blank, refused past 8000 characters.
python3 lab/test_exclude_styles.py"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import api  # noqa: E402

bad = []


def check(what, ok, got=""):
    print(("  ok    " if ok else "  FAIL  ") + what + ("" if ok else "  (" + str(got) + ")"))
    if not ok:
        bad.append(what)


class Col:
    def catalog(self, outputs, cfg):
        return {"takes": [{"name": "t1", "title": "Morning"}, {"name": "t2", "title": "Older"}]}


with tempfile.TemporaryDirectory() as tmp:
    out = Path(tmp)
    for name, req in (("t1", {"style": "folk", "lyrics": "la", "exclude_styles": "drones, beats"}),
                      ("t2", {"style": "folk", "lyrics": "la"})):
        d = out / name
        d.mkdir()
        (d / "meta.json").write_text(json.dumps({"title": name, "created": 1}), encoding="utf-8")
        (d / "request.json").write_text(json.dumps(req), encoding="utf-8")
    asked = []
    api._engine = lambda path, body=None, timeout=60: asked.append((path, body)) or {"id": "j1"}
    ctx = {"collection": Col(), "config_dir": lambda: out, "outputs": out}

    def post(payload):
        asked.clear()
        return api.handle("POST", "/api/v1/songs", {}, json.dumps(payload).encode(), {}, ctx)

    code, body = post({"style": "folk", "lyrics": "la", "exclude_styles": "  drones\nbeats  "})
    sent = asked[0][1] if asked else {}
    check("a song forwards exclude styles as one line", code == 202 and sent.get("exclude_styles") == "drones, beats" and asked[0][0] == "/synth",
          (code, body, asked))

    code, body = post({"style": "folk", "lyrics": "la", "exclude_styles": "  \n  "})
    sent = asked[0][1] if asked else {}
    check("a blank exclude is not sent", code == 202 and "exclude_styles" not in sent, (code, sent))

    code, body = post({"style": "folk", "lyrics": "la"})
    sent = asked[0][1] if asked else {}
    check("a song without the field leaves the pass alone", code == 202 and "exclude_styles" not in sent, (code, sent))

    code, body = post({"style": "folk", "exclude_styles": "x" * 8001})
    check("longer than 8000 is refused before the engine", code == 400 and "8000" in str(body) and not asked, (code, body, asked))

    spec = api.openapi()
    props = spec["paths"]["/songs"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]
    check("the OpenAPI song body names exclude_styles", "exclude_styles" in props, list(props))

    code, body = api.handle("GET", "/api/v1/takes/t1", {}, b"", {}, ctx)
    check("a take made with an exclude says so", code == 200 and body.get("request", {}).get("exclude_styles") == "drones, beats", (code, body))
    code, body = api.handle("GET", "/api/v1/takes/t2", {}, b"", {}, ctx)
    check("an older take has no exclude field", code == 200 and "exclude_styles" not in body.get("request", {}), (code, body.get("request")))

    code, body = post({"style": "folk", "lyrics": "la", "exclude_styles": "drones", "cfg_scale": 1})
    sent = asked[0][1] if asked else {}
    check("guidance 1 with no lyrics split still forwards the exclude and says it was not used",
          code == 202 and sent.get("exclude_styles") == "drones" and sent.get("cfg_scale") == 1
          and body.get("exclude_unused") is True and body.get("exclude_warning") == api.EXCLUDE_UNUSED and sent.get("debuzz") is True,
          (code, body, sent))

    code, body = post({"style": "folk", "lyrics": "la", "exclude_styles": "drones", "cfg_scale": 1, "cfg_lyrics": 2.2})
    sent = asked[0][1] if asked else {}
    check("a lyrics split at guidance 1 still uses the exclude",
          code == 202 and "exclude_warning" not in body and sent.get("cfg_lyrics") == 2.2 and sent.get("exclude_styles") == "drones",
          (code, body, sent))

    code, body = post({"style": "folk", "lyrics": "la", "exclude_styles": "drones"})
    check("the default guidance uses the exclude and does not warn", code == 202 and "exclude_warning" not in body, (code, body))

    code, body = post({"style": "folk", "lyrics": "la", "exclude_styles": "drones", "cfg_scale": 1.6, "debuzz": False})
    sent = asked[0][1] if asked else {}
    check("exclude and debuzz travel together",
          code == 202 and sent.get("exclude_styles") == "drones" and sent.get("debuzz") is False and "exclude_warning" not in body,
          (code, body, sent))

    code, body = post({"style": "folk", "debuzz": "no"})
    check("debuzz that is not a bool is refused", code == 400 and "debuzz" in str(body) and not asked, (code, body, asked))

    props = api.openapi()["paths"]["/songs"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]
    check("the OpenAPI song body keeps exclude_styles and debuzz",
          props.get("exclude_styles", {}).get("type") == "string" and props.get("debuzz", {}).get("type") == "boolean", props)

print("all good" if not bad else f"{len(bad)} failed")
sys.exit(1 if bad else 0)
