#pragma once
// nar-seam.h: plan and blend a song the sound half cannot hear in one window.
//
// One window holds the prefix, the piece's codes and the latent block (two rows
// a frame): (context - prefix - 3) / 2 frames, about 6 to 7¾ minutes at the full
// 24,576-row context. A longer song is solved in even pieces. Each window hears
// `margin` frames past an inner edge, and neighbours meet in complementary
// half-cosines `xfade` frames either side of that edge. The piece count, the
// margin and the crossfade are chosen so that:
//   - every solved window fits in the context window (no graph overflow),
//   - the crossfade sits inside the overlap (xfade <= margin),
//   - two crossfades never share a frame (each piece is at least 2 * xfade),
//   - the last piece is not a stub (same bound), so the tail is not dropped,
//   - the weights of the windows that cover a frame sum to 1.
// A window too small for the asked margin gets a shorter overlap, not a butt
// join and not a one-frame tail. Only a window that cannot hold two frames plus
// a one-frame overlap falls back to a butt join.

#include <cmath>
#include <cstddef>
#include <vector>

static const int YUE2_LADDER_PIECE  = 500;   // about 20 s at 25 frames/s
static const int YUE2_LADDER_MARGIN = 50;    // 2 s past each edge
static const int YUE2_SEAM_MARGIN   = 150;   // 6 s
static const int YUE2_SEAM_XFADE    = 50;    // 2 s either side of the edge (4 s of crossfade)

struct NarPiecePlan {
    int  piece         = 0;      // stride, frames; the last piece may be shorter
    int  margin        = 0;      // frames a window hears past an inner edge (0: no overlap)
    int  xfade         = 0;      // frames of crossfade either side of the edge
    bool joined        = false;  // more than one piece, blended
    bool margin_shrunk = false;  // the asked margin did not fit; a shorter one did
};

struct NarWindow {
    int start  = 0;   // first frame of this piece
    int nf     = 0;   // frames of the piece itself
    int ws     = 0;   // first frame the window solves (start - margin, clamped)
    int frames = 0;   // solved length
};

inline int nar_window_frames(int context, int prefix) {
    int window = (context - prefix - 3) / 2;
    return window > 0 ? window : 0;
}

inline int nar_piece_count(int T_lat, int piece) {
    if (T_lat < 1 || piece < 1) {
        return 0;
    }
    return (T_lat + piece - 1) / piece;
}

inline NarWindow nar_piece_window(int T_lat, int piece, int index, int margin, bool joined) {
    NarWindow w;
    w.start = index * piece;
    if (w.start >= T_lat || piece < 1) {
        return w;
    }
    int remain = T_lat - w.start;
    w.nf       = remain < piece ? remain : piece;
    if (!joined || margin <= 0) {
        w.ws     = w.start;
        w.frames = w.nf;
        return w;
    }
    w.ws     = w.start > margin ? w.start - margin : 0;
    int end  = w.start + w.nf + margin;
    if (end > T_lat) {
        end = T_lat;
    }
    w.frames = end - w.ws;
    return w;
}

inline bool nar_plan_windows_fit(int T_lat, const NarPiecePlan & plan, int window) {
    int count = nar_piece_count(T_lat, plan.piece);
    if (count < 1) {
        return false;
    }
    for (int i = 0; i < count; i++) {
        NarWindow w = nar_piece_window(T_lat, plan.piece, i, plan.margin, plan.joined);
        if (w.nf < 1 || w.frames < 1 || w.frames > window) {
            return false;
        }
    }
    return true;
}

// Smallest n whose pieces fit. `side` is 1 for two pieces (margin on the one
// inner edge) and 2 once a middle piece hears margin on both sides.
inline NarPiecePlan nar_plan_pieces(int T_lat, int window, int want_margin, int want_xfade, bool even_pieces) {
    NarPiecePlan butt;
    butt.piece = window > 0 ? window : 1;
    if (T_lat < 1 || window < 1 || T_lat <= window) {
        return butt;
    }
    int margin_cap = want_margin > 0 ? want_margin : 1;
    if (margin_cap > window - 2) {
        margin_cap = window - 2;
    }
    for (int margin = margin_cap; margin >= 1; --margin) {
        int n     = 2;
        int guard = 0;
        while (n <= T_lat / 2 && guard++ < T_lat) {
            int even = (T_lat + n - 1) / n;
            int side = n > 2 ? 2 : 1;
            if (even >= 2 && even + side * margin <= window) {
                int piece = even;
                if (!even_pieces && n == 2) {
                    int front = window - margin;
                    int tail  = T_lat - front;
                    if (front >= 2 && tail >= 2 && front < T_lat) {
                        piece = front;
                    }
                }
                int count = nar_piece_count(T_lat, piece);
                if (count < 2) {
                    n++;
                    continue;
                }
                int last = T_lat - (count - 1) * piece;
                int shortest = last < piece ? last : piece;
                if (shortest < 2 || last < 1) {
                    n++;
                    continue;
                }
                int xfade = want_xfade > 0 ? want_xfade : 1;
                if (xfade > margin) {
                    xfade = margin;
                }
                if (xfade > shortest / 2) {
                    xfade = shortest / 2;
                }
                if (xfade < 1) {
                    xfade = 0;
                }
                NarPiecePlan p;
                p.piece         = piece;
                p.margin        = margin;
                p.xfade         = xfade;
                p.joined        = true;
                p.margin_shrunk = margin < (want_margin > 0 ? want_margin : 1);
                if (nar_plan_windows_fit(T_lat, p, window)) {
                    return p;
                }
            }
            int room = window - side * margin;
            if (room < 2) {
                break;
            }
            int need = (T_lat + room - 1) / room;
            int next = need > n ? need : n + 1;
            if (next <= n) {
                break;
            }
            n = next;
        }
    }
    return butt;
}

// Ladder: even pieces of about `target` frames, each hearing `margin` past both
// edges, when that window fits. When it does not (a small context, a long
// song), the caller uses nar_plan_pieces — a full-window piece plus the margin
// would overflow the graph.
inline NarPiecePlan nar_plan_ladder(int T_lat, int window, int target, int margin) {
    NarPiecePlan butt;
    butt.piece = window > 0 ? window : 1;
    if (T_lat < 1 || window < 1 || target < 1 || T_lat <= target) {
        return butt;
    }
    int n    = (T_lat + target - 1) / target;
    int even = n > 0 ? (T_lat + n - 1) / n : T_lat;
    if (n > 1 && even >= 2 && margin >= 0 && even + 2 * margin <= window) {
        int count    = nar_piece_count(T_lat, even);
        int last     = count > 0 ? T_lat - (count - 1) * even : 0;
        int shortest = last < even ? last : even;
        NarPiecePlan p;
        p.piece  = even;
        p.margin = margin;
        p.xfade  = margin / 2;
        if (p.xfade > shortest / 2) {
            p.xfade = shortest / 2;
        }
        if (p.xfade < 1 && margin > 0 && shortest >= 2) {
            p.xfade = 1;
        }
        p.joined = true;
        if (count > 1 && shortest >= 2 && p.xfade <= shortest / 2 && nar_plan_windows_fit(T_lat, p, window)) {
            return p;
        }
    }
    return butt;
}

// The first sound window is already in the cache when the music half sealed it
// (kept == the window length). A ladder never seals: every piece, joined or
// butted, is read again, or the first window attends the next music code
// instead of the end token. A sealed seam (not a ladder) stays in the cache.
// Replay and sliders also read the window again; the cache is not theirs.
inline bool nar_reread_first_window(bool replay, bool sliders, bool ladder, int chunks) {
    if (replay || sliders) {
        return true;
    }
    return ladder && chunks > 1;
}

// What nar_pieces means once the request has been read.
// Said (set): the caller's bool. true joins; false holds the music to one window.
// Left out: the console's default. The asked length past one sound window is joined;
// a length that fits stays one window. want_frames is the duration cap when it is
// lower than the stage cap, else the stage cap. An old client that omits the field
// and asks past the window is no longer cut there.
inline bool nar_pieces_effective(bool set, bool value, int want_frames, int window_frames) {
    if (set) {
        return value;
    }
    if (window_frames < 1) {
        return want_frames > 0;
    }
    return want_frames > window_frames;
}

inline float nar_seam_ramp(float u) {
    if (u <= 0.0f) {
        return 0.0f;
    }
    if (u >= 1.0f) {
        return 1.0f;
    }
    return 0.5f - 0.5f * cosf(3.14159265f * u);
}

// Weight of the piece that owns [start, start+nf) at frame t.
// xfade < 1: the piece alone, a hard cut at its edges.
// Otherwise complementary half-cosines centred on the piece's edges.
inline float nar_seam_weight(int t, int start, int nf, int T_lat, int xfade) {
    if (t < 0 || t >= T_lat || nf < 1) {
        return 0.0f;
    }
    if (xfade < 1) {
        return (t >= start && t < start + nf) ? 1.0f : 0.0f;
    }
    float up = 1.0f;
    if (start > 0) {
        up = nar_seam_ramp((float) (t - (start - xfade)) / (float) (2 * xfade));
    }
    float down = 1.0f;
    if (start + nf < T_lat) {
        down = 1.0f - nar_seam_ramp((float) (t - (start + nf - xfade)) / (float) (2 * xfade));
    }
    float w = up * down;
    return w > 0.0f ? w : 0.0f;
}

// src is the solved window, time-major, `frames` * dim, starting at frame ws.
inline void nar_seam_accumulate(float * dst, float * weights, int T_lat, int dim, int start, int nf, int ws,
                                int frames, int xfade, const float * src) {
    if (!dst || !weights || !src || dim < 1 || frames < 1) {
        return;
    }
    int end = ws + frames;
    if (end > T_lat) {
        end = T_lat;
    }
    for (int t = ws; t < end; t++) {
        if (t < 0) {
            continue;
        }
        float w = nar_seam_weight(t, start, nf, T_lat, xfade);
        if (w <= 0.0f) {
            continue;
        }
        weights[t] += w;
        float *       d = dst + (size_t) t * (size_t) dim;
        const float * s = src + (size_t) (t - ws) * (size_t) dim;
        for (int k = 0; k < dim; k++) {
            d[k] += w * s[k];
        }
    }
}

// Scale a frame whose windows summed to something other than 1. A sum of 0 is a
// hole: left as written (zeros) so the caller can see it. A sum of 1 is left
// bit for bit, which is the steady case when the fades do not overlap.
inline void nar_seam_finish(float * dst, const float * weights, int T_lat, int dim) {
    if (!dst || !weights || dim < 1) {
        return;
    }
    for (int t = 0; t < T_lat; t++) {
        float s = weights[t];
        if (!(s > 1.0e-5f) || fabsf(s - 1.0f) <= 1.0e-5f) {
            continue;
        }
        float inv = 1.0f / s;
        float * d = dst + (size_t) t * (size_t) dim;
        for (int k = 0; k < dim; k++) {
            d[k] *= inv;
        }
    }
}

inline int nar_seam_holes(const float * weights, int T_lat) {
    int holes = 0;
    for (int t = 0; t < T_lat; t++) {
        if (!(weights[t] > 1.0e-4f)) {
            holes++;
        }
    }
    return holes;
}

// Worst |sum - 1| over the song, looking only at frames the windows actually
// write (the same frames nar_seam_accumulate touches).
inline float nar_seam_weight_error(int T_lat, const NarPiecePlan & plan) {
    int count = nar_piece_count(T_lat, plan.piece);
    if (count < 1 || T_lat < 1) {
        return 1.0f;
    }
    float worst = 0.0f;
    std::vector<float> w((size_t) T_lat, 0.0f);
    int xfade = plan.joined ? plan.xfade : 0;
    int margin = plan.joined ? plan.margin : 0;
    for (int i = 0; i < count; i++) {
        NarWindow win = nar_piece_window(T_lat, plan.piece, i, margin, plan.joined);
        int end = win.ws + win.frames;
        if (end > T_lat) {
            end = T_lat;
        }
        for (int t = win.ws; t < end; t++) {
            w[(size_t) t] += nar_seam_weight(t, win.start, win.nf, T_lat, xfade);
        }
    }
    for (int t = 0; t < T_lat; t++) {
        float e = fabsf(w[(size_t) t] - 1.0f);
        if (e > worst) {
            worst = e;
        }
    }
    return worst;
}
