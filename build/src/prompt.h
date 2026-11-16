// prompt.h: special token prompt assembly for the YuE2 backbone
//
// The prompt is one token stream:
//   <|endoftext|>instruction\n[Tags]\nstyle\n[Lyrics]\nlyrics\n
//   <abc>score</abc><|music_start|>
// The style and lyrics text reaches the model verbatim: the protocol defines
// no caption cleaning and no lyrics normalization. The chain of thought mode
// picks the instruction and decides who writes the score: an empty score slot
// hands the pen to the model, provided ABC ids place a composition of ours.
// The unconditional CFG stream keeps the instruction alone and drops the
// style and the lyrics, so guidance pulls toward the requested text. An
// exclude-styles line fills that empty style instead: the same [Tags] slot,
// lyrics still empty, so guidance pushes away from those styles. A blank
// exclude leaves the instruction-only stream as it was.
#pragma once

#include <string>
#include <vector>

// Special token ids (frozen text and ABC vocabulary)
static const int YUE2_EOD         = 151643;
static const int YUE2_ABC_START   = 151847;
static const int YUE2_ABC_END     = 151848;
static const int YUE2_MUSIC_START = 151851;
static const int YUE2_MUSIC_END   = 151852;

// Semantic code space inside the backbone vocab
static const int YUE2_CODEC_OFFSET = 151853;
static const int YUE2_CODEC_SIZE   = 32768;

// Context budget of the checkpoint, prefix plus generation
static const int YUE2_CONTEXT = 24576;

// Chain of thought mode: how the symbolic plan reaches the model
enum Yue2Cot {
    YUE2_COT_OFF,     // straight to codec tokens, no score
    YUE2_COT_MELODY,  // melody only score, free accompaniment
    YUE2_COT_FULL,    // chord annotated score
};

// The modes the protocol accepts, in the order an interface lists them. One
// table: the parser reads it, the server publishes it.
struct Yue2CotMode {
    const char * name;
    Yue2Cot      mode;
};

static const Yue2CotMode YUE2_COT_MODES[] = {
    { "full",   YUE2_COT_FULL   },
    { "melody", YUE2_COT_MELODY },
    { "off",    YUE2_COT_OFF    },
};

static bool yue2_cot_parse(const std::string & name, Yue2Cot * cot) {
    for (const Yue2CotMode & m : YUE2_COT_MODES) {
        if (name == m.name) {
            *cot = m.mode;
            return true;
        }
    }
    return false;
}

static const char * yue2_instruction(Yue2Cot cot) {
    if (cot == YUE2_COT_OFF) {
        return "Generate music with codec tokens from the given conditions.";
    }
    if (cot == YUE2_COT_MELODY) {
        return "Generate a melody-only ABC transcription without chord symbols, then generate music with codec "
               "tokens from the given conditions.";
    }
    return "Generate a chord-annotated ABC transcription, then generate music with codec tokens from the given "
           "conditions.";
}

// Conditioning text of the positive stream
static std::string yue2_request_text(Yue2Cot cot, const std::string & style, const std::string & lyrics) {
    return std::string(yue2_instruction(cot)) + "\n[Tags]\n" + style + "\n[Lyrics]\n" + lyrics + "\n";
}

// One style line, the way the page joins a box: each line trimmed, blanks
// dropped, the next line after a comma unless the previous already ends on
// punctuation. A blank box comes back empty.
static std::string yue2_style_line(const std::string & text) {
    std::string acc;
    size_t      i = 0, n = text.size();
    while (i < n) {
        size_t j = i;
        while (j < n && text[j] != '\n') {
            j++;
        }
        size_t a = i, b = j;
        if (b > a && text[b - 1] == '\r') {
            b--;
        }
        while (a < b && (text[a] == ' ' || text[a] == '\t')) {
            a++;
        }
        while (b > a && (text[b - 1] == ' ' || text[b - 1] == '\t')) {
            b--;
        }
        if (a < b) {
            if (!acc.empty()) {
                char last  = acc.back();
                bool punct = last == ',' || last == ';' || last == '.' || last == ':' || last == '!' || last == '?';
                acc += punct ? " " : ", ";
            }
            acc.append(text, a, b - a);
        }
        if (j < n) {
            j++;
        }
        i = j;
    }
    return acc;
}

// Assembles the conditional token stream. A null abc_ids in melody or full mode
// stops at ABC_START, which is the prefix the score generation starts from;
// pass the exact ids it produced to reach MUSIC_START.
template <typename Tokenize>
static std::vector<int> yue2_build_prompt_ids(Tokenize                 bpe_encode,
                                              Yue2Cot                  cot,
                                              const std::string &      style,
                                              const std::string &      lyrics,
                                              const std::vector<int> * abc_ids) {
    std::vector<int> ids;
    ids.push_back(YUE2_EOD);
    for (int id : bpe_encode(yue2_request_text(cot, style, lyrics))) {
        ids.push_back(id);
    }
    ids.push_back(YUE2_ABC_START);
    if (cot == YUE2_COT_OFF) {
        ids.push_back(YUE2_ABC_END);
        ids.push_back(YUE2_MUSIC_START);
        return ids;
    }
    if (!abc_ids) {
        return ids;
    }
    for (int id : *abc_ids) {
        ids.push_back(id);
    }
    ids.push_back(YUE2_ABC_END);
    ids.push_back(YUE2_MUSIC_START);
    return ids;
}

// Assembles the unconditional token stream. With no exclude styles: the
// instruction alone, then the same score. With exclude styles: that line in
// the [Tags] slot and an empty [Lyrics], the same text frame as a lyricless
// positive prompt, so guidance pushes the style away from those words. In
// off mode the score slot is absent entirely, unlike the conditional stream
// which carries an empty one, whether or not styles are excluded.
template <typename Tokenize>
static std::vector<int> yue2_build_negative_ids(Tokenize                 bpe_encode,
                                                Yue2Cot                  cot,
                                                const std::vector<int> * abc_ids,
                                                const std::string &      exclude = "") {
    std::string      line = yue2_style_line(exclude);
    std::vector<int> ids;
    ids.push_back(YUE2_EOD);
    const std::string body = line.empty() ? std::string(yue2_instruction(cot)) : yue2_request_text(cot, line, "");
    for (int id : bpe_encode(body)) {
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

// Guidance the protocol applies when the request leaves cfg_scale unset.
// HERESY 1031: 1.6 in every mode, Viktor's stable set (01.10.2026). The release had 1.01 in off
// mode and 1.0 (a single branch) otherwise; 1.6 keeps a second, unconditioned branch: half
// the speed of the music stage and 2.8 GB more cache a version at the full context.
static float yue2_default_guidance(Yue2Cot cot) {
    (void) cot;
    return 1.6f;
}

// Guidance of exactly 1, with no separate lyrics scale, is one branch. The
// excluded styles would not move a logit, so a caller must say they were not
// used instead of dropping them quietly. Empty when the exclude is blank, or
// when a branch will actually carry it.
static const char * const YUE2_EXCLUDE_UNUSED =
    "exclude_styles was not used: guidance 1.0 keeps a single branch";

static const char * yue2_exclude_unused(float cfg_scale, float cfg_lyrics, Yue2Cot cot, const std::string & exclude) {
    if (yue2_style_line(exclude).empty()) {
        return "";
    }
    const float guidance = cfg_scale < 0.0f ? yue2_default_guidance(cot) : cfg_scale;
    const float lyrics   = cfg_lyrics < 0.0f ? guidance : cfg_lyrics;
    if (guidance == 1.0f && lyrics == guidance) {
        return YUE2_EXCLUDE_UNUSED;
    }
    return "";
}
