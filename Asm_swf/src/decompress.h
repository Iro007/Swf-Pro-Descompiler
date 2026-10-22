#ifndef ASMSWF_DECOMPRESS_H
#define ASMSWF_DECOMPRESS_H

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

/* ============================================================================
 * Self-contained RFC-1950 / RFC-1951 Zlib/DEFLATE decompressor for CWS SWFs
 * ============================================================================ */

typedef struct {
    const uint8_t *src;
    size_t src_len;
    size_t byte_pos;
    uint32_t bit_buf;
    int bit_cnt;
} DeflateStream;

typedef struct {
    uint16_t counts[16];
    uint16_t symbols[288];
} HuffTable;

static inline uint32_t deflate_get_bits(DeflateStream *s, int n) {
    while (s->bit_cnt < n) {
        uint8_t b = (s->byte_pos < s->src_len) ? s->src[s->byte_pos++] : 0;
        s->bit_buf |= ((uint32_t)b) << s->bit_cnt;
        s->bit_cnt += 8;
    }
    uint32_t val = s->bit_buf & ((1u << n) - 1u);
    s->bit_buf >>= n;
    s->bit_cnt -= n;
    return val;
}

static void huff_build(HuffTable *h, const uint8_t *lengths, int num) {
    memset(h->counts, 0, sizeof(h->counts));
    for (int i = 0; i < num; i++) {
        if (lengths[i] < 16) h->counts[lengths[i]]++;
    }
    h->counts[0] = 0;
    uint16_t offs[16];
    uint16_t sum = 0;
    for (int i = 1; i < 16; i++) {
        offs[i] = sum;
        sum += h->counts[i];
    }
    for (int i = 0; i < num; i++) {
        if (lengths[i] != 0) {
            h->symbols[offs[lengths[i]]++] = (uint16_t)i;
        }
    }
}

static int huff_decode(DeflateStream *s, const HuffTable *h) {
    int code = 0, first = 0, index = 0;
    for (int len = 1; len < 16; len++) {
        code |= (int)deflate_get_bits(s, 1);
        int count = h->counts[len];
        if (code - count < first) {
            return h->symbols[index + (code - first)];
        }
        index += count;
        first += count;
        first <<= 1;
        code <<= 1;
    }
    return -1;
}

static uint8_t *zlib_decompress_cws(const uint8_t *src, size_t src_len, size_t expected_cap, size_t *out_len) {
    if (src_len < 2) return NULL;
    uint8_t cmf = src[0], flg = src[1];
    if ((cmf & 0x0F) != 8 || ((cmf << 8) + flg) % 31 != 0) return NULL;
    size_t offset = 2;
    if (flg & 0x20) {
        if (src_len < 6) return NULL;
        offset += 4;
    }
    size_t cap = expected_cap > 64 ? expected_cap : 4096;
    uint8_t *out = (uint8_t *)malloc(cap);
    if (!out) return NULL;
    size_t pos = 0;

    DeflateStream ds = { src, src_len, offset, 0, 0 };
    static const uint16_t len_base[29] = {
        3,4,5,6,7,8,9,10,11,13,15,17,19,23,27,31,35,43,51,59,67,83,99,115,131,163,195,227,258
    };
    static const uint8_t len_extra[29] = {
        0,0,0,0,0,0,0,0,1,1,1,1,2,2,2,2,3,3,3,3,4,4,4,4,5,5,5,5,0
    };
    static const uint16_t dist_base[30] = {
        1,2,3,4,5,7,9,13,17,25,33,49,65,97,129,193,257,385,513,769,1025,1537,2049,3073,4097,6145,8193,12289,16385,24577
    };
    static const uint8_t dist_extra[30] = {
        0,0,0,0,1,1,2,2,3,3,4,4,5,5,6,6,7,7,8,8,9,9,10,10,11,11,12,12,13,13
    };
    static const uint8_t clen_order[19] = {
        16,17,18,0,8,7,9,6,10,5,11,4,12,3,13,2,14,1,15
    };

    int bfinal = 0;
    while (!bfinal) {
        if (ds.byte_pos >= ds.src_len && ds.bit_cnt == 0) break;
        bfinal = (int)deflate_get_bits(&ds, 1);
        int btype = (int)deflate_get_bits(&ds, 2);
        if (btype == 0) {
            ds.bit_buf = 0;
            ds.bit_cnt = 0;
            if (ds.byte_pos + 4 > ds.src_len) { free(out); return NULL; }
            uint16_t len = (uint16_t)(ds.src[ds.byte_pos] | (ds.src[ds.byte_pos + 1] << 8));
            uint16_t nlen = (uint16_t)(ds.src[ds.byte_pos + 2] | (ds.src[ds.byte_pos + 3] << 8));
            ds.byte_pos += 4;
            if ((uint16_t)~len != nlen || ds.byte_pos + len > ds.src_len) { free(out); return NULL; }
            if (pos + len > cap) {
                while (pos + len > cap) cap *= 2;
                uint8_t *nb = (uint8_t *)realloc(out, cap);
                if (!nb) { free(out); return NULL; }
                out = nb;
            }
            memcpy(out + pos, ds.src + ds.byte_pos, len);
            pos += len;
            ds.byte_pos += len;
        } else if (btype == 1 || btype == 2) {
            HuffTable lit_table, dist_table;
            if (btype == 1) {
                uint8_t lengths[288];
                for (int i = 0; i < 144; i++) lengths[i] = 8;
                for (int i = 144; i < 256; i++) lengths[i] = 9;
                for (int i = 256; i < 280; i++) lengths[i] = 7;
                for (int i = 280; i < 288; i++) lengths[i] = 8;
                huff_build(&lit_table, lengths, 288);
                uint8_t dlengths[30];
                for (int i = 0; i < 30; i++) dlengths[i] = 5;
                huff_build(&dist_table, dlengths, 30);
            } else {
                int hlit = (int)deflate_get_bits(&ds, 5) + 257;
                int hdist = (int)deflate_get_bits(&ds, 5) + 1;
                int hclen = (int)deflate_get_bits(&ds, 4) + 4;
                uint8_t clengths[19];
                memset(clengths, 0, sizeof(clengths));
                for (int i = 0; i < hclen; i++) {
                    clengths[clen_order[i]] = (uint8_t)deflate_get_bits(&ds, 3);
                }
                HuffTable code_table;
                huff_build(&code_table, clengths, 19);
                uint8_t lengths[320];
                memset(lengths, 0, sizeof(lengths));
                int idx = 0;
                while (idx < hlit + hdist) {
                    int sym = huff_decode(&ds, &code_table);
                    if (sym < 0) { free(out); return NULL; }
                    if (sym < 16) {
                        lengths[idx++] = (uint8_t)sym;
                    } else if (sym == 16) {
                        if (idx == 0) { free(out); return NULL; }
                        int rep = (int)deflate_get_bits(&ds, 2) + 3;
                        uint8_t prev = lengths[idx - 1];
                        while (rep-- > 0 && idx < hlit + hdist) lengths[idx++] = prev;
                    } else if (sym == 17) {
                        int rep = (int)deflate_get_bits(&ds, 3) + 3;
                        while (rep-- > 0 && idx < hlit + hdist) lengths[idx++] = 0;
                    } else if (sym == 18) {
                        int rep = (int)deflate_get_bits(&ds, 7) + 11;
                        while (rep-- > 0 && idx < hlit + hdist) lengths[idx++] = 0;
                    }
                }
                huff_build(&lit_table, lengths, hlit);
                huff_build(&dist_table, lengths + hlit, hdist);
            }
            for (;;) {
                int sym = huff_decode(&ds, &lit_table);
                if (sym < 0) { free(out); return NULL; }
                if (sym < 256) {
                    if (pos + 1 > cap) {
                        cap *= 2;
                        uint8_t *nb = (uint8_t *)realloc(out, cap);
                        if (!nb) { free(out); return NULL; }
                        out = nb;
                    }
                    out[pos++] = (uint8_t)sym;
                } else if (sym == 256) {
                    break;
                } else {
                    sym -= 257;
                    if (sym >= 29) { free(out); return NULL; }
                    int length = len_base[sym] + (int)deflate_get_bits(&ds, len_extra[sym]);
                    int dsym = huff_decode(&ds, &dist_table);
                    if (dsym < 0 || dsym >= 30) { free(out); return NULL; }
                    size_t dist = dist_base[dsym] + (size_t)deflate_get_bits(&ds, dist_extra[dsym]);
                    if (dist > pos) { free(out); return NULL; }
                    if (pos + length > cap) {
                        while (pos + length > cap) cap *= 2;
                        uint8_t *nb = (uint8_t *)realloc(out, cap);
                        if (!nb) { free(out); return NULL; }
                        out = nb;
                    }
                    for (int i = 0; i < length; i++) {
                        out[pos] = out[pos - dist];
                        pos++;
                    }
                }
            }
        } else {
            free(out);
            return NULL;
        }
    }
    *out_len = pos;
    return out;
}

/* ============================================================================
 * Self-contained LZMA1 decoder for ZWS SWFs
 * ============================================================================ */

typedef struct {
    const uint8_t *buf;
    size_t len;
    size_t pos;
    uint32_t range;
    uint32_t code;
} LzmaRangeDec;

static inline uint8_t lzma_rd_byte(LzmaRangeDec *rd) {
    return (rd->pos < rd->len) ? rd->buf[rd->pos++] : 0;
}

static inline void lzma_rd_norm(LzmaRangeDec *rd) {
    if (rd->range < 0x01000000u) {
        rd->range <<= 8;
        rd->code = (rd->code << 8) | lzma_rd_byte(rd);
    }
}

static inline uint32_t lzma_rd_bit(LzmaRangeDec *rd, uint16_t *prob) {
    lzma_rd_norm(rd);
    uint32_t p = *prob;
    uint32_t bound = (rd->range >> 11) * p;
    if (rd->code < bound) {
        rd->range = bound;
        *prob = (uint16_t)(p + ((2048u - p) >> 5));
        return 0;
    } else {
        rd->range -= bound;
        rd->code -= bound;
        *prob = (uint16_t)(p - (p >> 5));
        return 1;
    }
}

static uint32_t lzma_rd_bittree(LzmaRangeDec *rd, uint16_t *probs, int num_bits) {
    uint32_t m = 1;
    for (int i = 0; i < num_bits; i++) {
        m = (m << 1) | lzma_rd_bit(rd, &probs[m]);
    }
    return m - (1u << num_bits);
}

static uint32_t lzma_rd_rev_bittree(LzmaRangeDec *rd, uint16_t *probs, int num_bits) {
    uint32_t m = 1, sym = 0;
    for (int i = 0; i < num_bits; i++) {
        uint32_t bit = lzma_rd_bit(rd, &probs[m]);
        m = (m << 1) | bit;
        sym |= bit << i;
    }
    return sym;
}

static uint32_t lzma_rd_direct(LzmaRangeDec *rd, int num_bits) {
    uint32_t res = 0;
    for (int i = 0; i < num_bits; i++) {
        lzma_rd_norm(rd);
        rd->range >>= 1;
        rd->code -= rd->range;
        uint32_t t = 0u - (rd->code >> 31);
        rd->code += rd->range & t;
        res = (res << 1) | (1u - (t & 1u));
    }
    return res;
}

static uint32_t lzma_decode_len(LzmaRangeDec *rd, uint16_t *choice, uint16_t *low, uint16_t *mid, uint16_t *high, int pos_state) {
    if (lzma_rd_bit(rd, &choice[0]) == 0) {
        return lzma_rd_bittree(rd, &low[pos_state * 8], 3);
    }
    if (lzma_rd_bit(rd, &choice[1]) == 0) {
        return 8u + lzma_rd_bittree(rd, &mid[pos_state * 8], 3);
    }
    return 16u + lzma_rd_bittree(rd, high, 8);
}

static uint8_t *lzma_decompress_zws(const uint8_t *zws, size_t zws_len, size_t expected_len) {
    if (zws_len < 17) return NULL;
    uint8_t props = zws[12];
    if (props >= 9 * 5 * 5) return NULL;
    int lc = props % 9;
    int rem = props / 9;
    int lp = rem % 5;
    int pb = rem / 5;
    int pos_states = 1 << pb;
    uint32_t pos_state_mask = (uint32_t)(pos_states - 1);
    uint32_t lit_pos_mask = (uint32_t)((1 << lp) - 1);
    size_t num_lit_probs = (size_t)0x300 << (lc + lp);

    uint16_t *lit_probs = (uint16_t *)malloc(num_lit_probs * sizeof(uint16_t));
    uint8_t *out = (uint8_t *)malloc(expected_len > 0 ? expected_len : 1);
    if (!lit_probs || !out) { free(lit_probs); free(out); return NULL; }
    for (size_t i = 0; i < num_lit_probs; i++) lit_probs[i] = 1024;

    uint16_t is_match[12 * 16], is_rep[12], is_rep_g0[12], is_rep_g1[12], is_rep_g2[12], is_rep0_long[12 * 16];
    uint16_t pos_slot[4 * 64], pos_decoders[115], align_decoder[16];
    uint16_t len_choice[2], len_low[16 * 8], len_mid[16 * 8], len_high[256];
    uint16_t rep_choice[2], rep_low[16 * 8], rep_mid[16 * 8], rep_high[256];

    #define INIT_ARR(a) for (size_t _i = 0; _i < sizeof(a)/sizeof((a)[0]); _i++) (a)[_i] = 1024
    INIT_ARR(is_match); INIT_ARR(is_rep); INIT_ARR(is_rep_g0); INIT_ARR(is_rep_g1); INIT_ARR(is_rep_g2); INIT_ARR(is_rep0_long);
    INIT_ARR(pos_slot); INIT_ARR(pos_decoders); INIT_ARR(align_decoder);
    INIT_ARR(len_choice); INIT_ARR(len_low); INIT_ARR(len_mid); INIT_ARR(len_high);
    INIT_ARR(rep_choice); INIT_ARR(rep_low); INIT_ARR(rep_mid); INIT_ARR(rep_high);
    #undef INIT_ARR

    LzmaRangeDec rd = { zws, zws_len, 18, 0xFFFFFFFFu, 0 };
    for (int i = 0; i < 4; i++) rd.code = (rd.code << 8) | lzma_rd_byte(&rd);

    size_t out_pos = 0;
    int state = 0;
    uint32_t rep0 = 0, rep1 = 0, rep2 = 0, rep3 = 0;
    uint8_t prev_byte = 0;

    while (out_pos < expected_len) {
        int pos_state = (int)((uint32_t)out_pos & pos_state_mask);
        if (lzma_rd_bit(&rd, &is_match[state * pos_states + pos_state]) == 0) {
            uint32_t lit_state = (((uint32_t)out_pos & lit_pos_mask) << lc) | ((uint32_t)prev_byte >> (8 - lc));
            uint16_t *probs = &lit_probs[(size_t)lit_state * 0x300];
            uint32_t symbol = 1;
            if (state >= 7) {
                uint8_t match_byte = (out_pos >= rep0 + 1) ? out[out_pos - rep0 - 1] : 0;
                while (symbol < 0x100) {
                    uint32_t match_bit = (match_byte >> 7) & 1;
                    match_byte <<= 1;
                    uint32_t bit = lzma_rd_bit(&rd, &probs[((1 + match_bit) << 8) + symbol]);
                    symbol = (symbol << 1) | bit;
                    if (match_bit != bit) break;
                }
            }
            while (symbol < 0x100) {
                symbol = (symbol << 1) | lzma_rd_bit(&rd, &probs[symbol]);
            }
            prev_byte = (uint8_t)(symbol & 0xFF);
            out[out_pos++] = prev_byte;
            state = (state < 4) ? 0 : (state < 10 ? state - 3 : state - 6);
        } else {
            uint32_t length = 0;
            if (lzma_rd_bit(&rd, &is_rep[state]) == 1) {
                if (lzma_rd_bit(&rd, &is_rep_g0[state]) == 0) {
                    if (lzma_rd_bit(&rd, &is_rep0_long[state * pos_states + pos_state]) == 0) {
                        state = (state < 7) ? 9 : 11;
                        if (out_pos < rep0 + 1) { free(lit_probs); free(out); return NULL; }
                        prev_byte = out[out_pos - rep0 - 1];
                        out[out_pos++] = prev_byte;
                        continue;
                    }
                } else {
                    uint32_t dist;
                    if (lzma_rd_bit(&rd, &is_rep_g1[state]) == 0) {
                        dist = rep1;
                    } else {
                        if (lzma_rd_bit(&rd, &is_rep_g2[state]) == 0) {
                            dist = rep2;
                        } else {
                            dist = rep3;
                            rep3 = rep2;
                        }
                        rep2 = rep1;
                    }
                    rep1 = rep0;
                    rep0 = dist;
                }
                length = lzma_decode_len(&rd, rep_choice, rep_low, rep_mid, rep_high, pos_state) + 2;
                state = (state < 7) ? 8 : 11;
            } else {
                rep3 = rep2;
                rep2 = rep1;
                rep1 = rep0;
                length = lzma_decode_len(&rd, len_choice, len_low, len_mid, len_high, pos_state) + 2;
                int len_state = (length - 2 < 3) ? (int)(length - 2) : 3;
                uint32_t slot = lzma_rd_bittree(&rd, &pos_slot[len_state * 64], 6);
                if (slot >= 4) {
                    int num_direct = (int)((slot >> 1) - 1);
                    uint32_t dist = (2u | (slot & 1u)) << num_direct;
                    if (slot < 14) {
                        dist += lzma_rd_rev_bittree(&rd, &pos_decoders[dist - slot], num_direct);
                    } else {
                        dist += lzma_rd_direct(&rd, num_direct - 4) << 4;
                        dist += lzma_rd_rev_bittree(&rd, align_decoder, 4);
                    }
                    rep0 = dist;
                } else {
                    rep0 = slot;
                }
                if (rep0 == 0xFFFFFFFFu) break;
                state = (state < 7) ? 7 : 10;
            }
            size_t dist = (size_t)rep0 + 1;
            if (dist > out_pos) { free(lit_probs); free(out); return NULL; }
            for (uint32_t i = 0; i < length && out_pos < expected_len; i++) {
                prev_byte = out[out_pos - dist];
                out[out_pos++] = prev_byte;
            }
        }
    }
    free(lit_probs);
    return out;
}

#endif
