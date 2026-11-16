// test-nar-seam.cpp: the long-song join, pure CPU, no model.
//
// The sound half hears one window. Past it, nar_plan_pieces / nar_plan_ladder choose even pieces whose
// windows fit, whose crossfades do not overlap, and whose last piece is not a stub. nar_seam_weight is the
// blend the pipeline applies. A constant latent must come back unchanged (the weights sum to 1), a frame
// outside a crossfade must equal the piece that owns it, and the blended length is the whole song.
//
// usage: test-nar-seam        (exit 0 when every case holds)

#include "nar-seam.h"

#include <cmath>
#include <cstdio>
#include <string>
#include <vector>

static int g_failed = 0;

static void check(bool ok, const char * name, const std::string & detail) {
    printf("[%s] %s%s%s\n", ok ? "PASS" : "FAIL", name, ok ? "" : ": ", ok ? "" : detail.c_str());
    g_failed += ok ? 0 : 1;
}

static int shortest_piece(int T, const NarPiecePlan & p) {
    int count = nar_piece_count(T, p.piece);
    int last  = T - (count - 1) * p.piece;
    return last < p.piece ? last : p.piece;
}

static bool windows_ok(int T, const NarPiecePlan & p, int window) {
    return p.piece >= 1 && nar_plan_windows_fit(T, p, window);
}

static void blend(int T, int dim, const NarPiecePlan & plan, const std::vector<float> & piece_value, std::vector<float> * out,
                  std::vector<float> * weights) {
    out->assign((size_t) T * (size_t) dim, 0.0f);
    weights->assign((size_t) T, 0.0f);
    int count = nar_piece_count(T, plan.piece);
    int xfade = plan.joined ? plan.xfade : 0;
    for (int i = 0; i < count; i++) {
        NarWindow w = nar_piece_window(T, plan.piece, i, plan.joined ? plan.margin : 0, plan.joined);
        std::vector<float> src((size_t) w.frames * (size_t) dim, piece_value[(size_t) i]);
        nar_seam_accumulate(out->data(), weights->data(), T, dim, w.start, w.nf, w.ws, w.frames, xfade, src.data());
    }
    nar_seam_finish(out->data(), weights->data(), T, dim);
}

static void check_cover(const char * name, int T, int window, const NarPiecePlan & p) {
    int count = nar_piece_count(T, p.piece);
    int last  = count > 0 ? T - (count - 1) * p.piece : 0;
    bool fit  = windows_ok(T, p, window);
    bool tail = !p.joined || (shortest_piece(T, p) >= 2 && (p.xfade < 1 || shortest_piece(T, p) >= 2 * p.xfade));
    bool fade = !p.joined || (p.xfade <= p.margin && p.xfade >= 1);
    float err = p.joined ? nar_seam_weight_error(T, p) : 0.0f;
    char buf[160];
    snprintf(buf, sizeof buf, "pieces %d stride %d last %d margin %d xfade %d err %.3g", count, p.piece, last, p.margin, p.xfade,
             (double) err);
    check(fit && tail && fade && err < 1.0e-4f && (p.joined || T <= window), name, buf);
}

int main() {
    const int context = 24576;
    const int long_prefix = 6000;
    const int short_prefix = 1000;
    int win_long  = nar_window_frames(context, long_prefix);    // (24576 - 6000 - 3) / 2 = 9286, about 6.2 min
    int win_short = nar_window_frames(context, short_prefix);   // 11786 frames, about 7.9 min
    check(win_long == (context - long_prefix - 3) / 2 && win_long == 9286, "long-prompt window is (context - prefix - 3) / 2",
          std::to_string(win_long));
    check(win_short == 11786, "short-prompt window", std::to_string(win_short));
    check(win_long / 25 < 8 * 60 && win_short / 25 < 8 * 60, "one window is under eight minutes at the full context",
          std::to_string(win_short / 25) + " s");

    NarPiecePlan inside = nar_plan_pieces(1000, win_long, YUE2_SEAM_MARGIN, YUE2_SEAM_XFADE, true);
    check(!inside.joined && inside.piece == win_long, "a song inside one window is not joined", std::to_string(inside.piece));

    const int eight = 8 * 60 * 25;   // 12000 frames, the default Max length
    NarPiecePlan long_song = nar_plan_pieces(eight, win_long, YUE2_SEAM_MARGIN, YUE2_SEAM_XFADE, true);
    check_cover("eight minutes past a long-prompt window", eight, win_long, long_song);
    check(long_song.joined && long_song.margin == YUE2_SEAM_MARGIN && long_song.xfade == YUE2_SEAM_XFADE,
          "the asked 6 s overlap and 4 s crossfade fit an eight-minute song",
          std::to_string(long_song.margin) + "/" + std::to_string(long_song.xfade));
    int count8 = nar_piece_count(eight, long_song.piece);
    int last8  = eight - (count8 - 1) * long_song.piece;
    check(count8 >= 2 && last8 >= long_song.piece - (count8 - 1) && last8 > eight / 10,
          "the last piece is not a stub of the first window", std::to_string(last8) + " of " + std::to_string(long_song.piece));

    int window = 4000;
    int just_over = window + 1;
    NarPiecePlan over = nar_plan_pieces(just_over, window, YUE2_SEAM_MARGIN, YUE2_SEAM_XFADE, true);
    check_cover("one frame past the window is two even pieces, not a one-frame tail", just_over, window, over);
    int last_over = just_over - (nar_piece_count(just_over, over.piece) - 1) * over.piece;
    check(over.joined && last_over > 100, "the tail is about half the song", std::to_string(last_over));

    // The old butt join of a small window left T % window frames, which can be 1.
    NarPiecePlan tiny = nar_plan_pieces(5001, 200, YUE2_SEAM_MARGIN, YUE2_SEAM_XFADE, true);
    check_cover("a 200-frame window still overlaps; the margin shrinks instead of butting a 1-frame tail", 5001, 200, tiny);
    check(tiny.joined && tiny.margin_shrunk && tiny.margin < YUE2_SEAM_MARGIN && shortest_piece(5001, tiny) > 1,
          "the overlap is shorter than 6 s and the last piece is longer than one frame",
          "margin " + std::to_string(tiny.margin) + " last " + std::to_string(shortest_piece(5001, tiny)));

    NarPiecePlan impossible = nar_plan_pieces(100, 3, YUE2_SEAM_MARGIN, YUE2_SEAM_XFADE, true);
    check(!impossible.joined && impossible.piece == 3, "a window of 3 frames cannot overlap and says so",
          std::to_string(impossible.piece));

    NarPiecePlan ladder = nar_plan_ladder(750, win_short, YUE2_LADDER_PIECE, YUE2_LADDER_MARGIN);
    check_cover("a 30 s song on the ladder is two pieces inside the window", 750, win_short, ladder);
    check(ladder.joined && ladder.margin == YUE2_LADDER_MARGIN && ladder.xfade == YUE2_LADDER_MARGIN / 2,
          "the ladder keeps its 2 s margin and 1 s crossfade", std::to_string(ladder.xfade));

    NarPiecePlan ladder_short = nar_plan_ladder(400, win_short, YUE2_LADDER_PIECE, YUE2_LADDER_MARGIN);
    check(!ladder_short.joined, "a song under one ladder piece stays one piece", "");

    NarPiecePlan ladder_tight = nar_plan_ladder(2000, 400, YUE2_LADDER_PIECE, YUE2_LADDER_MARGIN);
    check(!ladder_tight.joined, "ladder pieces that would overflow the window are not used",
          "piece " + std::to_string(ladder_tight.piece));
    NarPiecePlan ladder_fit = nar_plan_pieces(2000, 400, YUE2_LADDER_MARGIN, YUE2_LADDER_MARGIN / 2, true);
    check_cover("the same song joined with a margin that fits the 400-frame window", 2000, 400, ladder_fit);
    int overflow = 0;
    for (int i = 0; i < nar_piece_count(2000, ladder_fit.piece); i++) {
        NarWindow w = nar_piece_window(2000, ladder_fit.piece, i, ladder_fit.margin, true);
        if (w.frames > 400) {
            overflow++;
        }
    }
    check(overflow == 0, "no solved window is longer than the context window", std::to_string(overflow));

    // Three pieces, fades must not share a frame, every frame sums to 1.
    NarPiecePlan three = nar_plan_pieces(9000, 3500, YUE2_SEAM_MARGIN, YUE2_SEAM_XFADE, true);
    check_cover("three pieces: fades do not overlap and weights sum to 1", 9000, 3500, three);
    check(nar_piece_count(9000, three.piece) >= 3, "this window needs at least three pieces",
          std::to_string(nar_piece_count(9000, three.piece)));

    // Synthetic latents. Piece i is the constant 10+i on every channel. Exclusive frames keep it;
    // the centre of a seam is the mean; the length is the whole song; a constant field is unchanged.
    const int dim = 64;
    const int T   = 2400;
    NarPiecePlan synth = nar_plan_pieces(T, 1000, 80, 20, true);
    check_cover("synthetic plan", T, 1000, synth);
    int np = nar_piece_count(T, synth.piece);
    std::vector<float> values((size_t) np), flat((size_t) np, 0.5f);
    for (int i = 0; i < np; i++) {
        values[(size_t) i] = 10.0f + (float) i;
    }
    std::vector<float> mixed, weights, same, wsame;
    blend(T, dim, synth, values, &mixed, &weights);
    blend(T, dim, synth, flat, &same, &wsame);
    check((int) mixed.size() == T * dim && nar_seam_holes(weights.data(), T) == 0, "the blend is the whole song, no frame dropped",
          std::to_string(mixed.size()));
    int bad_const = 0;
    for (int t = 0; t < T; t++) {
        for (int k = 0; k < dim; k++) {
            if (fabsf(same[(size_t) t * dim + k] - 0.5f) > 1.0e-4f) {
                bad_const++;
            }
        }
    }
    check(bad_const == 0, "a constant latent is unchanged across the seams", std::to_string(bad_const));

    int bad_own = 0, seams_checked = 0, bad_mid = 0;
    for (int i = 0; i < np; i++) {
        int a = i * synth.piece;
        int b = a + synth.piece;
        if (b > T) {
            b = T;
        }
        int left  = a + (i == 0 ? 0 : synth.xfade);
        int right = b - (i + 1 == np ? 0 : synth.xfade);
        for (int t = left; t < right; t++) {
            for (int k = 0; k < dim; k++) {
                if (fabsf(mixed[(size_t) t * dim + k] - values[(size_t) i]) > 1.0e-3f) {
                    bad_own++;
                }
            }
        }
        if (i + 1 < np) {
            int edge = (i + 1) * synth.piece;
            seams_checked++;
            float expect = 0.5f * (values[(size_t) i] + values[(size_t) (i + 1)]);
            for (int k = 0; k < dim; k++) {
                if (fabsf(mixed[(size_t) edge * dim + k] - expect) > 1.0e-3f) {
                    bad_mid++;
                }
            }
            // Monotone: the outgoing weight falls across the crossfade, the incoming rises, and they sum to 1.
            float prev_out = 2.0f, prev_in = -1.0f;
            bool mono = true;
            for (int t = edge - synth.xfade; t <= edge + synth.xfade && t < T; t++) {
                float wo = nar_seam_weight(t, i * synth.piece, synth.piece, T, synth.xfade);
                int ns = (i + 1) * synth.piece;
                int nn = T - ns < synth.piece ? T - ns : synth.piece;
                float wi = nar_seam_weight(t, ns, nn, T, synth.xfade);
                if (wo > prev_out + 1.0e-4f || wi + 1.0e-4f < prev_in || fabsf(wo + wi - 1.0f) > 1.0e-4f) {
                    mono = false;
                }
                prev_out = wo;
                prev_in  = wi;
            }
            check(mono, "crossfade is complementary and monotone", "seam " + std::to_string(i));
        }
    }
    check(bad_own == 0, "outside the crossfade the latent is the piece that owns the frame", std::to_string(bad_own));
    check(seams_checked > 0 && bad_mid == 0, "the centre of each seam is the mean of the two windows", std::to_string(bad_mid));

    // Eight minutes of synthetic latents, the shape the pipeline writes (64-wide), weights only.
    check(nar_seam_weight_error(eight, long_song) < 1.0e-4f, "eight minutes of weights sum to 1 at every frame",
          std::to_string((double) nar_seam_weight_error(eight, long_song)));

    // A ladder does not seal its first window. Butted pieces (the seam turned off) still have to be read again.
    check(nar_reread_first_window(false, false, true, 3), "a butted ladder reads its first window again", "");
    check(nar_reread_first_window(false, false, true, 2), "a joined ladder reads its first window again", "");
    check(!nar_reread_first_window(false, false, true, 1), "one ladder piece keeps the cache", "");
    check(!nar_reread_first_window(false, false, false, 4), "a sealed seam stays in the cache", "");
    check(nar_reread_first_window(true, false, false, 4), "a replay reads the window again", "");

    // Left out follows the console. 9286 frames is one window at a 6000-token prefix; 12000 is the default 8:00.
    check(nar_pieces_effective(false, false, 12000, 9286), "left out, eight minutes is joined", "");
    check(!nar_pieces_effective(false, false, 4500, 9286), "left out, three minutes stays one window", "");
    check(!nar_pieces_effective(false, false, 9286, 9286), "left out, a song that fills the window stays one window", "");
    check(nar_pieces_effective(false, false, 9287, 9286), "left out, one frame past the window is joined", "");
    check(!nar_pieces_effective(false, false, 10000, 11786), "left out, 400 s fits a short prefix", "");
    check(!nar_pieces_effective(true, false, 12000, 9286), "an explicit false stays whole past the window", "");
    check(nar_pieces_effective(true, true, 4500, 9286), "an explicit true is pieces even when the song fits", "");

    printf("%s\n", g_failed ? "FAILED" : "all cases hold");
    return g_failed ? 1 : 0;
}
