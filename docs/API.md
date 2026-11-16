# Ruach Studio · the API

The Studio's own door for scripts, DAWs and agents **on this machine and its local network**. It is the same server as the page (`http://HOST:7777`), under `/api/v1`. The Studio answers private addresses only: this machine, 10.x, 172.16–31.x, 192.168.x, 100.64–127.x (carrier-grade NAT, where Tailscale lives), link-local, and their IPv6 kin. Anyone else gets `403`. `RUACH_ALLOW_PUBLIC=1` (in the Studio's environment) opens it to all, for those who put their own proxy and guard in front. A request a page of another website makes is refused (`403`), and so is a name, which a site on the internet or the network's own names could point at the machine: an address of the private networks above, written as one (except 100.64–127.x: a Tailscale address is answered when `RUACH_HOSTS` names it), and localhost are answered, and `RUACH_HOSTS` (commas or spaces between them; `.example.org` takes its subdomains; an address beyond the private networks too) names any other. Every answer also says who may put the Studio in a frame: its own pages and Pinokio's (`http://localhost:42000`, `http://127.0.0.1:42000`); `RUACH_FRAME_ANCESTORS` (each `scheme://host:port`, commas or spaces between them) names more. Through the https door the caller is the visitor, not the door, so a token is asked of it as of anyone else on the network.

A token is asked only when `RUACH_API_TOKEN` is set in the Studio's environment, and only of callers not on the Studio's machine: `Authorization: Bearer <token>`.

The full description, for tools that read it: `GET /api/v1/openapi.json` (OpenAPI 3.1).

## Routes

| | route | what it does |
|---|---|---|
| GET | `/api/v1` | what this is, and every route |
| GET | `/api/v1/health` | the engine (busy, memory loaded, cards), the lab, and the GPU guard's verdict on each card |
| POST | `/api/v1/songs` | make a song; answers `{job, poll}` at once. `gloss_gate` (off unless true) scores the take and reseeds while the word error is over `gloss_threshold` (0.35), up to `gloss_attempts` (3, at most 8). The job then lists every attempt and keeps the lowest error |
| POST | `/api/v1/score/from-midi` | a MIDI file as a song's score, without making the song: `{midi_b64, key, ins}` → the score and how it was read |
| GET | `/api/v1/jobs/ID` | queued, running, done, failed or cancelled; when done, its `takes` |
| POST | `/api/v1/jobs/ID/cancel` | stop it |
| GET | `/api/v1/takes` | the takes, newest first: `?workspace=` `?q=` (title or note) `?limit=` `?offset=` |
| GET | `/api/v1/takes/NAME` | one take: its marks, workspaces, and what it was made with (style, lyrics, score, seeds, adapters) |
| GET | `/api/v1/takes/NAME/audio` | its audio (a redirect to the file the engine serves). `?raw=1` is the decoder's peak-whole WAV when debuzz kept one (`audio.raw.wav`); 404 when the take has none |
| POST | `/api/v1/takes/NAME/marks` | `{rating: 1 / -1 / 0, favorite: true/false, note: "…"}` |
| POST | `/api/v1/takes/NAME/reaper` | the take as a REAPER project (see the guide's DAW tab); answers where to fetch the ZIP |
| GET | `/api/v1/takes/NAME/artwork` | its artwork (a redirect to the JPEG, 768 px), 404 until it has one |
| POST | `/api/v1/takes/NAME/artwork` | draw it: `{again, seed}`; answers `202 {status}` while drawing (ask again), then the prompt and the seed |
| GET | `/api/v1/takes/NAME/derived` | the Refiner's tree: every branch made from the take, its files (each with a `url`), and what runs |
| GET | `/api/v1/takes/NAME/file?path=derived/…` | one file of the tree (a redirect to it) |
| POST | `/api/v1/takes/NAME/stems` | `{mode: four \| vocals, source}`: four by default; `source` a debuzzed or upscaled file of the tree, a set of its own (the same file again answers with the set it gave) |
| POST | `/api/v1/takes/NAME/debuzz` | `{strength: 0.1–1, source}`: the take itself, when the engine already cleaned it, is not cured again |
| POST | `/api/v1/takes/NAME/remaster` | `{source, preset, lufs, tp, hz432, deess, cleanup, fade, formats}` (the Refiner's Remaster step) |
| POST | `/api/v1/takes/NAME/upscale` | `{mode: subtle \| normal \| high \| extreme, variants: 1 \| 2, keep, source}` |
| POST | `/api/v1/takes/NAME/lyrics-check` | `{source, again}`: Whisper against the lyrics, the result when ready |
| GET | `/api/v1/workspaces` | the workspaces and how many takes each holds |
| POST | `/api/v1/workspaces/WS` | `{op: "add" / "remove" / "move", names: [...], from: "…"}` |

### Making a song

```json
POST /api/v1/songs
{
  "title": "Morning",
  "style": "Russian, folk ballad, female alto, acoustic guitar and cello, 72 bpm",
  "lyrics": "[Verse]\nfirst line\nsecond line\n\n[Chorus]\n…",
  "mode": "direct",
  "duration": 180,
  "workspace": "From my scripts"
}
```

- `mode`: `direct` (fastest), `full` (a score first: melody and chords, then the song), `melody`. With a score of your own (`abc`, or a MIDI file) and no mode, the score's kind picks it: `full` when it has chord symbols, `melody` when not. `direct` with a score is refused (`400`): direct makes the music without one, and the score would be dropped.
- `exclude_styles`: what the song must not have (`drones, beats, drums`). The guidance's unconditional pass carries these styles instead of an empty style, so the song is pushed away from them. Left out or empty, that pass stays the instruction alone. Lines are joined into one, as in the Creator. Longer than 8000 characters is refused. Guidance of exactly `1.0`, with `cfg_lyrics` left out or equal to that same `1.0`, is a single branch: the exclusion would not move a logit. The song is still made, and the answer says `exclude_unused: true` with `exclude_warning` (`exclude_styles was not used: guidance 1.0 keeps a single branch`). The job status repeats that sentence. Any other guidance, or a lyrics scale of its own, uses the exclusion.
- `instrumental: true` makes it without a voice (the lyrics are then left out).
- `music_seed`, `sound_seed`: to make a take again; left out, both are random.
- `abc`: your own score, in YuE2's two-voice dialect (what the Creator's score drawer holds).
- `midi_b64`: a standard MIDI file, base64, as the song's melody (below). `midi_key`: the key to write it in when the file has none (`Em`, `Bb`, `F#m`…); `midi_ins`: `auto`, `keep` or `none` (below).
- `duration`: seconds; left out, 180, or the MIDI file's own length.
- `nar_pieces`: the long-song choice, the same one as the console's Long songs.
  - Left out: the engine decides from the real prefix. When `duration` is past one sound window (about 6 to 7¾ minutes at a full context, less with a long score or a smaller Engine context), the music runs to that duration and the sound is joined. When it fits, the song stays one window. The API's own default duration, 180 seconds, fits, so an old client that omits both fields is unchanged.
  - `false`: Whole. The music stops at one window, even when `duration` is longer. This is what the console sends when Whole is selected.
  - `true`: Pieces. The music is not cut at the window. A song that fits is still one chunk.
  - Anything other than `true` or `false` is `400`.
  - An old client of `POST /synth`, `/api/v1/songs`, or the MCP tool that omits the field and asks for a length past one window (the engine's own default duration is 8:00) is no longer cut there. To keep that cut, send `false`. A saved `request.json` that does not contain the field is decided again the same way; one that contains `false` stays Whole.
- `debuzz`: `true` by default. A WAV take is written with the decoder's 25 Hz frame buzz taken out of the highs (strength 0.8, the measured cure). `audio.wav` and the player's `listen.flac` are that cleaned file; `audio.raw.wav` beside them is the decoder's own peak-whole file. `false` leaves the take as the decoder wrote it, and nothing is kept beside it. An MP3 is already encoded and is left as it is: `debuzzed` is false and `debuzz_note` is `mp3`, even when the request says `debuzz: true` (the checkbox can stay on; the file is not cleaned, and nothing reports `debuzzed: true`). A WAV32 (float) keeps the level the cure left, including a peak above full scale; 16- and 24-bit come down to 0.999 only when a peak would not fit. With no library (`--outputs` unset) the job's WAV is still cleaned, and the result's extra part named `raw` is the decoder's file (the same role as `audio.raw.wav`). A failed cure leaves the decoder's WAV as the audio and sets `debuzzed` false. Cancelling the job stops the cure: every variation is still saved, one whose cure finished stays `debuzzed` true, and one it did not finish is `debuzzed` false with note `cancel`. The cancelled job names those takes. `POST /takes/NAME/debuzz` on a take that is already cleaned does not run the cure again; pass `source` (for example `audio.raw.wav`) to cure that file. Anything other than `true` or `false` is `400`.
- Also passed on as they are: `loras`, `vae`, `steps`, `solver`, `cfg_scale`, `cfg_lyrics`, `output_format` (WAV 24-bit unless asked otherwise). `cfg_lyrics` left out is the engine's own (the same as Guidance). The Console's starting value depends on the backbone: RS-TW 2.4, RS-FW 2.2 (`lab/cfg_lyrics_sweep.py` measures the grid and reports word error; it does not load a model by itself). Anything other than a number is `400`. `nar_pieces` is passed on only when the request says it. `debuzz` defaults to `true` when the request leaves it out. `exclude_styles` and `debuzz` are both kept.
- `gloss_gate` (default off), `gloss_threshold` (0 to 1, default 0.35), `gloss_attempts` (1 to 8, default 3). With the gate on, the song is made, then Whisper's word error is measured (`lab/gloss_core.py`). Over the threshold, `lm_seed` is drawn again and the song is made again, up to that many attempts. The job's `takes` is the lowest error; `gloss_attempts` records each one (`n`, `lm_seed`, `job`, `take`, `wer`). The gate needs lyrics. Poll `GET /api/v1/jobs/ID` as for any other song: the id starts with `gg-`. A gated retry keeps `exclude_styles`, `debuzz`, and `nar_pieces`.
- `workspace`: the take lands there when it is done. With the gate on, the take that was kept.

### A take's flagged sections

`POST /lab/quality/loop` with `{name, iterations}` (the page's lab, the same host, under `/lab`). The take is inspected (`lab/inspect_job.py`). A dropout, a run of clicks, or a line a gloss already marked is sung again, and only that section (`lab/section.py`), up to `iterations` times (2, at most 4). A fault of the whole take is not sung again: the VAE's frame buzz names Debuzz, clipping or mains hum names Remaster. The answer is `202 {id}`; `GET /lab/quality/loop?id=` is the job, each pass on it (`passes`), and `suggest` when a tool is named. The Console's Artifacts drawer has the same action.

`POST /lab/meter/retry` with `{name, lyrics, abc}`. Each lyric line's syllables (vowel groups) are set against the notes of the next `V:Vocal` line. A rest and a quoted chord are not notes. Off by more than two, that section is sung again. `GET /lab/meter/retry?id=` is the job. The score drawer has the same action. A trained lyrics adapter is described in `docs/lyrics-adapter-plan.md` and is not part of this API.

### A melody as a MIDI file

Each track with notes is one line: the first two become the score's **Vocal** (sung) and **Ins** (the instruments' melody), by their names when a track is called *vocal / voice / vox / melody / sing* or *ins / instr / accomp*, else in order. A line is one note at a time: of notes starting together the highest stays. The grid is a sixteenth; the meter and the tempo are the file's (4/4 and 120 when it has none). The key: `midi_key`, else the file's key signature, else the one its notes suggest (said in the answer as `key_from`).

A second track of **chords** is not given as the Ins line (`midi_ins: auto`): its top notes as the instruments' melody let an instrument take the tune. Measured on one melody in an accordion waltz style, three seeds: with the chords' top line the instruments carried the tune in two takes of three; with the sung line alone the voice carried it in all three. `keep` gives their top line anyway (as the Creator's MIDI import does), `none` leaves out any second line.

The answer's `score` says how the file was read: `key` and `key_from`, `bpm`, `meter`, `bars`, `seconds`, the notes kept per voice and those left out (`dropped`), `ins_line`, and which tracks became which voice (`from`). The score is checked before anything is spent on it: it must read back (YuE2 Studio's own reader) as the very notes it was written from.

```bash
curl -s -X POST -H "Content-Type: application/json" \
  -d "{\"style\":\"Russian, folk ballad, warm female vocal, gusli\",\"lyrics\":\"[Verse]\\nТихо ветер над рекой\",\"midi_b64\":\"$(base64 -w0 melody.mid)\"}" \
  http://127.0.0.1:7777/api/v1/songs
```

## From the shell

```bash
B=http://127.0.0.1:7777/api/v1          # from another machine: the Studio's address on your network
J=$(curl -s -X POST -H "Content-Type: application/json" -d '{"style":"Instrumental, solo cello, slow","instrumental":true,"duration":60}' $B/songs | python3 -c "import json,sys; print(json.load(sys.stdin)['job'])")
until curl -s $B/jobs/$J | grep -q '"status": *"done"'; do sleep 3; done
T=$(curl -s $B/jobs/$J | python3 -c "import json,sys; print(json.load(sys.stdin)['takes'][0])")
curl -sL -o cello.wav $B/takes/$T/audio
```

## For agents: the MCP server

`extras/ruach-mcp.py` puts the API before any MCP client (Claude Desktop, Claude Code, others) as twelve tools (`generate_song` takes a MIDI file on the agent's machine as `midi_file`): `generate_song`, `job_status`, `cancel_job`, `list_takes`, `get_take`, `mark_take`, `list_workspaces`, `file_takes`, `export_reaper`, `draw_artwork`, `refine_take`, `studio_health`. It runs where the agent runs and talks HTTP to the Studio.

```bash
pip install mcp          # once; the SDK's v1 and v2 both do
```

The client's configuration (Claude Desktop: `claude_desktop_config.json`; Claude Code: `.mcp.json` in a project):

```json
{
  "mcpServers": {
    "ruach-studio": {
      "command": "python3",
      "args": ["/path/to/Ruach_Studio/extras/ruach-mcp.py"],
      "env": { "RUACH_URL": "http://127.0.0.1:7777" }
    }
  }
}
```

Then ask the agent in plain words: *make a two-minute instrumental for solo duduk and put it in the workspace "Sketches"*; *list my liked takes in Demos*; *export the last take to REAPER*.

## What it does not do (yet)

A Refiner step answers `202` with its job while it runs (its branch appears in `/derived` when done; `source` is a path in the tree, `derived/…`, or empty for the take). Training is not on the API yet. REAPER already talks to it: `extras/reaper/` (make a song from inside REAPER, its selected MIDI as the melody; bring a take).
