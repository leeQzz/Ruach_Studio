"""nar_pieces on POST /api/v1/songs, and the MCP tool that calls it.

Left out, the API does not invent a bool: /synth joins when the duration is past one sound window.
true and false are forwarded. Anything else is 400.
python3 lab/test_api_nar_pieces.py
"""
import ast
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import api  # noqa: E402

bad = []


def check(what, ok, got=""):
    print(("  ok    " if ok else "  FAIL  ") + what + ("" if ok else "  (" + str(got) + ")"))
    if not ok:
        bad.append(what)


def post(payload):
    asked = []
    api._engine = lambda path, body=None, timeout=60: asked.append((path, body)) or {"id": "j1"}
    ctx = {"collection": None, "outputs": None, "config_dir": lambda: None}
    code, body = api.handle("POST", "/api/v1/songs", {}, json.dumps(payload).encode(), {}, ctx)
    return code, body, asked


code, body, asked = post({"style": "folk", "lyrics": "a", "duration": 480})
sent = asked[0][1] if asked else {}
check("left out is not sent, so /synth applies the window default", code == 202 and "nar_pieces" not in sent, (code, sent))

code, body, asked = post({"style": "folk", "lyrics": "a", "duration": 180, "nar_pieces": False})
sent = asked[0][1] if asked else {}
check("false is forwarded", code == 202 and sent.get("nar_pieces") is False, (code, sent))

code, body, asked = post({"style": "folk", "lyrics": "a", "duration": 180, "nar_pieces": True})
sent = asked[0][1] if asked else {}
check("true is forwarded", code == 202 and sent.get("nar_pieces") is True, (code, sent))

code, body, asked = post({"style": "folk", "lyrics": "a", "nar_pieces": "yes"})
check("a non-bool is 400 and the engine is not asked", code == 400 and not asked and "nar_pieces" in str(body), (code, body, asked))

spec = api.openapi()
prop = spec["paths"]["/songs"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"].get("nar_pieces")
check("OpenAPI lists nar_pieces and the omitted default", isinstance(prop, dict) and "Left out" in prop.get("description", ""), prop)

mcp = Path(__file__).resolve().parent.parent / "extras" / "ruach-mcp.py"
tree = ast.parse(mcp.read_text(encoding="utf-8"))
fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "generate_song")
names = [a.arg for a in fn.args.args]
defaults = fn.args.defaults
none_at = names.index("nar_pieces")
check("MCP keeps wait where it was and nar_pieces defaults to None",
      names[names.index("wait") - 1] == "midi_key" and isinstance(defaults[none_at - (len(names) - len(defaults))], ast.Constant)
      and defaults[none_at - (len(names) - len(defaults))].value is None, names)
forwarded = any(isinstance(n, ast.Assign) and any(isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant)
                                                  and t.slice.value == "nar_pieces" for t in n.targets) for n in ast.walk(fn))
check("MCP writes nar_pieces onto the song body", forwarded)
doc = ast.get_docstring(fn) or ""
check("MCP says an omitted field joins past one window", "left out" in doc and "false stops" in doc, doc[:240])

print("all good" if not bad else f"{len(bad)} failed")
sys.exit(1 if bad else 0)
