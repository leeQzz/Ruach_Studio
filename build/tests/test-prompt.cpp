// test-prompt.cpp: exclude styles on the unconditional CFG stream, pure CPU, no model.
//
// A blank exclude leaves the instruction-only negative the engine has always
// built. A line of excluded styles fills the [Tags] slot of that stream and
// leaves [Lyrics] empty, the same text frame as the lyricless positive prompt
// the split guidance (cfg_lyrics) reads. The score framing does not change:
// off mode still has no ABC slot.
//
// usage: test-prompt        (exit 0 when every case holds)

#include "prompt.h"
#include "request.h"

#include <cstddef>
#include <cstdio>
#include <string>
#include <vector>

static int g_failed = 0;

static void check(bool ok, const char * name, const std::string & detail) {
    printf("[%s] %s%s%s\n", ok ? "PASS" : "FAIL", name, ok ? "" : ": ", ok ? "" : detail.c_str());
    g_failed += ok ? 0 : 1;
}

// One byte one id, above the special tokens, so the text can be read back.
static std::vector<int> enc(const std::string & text) {
    std::vector<int> ids;
    ids.reserve(text.size());
    for (unsigned char c : text) {
        ids.push_back(1000 + (int) c);
    }
    return ids;
}

static std::string dec(const std::vector<int> & ids) {
    std::string s;
    for (int id : ids) {
        if (id >= 1000 && id < 1256) {
            s.push_back((char) (id - 1000));
        }
    }
    return s;
}

// The negative stream as it was before exclude styles: instruction alone.
template <typename Tokenize>
static std::vector<int> legacy_negative(Tokenize bpe_encode, Yue2Cot cot, const std::vector<int> * abc_ids) {
    std::vector<int> ids;
    ids.push_back(YUE2_EOD);
    for (int id : bpe_encode(std::string(yue2_instruction(cot)))) {
        ids.push_back(id);
    }
    if (cot == YUE2_COT_OFF) {
        ids.push_back(YUE2_MUSIC_START);
        return ids;
    }
    ids.push_back(YUE2_ABC_START);
    if (abc_ids) {
        for (int id : *abc_ids) {
            ids.push_back(id);
        }
    }
    ids.push_back(YUE2_ABC_END);
    ids.push_back(YUE2_MUSIC_START);
    return ids;
}

static bool same(const std::vector<int> & a, const std::vector<int> & b) {
    return a == b;
}

static std::vector<int> tail_from(const std::vector<int> & ids, int marker) {
    for (size_t i = 0; i < ids.size(); i++) {
        if (ids[i] == marker) {
            return std::vector<int>(ids.begin() + (std::ptrdiff_t) i, ids.end());
        }
    }
    return {};
}

int main() {
    const std::vector<int> abc = { 7, 8, 9 };
    const char *           blanks[] = { "", "   ", "\n\n", " \t\r\n " };
    for (Yue2Cot cot : { YUE2_COT_OFF, YUE2_COT_MELODY, YUE2_COT_FULL }) {
        const std::vector<int> * score = cot == YUE2_COT_OFF ? nullptr : &abc;
        std::vector<int>        old    = legacy_negative(enc, cot, score);
        for (const char * blank : blanks) {
            std::vector<int> got = yue2_build_negative_ids(enc, cot, score, blank);
            check(same(got, old), "a blank exclude keeps the instruction-only pass",
                  std::string(yue2_instruction(cot)).substr(0, 24) + " / «" + blank + "»");
        }
        check(same(yue2_build_negative_ids(enc, cot, score), old), "the three-argument call is the blank exclude", "");
    }

    check(yue2_style_line("  drones\nbeats  ") == "drones, beats", "lines join with a comma", yue2_style_line("  drones\nbeats  "));
    check(yue2_style_line("drones,\nbeats") == "drones, beats", "a comma already there takes a space", yue2_style_line("drones,\nbeats"));
    check(yue2_style_line("\n  \n") == "", "blank lines are empty", "«" + yue2_style_line("\n  \n") + "»");

    for (Yue2Cot cot : { YUE2_COT_OFF, YUE2_COT_MELODY, YUE2_COT_FULL }) {
        const std::vector<int> * score = cot == YUE2_COT_OFF ? nullptr : &abc;
        std::vector<int>         neg    = yue2_build_negative_ids(enc, cot, score, "drones\nbeats");
        std::string              want   = yue2_request_text(cot, "drones, beats", "");
        check(dec(neg) == want, "excluded styles fill [Tags] and leave [Lyrics] empty", dec(neg));
        check(neg.front() == YUE2_EOD, "the negative stream still opens on the end-of-text token", "");
        if (cot == YUE2_COT_OFF) {
            check(tail_from(neg, YUE2_ABC_START).empty() && neg.back() == YUE2_MUSIC_START,
                  "off mode still has no ABC slot", "");
        } else {
            std::vector<int> tail = tail_from(neg, YUE2_ABC_START);
            std::vector<int> expect = { YUE2_ABC_START, 7, 8, 9, YUE2_ABC_END, YUE2_MUSIC_START };
            check(same(tail, expect), "the score framing is unchanged", "");
        }
    }

    // Split CFG (generate.h): uncond carries the exclude, styled carries the
    // requested style with empty lyrics, cond carries that style and the lyrics.
    // In melody and full the three share one score tail, so the style scale
    // compares styles and the lyrics scale compares lyrics.
    {
        std::vector<int> cond   = yue2_build_prompt_ids(enc, YUE2_COT_FULL, "folk ballad", "la la", &abc);
        std::vector<int> styled = yue2_build_prompt_ids(enc, YUE2_COT_FULL, "folk ballad", "", &abc);
        std::vector<int> uncond = yue2_build_negative_ids(enc, YUE2_COT_FULL, &abc, "drones, drums");
        check(dec(cond) == yue2_request_text(YUE2_COT_FULL, "folk ballad", "la la"), "the conditional prompt keeps its style and lyrics", dec(cond));
        check(dec(styled) == yue2_request_text(YUE2_COT_FULL, "folk ballad", ""), "the lyricless branch keeps the requested style", dec(styled));
        check(dec(uncond) == yue2_request_text(YUE2_COT_FULL, "drones, drums", ""), "the unconditional branch carries the excluded styles", dec(uncond));
        check(same(tail_from(cond, YUE2_ABC_START), tail_from(styled, YUE2_ABC_START)) &&
                  same(tail_from(styled, YUE2_ABC_START), tail_from(uncond, YUE2_ABC_START)),
              "split CFG: the three branches share the score", "");
        check(dec(styled) != dec(uncond) && dec(cond) != dec(styled), "split CFG: style and lyrics are different deltas", "");
    }

    Yue2Request r;
    check(request_parse_json(&r, "{\"style\":\"folk\",\"exclude_styles\":\"  drones\\nbeats  \"}"), "a request with exclude styles parses", "");
    check(r.style == "folk" && r.exclude_styles == "drones, beats", "exclude styles are stored as one line", r.exclude_styles);
    std::string json = request_to_json(&r, true);
    Yue2Request back;
    check(request_parse_json(&back, json.c_str()) && back.exclude_styles == "drones, beats" && back.style == "folk",
          "exclude styles round-trip in the request JSON", json);
    Yue2Request plain;
    request_init(&plain);
    check(request_to_json(&plain, true).find("exclude_styles") == std::string::npos, "a blank exclude is left out of a sparse request",
          request_to_json(&plain, true));
    Yue2Request ignored;
    check(request_parse_json(&ignored, "{\"exclude_styles\":1}") && ignored.exclude_styles.empty(), "a non-text exclude is ignored", ignored.exclude_styles);
    Yue2Request spaces;
    check(request_parse_json(&spaces, "{\"exclude_styles\":\"  \\n  \"}") && spaces.exclude_styles.empty(), "a whitespace exclude is empty",
          "«" + spaces.exclude_styles + "»");

    printf("%s\n", g_failed ? "FAILED" : "all cases hold");
    return g_failed ? 1 : 0;
}
