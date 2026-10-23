#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <math.h>
#ifdef _WIN32
#include <windows.h>
#include <io.h>
#include <fcntl.h>
#endif

#include "decompress.h"

/* ============================================================================
 * External x86_64 GNU Assembly hot-path routines (swf_fast.S)
 * ============================================================================ */
extern uint64_t asm_swf_read_tag_header(
    const uint8_t *buf, size_t pos, size_t end,
    uint16_t *out_code, uint32_t *out_len, uint32_t *out_long
);
extern size_t asm_avm2_read_u30(
    const uint8_t *buf, size_t pos, size_t end, uint32_t *out_val
);
extern size_t asm_avm1_scan_action(
    const uint8_t *buf, size_t pos, size_t end,
    uint8_t *out_code, uint16_t *out_len
);
extern int asm_bit_read_u32(
    const uint8_t *buf, size_t byte_len, size_t *bit_pos,
    uint32_t count, uint32_t *out_val
);
extern void asm_sha256_transform(uint32_t state[8], const uint8_t block[64]);

/* ============================================================================
 * SHA-256 wrapper backed by asm_sha256_transform
 * ============================================================================ */
static void compute_sha256_hex(const uint8_t *data, size_t len, char out_hex[65]) {
    uint32_t state[8] = {
        0x6a09e667u, 0xbb67ae85u, 0x3c6ef372u, 0xa54ff53au,
        0x510e527fu, 0x9b05688cu, 0x1f83d9abu, 0x5be0cd19u
    };
    size_t offset = 0;
    while (offset + 64 <= len) {
        asm_sha256_transform(state, data + offset);
        offset += 64;
    }
    uint8_t block[64];
    memset(block, 0, 64);
    size_t rem = len - offset;
    if (rem > 0) memcpy(block, data + offset, rem);
    block[rem] = 0x80;
    if (rem >= 56) {
        asm_sha256_transform(state, block);
        memset(block, 0, 64);
    }
    uint64_t bit_len = (uint64_t)len * 8u;
    for (int i = 0; i < 8; i++) {
        block[63 - i] = (uint8_t)(bit_len >> (i * 8));
    }
    asm_sha256_transform(state, block);

    static const char hex_chars[] = "0123456789abcdef";
    for (int i = 0; i < 8; i++) {
        uint32_t w = state[i];
        out_hex[i * 8 + 0] = hex_chars[(w >> 28) & 0xF];
        out_hex[i * 8 + 1] = hex_chars[(w >> 24) & 0xF];
        out_hex[i * 8 + 2] = hex_chars[(w >> 20) & 0xF];
        out_hex[i * 8 + 3] = hex_chars[(w >> 16) & 0xF];
        out_hex[i * 8 + 4] = hex_chars[(w >> 12) & 0xF];
        out_hex[i * 8 + 5] = hex_chars[(w >> 8) & 0xF];
        out_hex[i * 8 + 6] = hex_chars[(w >> 4) & 0xF];
        out_hex[i * 8 + 7] = hex_chars[w & 0xF];
    }
    out_hex[64] = '\0';
}

/* ============================================================================
 * Dynamic String Builder
 * ============================================================================ */
typedef struct {
    char *buf;
    size_t len;
    size_t cap;
} StrBuf;

static void sb_init(StrBuf *sb) {
    sb->cap = 1024;
    sb->len = 0;
    sb->buf = (char *)malloc(sb->cap);
    if (sb->buf) sb->buf[0] = '\0';
}

static void sb_free(StrBuf *sb) {
    free(sb->buf);
    sb->buf = NULL;
    sb->len = 0;
    sb->cap = 0;
}

static void sb_reserve(StrBuf *sb, size_t extra) {
    if (sb->len + extra + 1 > sb->cap) {
        while (sb->len + extra + 1 > sb->cap) sb->cap *= 2;
        sb->buf = (char *)realloc(sb->buf, sb->cap);
    }
}

static void sb_append_n(StrBuf *sb, const char *s, size_t n) {
    sb_reserve(sb, n);
    memcpy(sb->buf + sb->len, s, n);
    sb->len += n;
    sb->buf[sb->len] = '\0';
}

static void sb_append(StrBuf *sb, const char *s) {
    if (!s) return;
    sb_append_n(sb, s, strlen(s));
}

static void sb_append_char(StrBuf *sb, char c) {
    sb_reserve(sb, 1);
    sb->buf[sb->len++] = c;
    sb->buf[sb->len] = '\0';
}

/* UTF-8 lossy sanitization (matches Rust String::from_utf8_lossy / Node Buffer.toString('utf8')) */
static char *utf8_lossy_dup(const uint8_t *src, size_t len) {
    StrBuf sb;
    sb_init(&sb);
    size_t i = 0;
    while (i < len) {
        uint8_t c = src[i];
        if (c < 0x80) {
            sb_append_char(&sb, (char)c);
            i++;
        } else if ((c & 0xE0) == 0xC0 && i + 1 < len && (src[i + 1] & 0xC0) == 0x80) {
            uint32_t cp = ((uint32_t)(c & 0x1F) << 6) | (src[i + 1] & 0x3F);
            if (cp >= 0x80) {
                sb_append_n(&sb, (const char *)(src + i), 2);
                i += 2;
            } else {
                sb_append_n(&sb, "\xEF\xBF\xBD", 3);
                i++;
            }
        } else if ((c & 0xF0) == 0xE0 && i + 2 < len && (src[i + 1] & 0xC0) == 0x80 && (src[i + 2] & 0xC0) == 0x80) {
            uint32_t cp = ((uint32_t)(c & 0x0F) << 12) | ((uint32_t)(src[i + 1] & 0x3F) << 6) | (src[i + 2] & 0x3F);
            if (cp >= 0x800 && !(cp >= 0xD800 && cp <= 0xDFFF)) {
                sb_append_n(&sb, (const char *)(src + i), 3);
                i += 3;
            } else {
                sb_append_n(&sb, "\xEF\xBF\xBD", 3);
                i++;
            }
        } else if ((c & 0xF8) == 0xF0 && i + 3 < len && (src[i + 1] & 0xC0) == 0x80 && (src[i + 2] & 0xC0) == 0x80 && (src[i + 3] & 0xC0) == 0x80) {
            uint32_t cp = ((uint32_t)(c & 0x07) << 18) | ((uint32_t)(src[i + 1] & 0x3F) << 12) | ((uint32_t)(src[i + 2] & 0x3F) << 6) | (src[i + 3] & 0x3F);
            if (cp >= 0x10000 && cp <= 0x10FFFF) {
                sb_append_n(&sb, (const char *)(src + i), 4);
                i += 4;
            } else {
                sb_append_n(&sb, "\xEF\xBF\xBD", 3);
                i++;
            }
        } else {
            sb_append_n(&sb, "\xEF\xBF\xBD", 3);
            i++;
        }
    }
    return sb.buf;
}

static void sb_append_json_str(StrBuf *sb, const char *s) {
    if (!s) {
        sb_append(sb, "null");
        return;
    }
    sb_append_char(sb, '"');
    for (const unsigned char *p = (const unsigned char *)s; *p; p++) {
        unsigned char c = *p;
        switch (c) {
            case '"':  sb_append(sb, "\\\""); break;
            case '\\': sb_append(sb, "\\\\"); break;
            case '\b': sb_append(sb, "\\b"); break;
            case '\f': sb_append(sb, "\\f"); break;
            case '\n': sb_append(sb, "\\n"); break;
            case '\r': sb_append(sb, "\\r"); break;
            case '\t': sb_append(sb, "\\t"); break;
            default:
                if (c < 0x20) {
                    char tmp[8];
                    snprintf(tmp, sizeof(tmp), "\\u%04x", c);
                    sb_append(sb, tmp);
                } else {
                    sb_append_char(sb, (char)c);
                }
        }
    }
    sb_append_char(sb, '"');
}

/* Slice UTF-8 string by codepoint offset and limit */
static void utf8_slice_by_chars(const char *s, size_t byte_len, size_t cp_offset, size_t cp_limit, size_t *out_byte_start, size_t *out_byte_len, size_t *out_total_cp) {
    size_t cur_cp = 0;
    size_t start_byte = byte_len;
    size_t end_byte = byte_len;
    size_t target_end = cp_offset + cp_limit;
    for (size_t i = 0; i < byte_len; i++) {
        if (((uint8_t)s[i] & 0xC0) != 0x80) {
            if (cur_cp == cp_offset) start_byte = i;
            if (cur_cp == target_end) end_byte = i;
            cur_cp++;
        }
    }
    if (cp_offset >= cur_cp) {
        start_byte = byte_len;
        end_byte = byte_len;
    }
    *out_byte_start = start_byte;
    *out_byte_len = end_byte - start_byte;
    *out_total_cp = cur_cp;
}

/* ============================================================================
 * Bit reader helpers backed by asm_bit_read_u32
 * ============================================================================ */
static int bit_read_s32(const uint8_t *buf, size_t byte_len, size_t *bit_pos, uint32_t count, int32_t *out_val) {
    if (count == 0) { *out_val = 0; return 1; }
    uint32_t u = 0;
    if (!asm_bit_read_u32(buf, byte_len, bit_pos, count, &u)) return 0;
    uint32_t sign_bit = 1u << (count - 1);
    if (u & sign_bit) {
        *out_val = (int32_t)(u | (~0u << count));
    } else {
        *out_val = (int32_t)u;
    }
    return 1;
}

static int skip_matrix(const uint8_t *data, size_t len, size_t *pos) {
    if (*pos >= len) return 0;
    const uint8_t *sub = data + *pos;
    size_t sub_len = len - *pos;
    size_t bit_pos = 0;
    uint32_t has_scale = 0, bits = 0, dummy = 0;
    if (!asm_bit_read_u32(sub, sub_len, &bit_pos, 1, &has_scale)) return 0;
    if (has_scale) {
        if (!asm_bit_read_u32(sub, sub_len, &bit_pos, 5, &bits)) return 0;
        if (!asm_bit_read_u32(sub, sub_len, &bit_pos, bits, &dummy)) return 0;
        if (!asm_bit_read_u32(sub, sub_len, &bit_pos, bits, &dummy)) return 0;
    }
    uint32_t has_rot = 0;
    if (!asm_bit_read_u32(sub, sub_len, &bit_pos, 1, &has_rot)) return 0;
    if (has_rot) {
        if (!asm_bit_read_u32(sub, sub_len, &bit_pos, 5, &bits)) return 0;
        if (!asm_bit_read_u32(sub, sub_len, &bit_pos, bits, &dummy)) return 0;
        if (!asm_bit_read_u32(sub, sub_len, &bit_pos, bits, &dummy)) return 0;
    }
    if (!asm_bit_read_u32(sub, sub_len, &bit_pos, 5, &bits)) return 0;
    if (!asm_bit_read_u32(sub, sub_len, &bit_pos, bits, &dummy)) return 0;
    if (!asm_bit_read_u32(sub, sub_len, &bit_pos, bits, &dummy)) return 0;
    *pos += (bit_pos + 7) >> 3;
    return 1;
}

static int skip_cxform_alpha(const uint8_t *data, size_t len, size_t *pos) {
    if (*pos >= len) return 0;
    const uint8_t *sub = data + *pos;
    size_t sub_len = len - *pos;
    size_t bit_pos = 0;
    uint32_t has_add = 0, has_mult = 0, nbits = 0, dummy = 0;
    if (!asm_bit_read_u32(sub, sub_len, &bit_pos, 1, &has_add)) return 0;
    if (!asm_bit_read_u32(sub, sub_len, &bit_pos, 1, &has_mult)) return 0;
    if (!asm_bit_read_u32(sub, sub_len, &bit_pos, 4, &nbits)) return 0;
    if (has_mult) {
        for (int i = 0; i < 4; i++) if (!asm_bit_read_u32(sub, sub_len, &bit_pos, nbits, &dummy)) return 0;
    }
    if (has_add) {
        for (int i = 0; i < 4; i++) if (!asm_bit_read_u32(sub, sub_len, &bit_pos, nbits, &dummy)) return 0;
    }
    *pos += (bit_pos + 7) >> 3;
    return 1;
}

static int skip_filter_list(const uint8_t *data, size_t len, size_t *pos) {
    if (*pos >= len) return 0;
    uint8_t count = data[(*pos)++];
    for (uint8_t i = 0; i < count; i++) {
        if (*pos >= len) return 0;
        uint8_t fid = data[(*pos)++];
        size_t skip = 0;
        switch (fid) {
            case 0: skip = 23; break;
            case 1: skip = 9; break;
            case 2: skip = 15; break;
            case 3: skip = 27; break;
            case 4:
                if (*pos >= len) return 0;
                skip = (size_t)data[(*pos)++] * 5 + 19;
                break;
            case 5:
                if (*pos + 2 > len) return 0;
                skip = (size_t)data[*pos] * (size_t)data[*pos + 1] * 4 + 13;
                *pos += 2;
                break;
            case 6: skip = 80; break;
            case 7:
                if (*pos >= len) return 0;
                skip = (size_t)data[(*pos)++] * 5 + 27;
                break;
            default: return 0;
        }
        if (*pos + skip > len) return 0;
        *pos += skip;
    }
    return 1;
}

static int skip_button_records(const uint8_t *data, size_t len, size_t *pos, int is_button2) {
    for (;;) {
        if (*pos >= len) return 0;
        uint8_t flags = data[(*pos)++];
        if (flags == 0) return 1;
        if (*pos + 4 > len) return 0;
        *pos += 4;
        if (!skip_matrix(data, len, pos)) return 0;
        if (is_button2 && !skip_cxform_alpha(data, len, pos)) return 0;
        if ((flags & 0x10) && !skip_filter_list(data, len, pos)) return 0;
        if (flags & 0x20) {
            if (*pos >= len) return 0;
            (*pos)++;
        }
    }
}

/* ============================================================================
 * AVM2 ABC structures & parser (using asm_avm2_read_u30)
 * ============================================================================ */
typedef struct {
    int method_index;
    size_t code_offset;
    size_t code_len;
    const uint8_t *code;
} AbcBody;

typedef struct {
    uint16_t minor_version;
    uint16_t major_version;
    char *abc_name;
    size_t abc_header_offset;
    int32_t *integers;
    size_t int_count;
    uint32_t *uintegers;
    size_t uint_count;
    double *doubles;
    size_t double_count;
    char **strings;
    size_t string_count;
    char **namespaces;
    size_t ns_count;
    char **multinames;
    size_t mn_count;
    char **method_raw_names;
    char **method_trait_names;
    size_t method_count;
    AbcBody *bodies;
    size_t body_count;
} AbcFile;

static void free_abc(AbcFile *abc) {
    if (!abc) return;
    free(abc->abc_name);
    free(abc->integers);
    free(abc->uintegers);
    free(abc->doubles);
    for (size_t i = 0; i < abc->string_count; i++) free(abc->strings[i]);
    free(abc->strings);
    for (size_t i = 0; i < abc->ns_count; i++) free(abc->namespaces[i]);
    free(abc->namespaces);
    for (size_t i = 0; i < abc->mn_count; i++) free(abc->multinames[i]);
    free(abc->multinames);
    for (size_t i = 0; i < abc->method_count; i++) {
        free(abc->method_raw_names[i]);
        free(abc->method_trait_names[i]);
    }
    free(abc->method_raw_names);
    free(abc->method_trait_names);
    free(abc->bodies);
    free(abc);
}

static int abc_read_u30(const uint8_t *buf, size_t *pos, size_t end, uint32_t *out) {
    size_t next = asm_avm2_read_u30(buf, *pos, end, out);
    if (!next) return 0;
    *pos = next;
    return 1;
}

static int abc_skip_traits(const uint8_t *buf, size_t *pos, size_t end, AbcFile *abc, const char *owner) {
    uint32_t count = 0;
    if (!abc_read_u30(buf, pos, end, &count)) return 0;
    for (uint32_t i = 0; i < count; i++) {
        uint32_t name_idx = 0;
        if (!abc_read_u30(buf, pos, end, &name_idx)) return 0;
        if (*pos >= end) return 0;
        uint8_t kind_attr = buf[(*pos)++];
        uint8_t kind = kind_attr & 0x0F;
        uint8_t attr = kind_attr >> 4;
        uint32_t dummy = 0, method_idx = 0;
        if (kind == 0 || kind == 6) {
            if (!abc_read_u30(buf, pos, end, &dummy)) return 0;
            if (!abc_read_u30(buf, pos, end, &dummy)) return 0;
            uint32_t vindex = 0;
            if (!abc_read_u30(buf, pos, end, &vindex)) return 0;
            if (vindex != 0) {
                if (*pos >= end) return 0;
                (*pos)++;
            }
        } else if (kind >= 1 && kind <= 5) {
            if (!abc_read_u30(buf, pos, end, &dummy)) return 0;
            if (!abc_read_u30(buf, pos, end, &method_idx)) return 0;
            if (owner && (kind == 1 || kind == 2 || kind == 3) && method_idx < abc->method_count) {
                const char *tname = (name_idx < abc->mn_count) ? abc->multinames[name_idx] : "trait";
                const char *suffix = (kind == 2) ? "/get" : (kind == 3 ? "/set" : "");
                size_t l = strlen(owner) + 1 + strlen(tname) + strlen(suffix) + 1;
                char *full = (char *)malloc(l);
                snprintf(full, l, "%s.%s%s", owner, tname, suffix);
                free(abc->method_trait_names[method_idx]);
                abc->method_trait_names[method_idx] = full;
            }
        } else {
            return 0;
        }
        if (attr & 0x04) {
            uint32_t mcount = 0;
            if (!abc_read_u30(buf, pos, end, &mcount)) return 0;
            for (uint32_t m = 0; m < mcount; m++) {
                if (!abc_read_u30(buf, pos, end, &dummy)) return 0;
            }
        }
    }
    return 1;
}

static AbcFile *parse_do_abc(const uint8_t *tag_data, size_t tag_len, int is_abc2) {
    size_t abc_offset = 0;
    char *abc_name = NULL;
    if (is_abc2) {
        if (tag_len < 5) return NULL;
        size_t p = 4;
        while (p < tag_len && tag_data[p] != 0) p++;
        if (p >= tag_len) return NULL;
        abc_name = utf8_lossy_dup(tag_data + 4, p - 4);
        abc_offset = p + 1;
    } else {
        abc_name = strdup("");
    }
    const uint8_t *buf = tag_data + abc_offset;
    size_t end = tag_len - abc_offset;
    if (end < 4) { free(abc_name); return NULL; }

    AbcFile *abc = (AbcFile *)calloc(1, sizeof(AbcFile));
    abc->abc_name = abc_name;
    abc->abc_header_offset = abc_offset;
    abc->minor_version = (uint16_t)(buf[0] | (buf[1] << 8));
    abc->major_version = (uint16_t)(buf[2] | (buf[3] << 8));
    size_t pos = 4;
    uint32_t cnt = 0;

    /* integers */
    if (!abc_read_u30(buf, &pos, end, &cnt)) { free_abc(abc); return NULL; }
    abc->int_count = cnt > 0 ? cnt : 1;
    abc->integers = (int32_t *)calloc(abc->int_count, sizeof(int32_t));
    for (uint32_t i = 1; i < cnt; i++) {
        uint32_t v = 0;
        if (!abc_read_u30(buf, &pos, end, &v)) { free_abc(abc); return NULL; }
        abc->integers[i] = (int32_t)v;
    }

    /* uintegers */
    if (!abc_read_u30(buf, &pos, end, &cnt)) { free_abc(abc); return NULL; }
    abc->uint_count = cnt > 0 ? cnt : 1;
    abc->uintegers = (uint32_t *)calloc(abc->uint_count, sizeof(uint32_t));
    for (uint32_t i = 1; i < cnt; i++) {
        if (!abc_read_u30(buf, &pos, end, &abc->uintegers[i])) { free_abc(abc); return NULL; }
    }

    /* doubles */
    if (!abc_read_u30(buf, &pos, end, &cnt)) { free_abc(abc); return NULL; }
    abc->double_count = cnt > 0 ? cnt : 1;
    abc->doubles = (double *)calloc(abc->double_count, sizeof(double));
    for (uint32_t i = 1; i < cnt; i++) {
        if (pos + 8 > end) { free_abc(abc); return NULL; }
        double d = 0.0;
        memcpy(&d, buf + pos, 8);
        pos += 8;
        abc->doubles[i] = d;
    }

    /* strings */
    if (!abc_read_u30(buf, &pos, end, &cnt)) { free_abc(abc); return NULL; }
    abc->string_count = cnt > 0 ? cnt : 1;
    abc->strings = (char **)calloc(abc->string_count, sizeof(char *));
    abc->strings[0] = strdup("");
    for (uint32_t i = 1; i < cnt; i++) {
        uint32_t slen = 0;
        if (!abc_read_u30(buf, &pos, end, &slen) || pos + slen > end) { free_abc(abc); return NULL; }
        abc->strings[i] = utf8_lossy_dup(buf + pos, slen);
        pos += slen;
    }

    /* namespaces */
    if (!abc_read_u30(buf, &pos, end, &cnt)) { free_abc(abc); return NULL; }
    abc->ns_count = cnt > 0 ? cnt : 1;
    abc->namespaces = (char **)calloc(abc->ns_count, sizeof(char *));
    abc->namespaces[0] = strdup("*");
    for (uint32_t i = 1; i < cnt; i++) {
        if (pos >= end) { free_abc(abc); return NULL; }
        pos++;
        uint32_t nidx = 0;
        if (!abc_read_u30(buf, &pos, end, &nidx)) { free_abc(abc); return NULL; }
        abc->namespaces[i] = strdup(nidx < abc->string_count ? abc->strings[nidx] : "");
    }

    /* ns sets */
    if (!abc_read_u30(buf, &pos, end, &cnt)) { free_abc(abc); return NULL; }
    for (uint32_t i = 1; i < cnt; i++) {
        uint32_t sc = 0, dummy = 0;
        if (!abc_read_u30(buf, &pos, end, &sc)) { free_abc(abc); return NULL; }
        for (uint32_t j = 0; j < sc; j++) {
            if (!abc_read_u30(buf, &pos, end, &dummy)) { free_abc(abc); return NULL; }
        }
    }

    /* multinames */
    if (!abc_read_u30(buf, &pos, end, &cnt)) { free_abc(abc); return NULL; }
    abc->mn_count = cnt > 0 ? cnt : 1;
    abc->multinames = (char **)calloc(abc->mn_count, sizeof(char *));
    abc->multinames[0] = strdup("*");
    for (uint32_t i = 1; i < cnt; i++) {
        if (pos >= end) { free_abc(abc); return NULL; }
        uint8_t kind = buf[pos++];
        uint32_t a = 0, b = 0;
        if (kind == 0x07 || kind == 0x0D) {
            if (!abc_read_u30(buf, &pos, end, &a) || !abc_read_u30(buf, &pos, end, &b)) { free_abc(abc); return NULL; }
            const char *ns = (a < abc->ns_count) ? abc->namespaces[a] : "";
            const char *nm = (b < abc->string_count) ? abc->strings[b] : "*";
            if (!ns[0] || strcmp(ns, "*") == 0) {
                abc->multinames[i] = strdup(nm);
            } else {
                size_t l = strlen(ns) + 2 + strlen(nm) + 1;
                abc->multinames[i] = (char *)malloc(l);
                snprintf(abc->multinames[i], l, "%s::%s", ns, nm);
            }
        } else if (kind == 0x0F || kind == 0x10) {
            if (!abc_read_u30(buf, &pos, end, &a)) { free_abc(abc); return NULL; }
            abc->multinames[i] = strdup(a < abc->string_count ? abc->strings[a] : "*");
        } else if (kind == 0x11 || kind == 0x12) {
            abc->multinames[i] = strdup("<rtqname>");
        } else if (kind == 0x09 || kind == 0x0E) {
            if (!abc_read_u30(buf, &pos, end, &a) || !abc_read_u30(buf, &pos, end, &b)) { free_abc(abc); return NULL; }
            abc->multinames[i] = strdup(a < abc->string_count ? abc->strings[a] : "*");
        } else if (kind == 0x1B || kind == 0x1C) {
            if (!abc_read_u30(buf, &pos, end, &a)) { free_abc(abc); return NULL; }
            abc->multinames[i] = strdup("<multinameL>");
        } else if (kind == 0x1D) {
            if (!abc_read_u30(buf, &pos, end, &a) || !abc_read_u30(buf, &pos, end, &b)) { free_abc(abc); return NULL; }
            StrBuf sb;
            sb_init(&sb);
            sb_append(&sb, (a < abc->mn_count && abc->multinames[a]) ? abc->multinames[a] : "Vector");
            sb_append(&sb, ".<");
            for (uint32_t p = 0; p < b; p++) {
                uint32_t pidx = 0;
                if (!abc_read_u30(buf, &pos, end, &pidx)) { sb_free(&sb); free_abc(abc); return NULL; }
                if (p > 0) sb_append(&sb, ", ");
                sb_append(&sb, (pidx < abc->mn_count && abc->multinames[pidx]) ? abc->multinames[pidx] : "*");
            }
            sb_append(&sb, ">");
            abc->multinames[i] = sb.buf;
        } else {
            free_abc(abc);
            return NULL;
        }
    }

    /* methods */
    if (!abc_read_u30(buf, &pos, end, &cnt)) { free_abc(abc); return NULL; }
    abc->method_count = cnt;
    abc->method_raw_names = (char **)calloc(cnt > 0 ? cnt : 1, sizeof(char *));
    abc->method_trait_names = (char **)calloc(cnt > 0 ? cnt : 1, sizeof(char *));
    for (uint32_t i = 0; i < cnt; i++) {
        uint32_t pcount = 0, dummy = 0, name_idx = 0;
        if (!abc_read_u30(buf, &pos, end, &pcount)) { free_abc(abc); return NULL; }
        if (!abc_read_u30(buf, &pos, end, &dummy)) { free_abc(abc); return NULL; }
        for (uint32_t p = 0; p < pcount; p++) {
            if (!abc_read_u30(buf, &pos, end, &dummy)) { free_abc(abc); return NULL; }
        }
        if (!abc_read_u30(buf, &pos, end, &name_idx) || pos >= end) { free_abc(abc); return NULL; }
        uint8_t flags = buf[pos++];
        if (flags & 0x08) {
            uint32_t oc = 0;
            if (!abc_read_u30(buf, &pos, end, &oc)) { free_abc(abc); return NULL; }
            for (uint32_t o = 0; o < oc; o++) {
                if (!abc_read_u30(buf, &pos, end, &dummy) || pos >= end) { free_abc(abc); return NULL; }
                pos++;
            }
        }
        if (flags & 0x80) {
            for (uint32_t p = 0; p < pcount; p++) {
                if (!abc_read_u30(buf, &pos, end, &dummy)) { free_abc(abc); return NULL; }
            }
        }
        abc->method_raw_names[i] = strdup(name_idx < abc->string_count ? abc->strings[name_idx] : "");
    }

    /* metadata */
    if (!abc_read_u30(buf, &pos, end, &cnt)) { free_abc(abc); return NULL; }
    for (uint32_t i = 0; i < cnt; i++) {
        uint32_t dummy = 0, ic = 0;
        if (!abc_read_u30(buf, &pos, end, &dummy) || !abc_read_u30(buf, &pos, end, &ic)) { free_abc(abc); return NULL; }
        for (uint32_t j = 0; j < ic; j++) {
            if (!abc_read_u30(buf, &pos, end, &dummy) || !abc_read_u30(buf, &pos, end, &dummy)) { free_abc(abc); return NULL; }
        }
    }

    /* instances & classes */
    uint32_t class_count = 0;
    if (!abc_read_u30(buf, &pos, end, &class_count)) { free_abc(abc); return NULL; }
    char **class_names = (char **)calloc(class_count > 0 ? class_count : 1, sizeof(char *));
    for (uint32_t i = 0; i < class_count; i++) {
        uint32_t name_idx = 0, dummy = 0, ic = 0, iinit = 0;
        if (!abc_read_u30(buf, &pos, end, &name_idx) || !abc_read_u30(buf, &pos, end, &dummy) || pos >= end) {
            for (uint32_t k = 0; k < i; k++) free(class_names[k]);
            free(class_names); free_abc(abc); return NULL;
        }
        class_names[i] = strdup(name_idx < abc->mn_count ? abc->multinames[name_idx] : "Class");
        uint8_t flags = buf[pos++];
        if ((flags & 0x08) && !abc_read_u30(buf, &pos, end, &dummy)) {
            for (uint32_t k = 0; k <= i; k++) free(class_names[k]);
            free(class_names); free_abc(abc); return NULL;
        }
        if (!abc_read_u30(buf, &pos, end, &ic)) {
            for (uint32_t k = 0; k <= i; k++) free(class_names[k]);
            free(class_names); free_abc(abc); return NULL;
        }
        for (uint32_t j = 0; j < ic; j++) {
            if (!abc_read_u30(buf, &pos, end, &dummy)) {
                for (uint32_t k = 0; k <= i; k++) free(class_names[k]);
                free(class_names); free_abc(abc); return NULL;
            }
        }
        if (!abc_read_u30(buf, &pos, end, &iinit)) {
            for (uint32_t k = 0; k <= i; k++) free(class_names[k]);
            free(class_names); free_abc(abc); return NULL;
        }
        if (iinit < abc->method_count) {
            size_t l = strlen(class_names[i]) + 16;
            free(abc->method_trait_names[iinit]);
            abc->method_trait_names[iinit] = (char *)malloc(l);
            snprintf(abc->method_trait_names[iinit], l, "%s/instance_init", class_names[i]);
        }
        if (!abc_skip_traits(buf, &pos, end, abc, class_names[i])) {
            for (uint32_t k = 0; k <= i; k++) free(class_names[k]);
            free(class_names); free_abc(abc); return NULL;
        }
    }
    for (uint32_t i = 0; i < class_count; i++) {
        uint32_t cinit = 0;
        if (!abc_read_u30(buf, &pos, end, &cinit)) {
            for (uint32_t k = 0; k < class_count; k++) free(class_names[k]);
            free(class_names); free_abc(abc); return NULL;
        }
        if (cinit < abc->method_count) {
            size_t l = strlen(class_names[i]) + 16;
            free(abc->method_trait_names[cinit]);
            abc->method_trait_names[cinit] = (char *)malloc(l);
            snprintf(abc->method_trait_names[cinit], l, "%s/class_init", class_names[i]);
        }
        size_t sl = strlen(class_names[i]) + 2;
        char *sowner = (char *)malloc(sl);
        snprintf(sowner, sl, "%s$", class_names[i]);
        int ok = abc_skip_traits(buf, &pos, end, abc, sowner);
        free(sowner);
        if (!ok) {
            for (uint32_t k = 0; k < class_count; k++) free(class_names[k]);
            free(class_names); free_abc(abc); return NULL;
        }
    }
    for (uint32_t k = 0; k < class_count; k++) free(class_names[k]);
    free(class_names);

    /* scripts */
    uint32_t script_count = 0;
    if (!abc_read_u30(buf, &pos, end, &script_count)) { free_abc(abc); return NULL; }
    for (uint32_t i = 0; i < script_count; i++) {
        uint32_t init = 0;
        if (!abc_read_u30(buf, &pos, end, &init)) { free_abc(abc); return NULL; }
        char sname[32];
        snprintf(sname, sizeof(sname), "script_%u", i);
        if (init < abc->method_count) {
            char full[48];
            snprintf(full, sizeof(full), "%s/init", sname);
            free(abc->method_trait_names[init]);
            abc->method_trait_names[init] = strdup(full);
        }
        if (!abc_skip_traits(buf, &pos, end, abc, sname)) { free_abc(abc); return NULL; }
    }

    /* bodies */
    uint32_t body_count = 0;
    if (!abc_read_u30(buf, &pos, end, &body_count)) { free_abc(abc); return NULL; }
    abc->body_count = body_count;
    abc->bodies = (AbcBody *)calloc(body_count > 0 ? body_count : 1, sizeof(AbcBody));
    for (uint32_t i = 0; i < body_count; i++) {
        uint32_t midx = 0, dummy = 0, code_len = 0, exc_count = 0;
        if (!abc_read_u30(buf, &pos, end, &midx)) { free_abc(abc); return NULL; }
        for (int k = 0; k < 4; k++) if (!abc_read_u30(buf, &pos, end, &dummy)) { free_abc(abc); return NULL; }
        if (!abc_read_u30(buf, &pos, end, &code_len) || pos + code_len > end) { free_abc(abc); return NULL; }
        abc->bodies[i].method_index = (int)midx;
        abc->bodies[i].code_offset = pos;
        abc->bodies[i].code_len = code_len;
        abc->bodies[i].code = buf + pos;
        pos += code_len;
        if (!abc_read_u30(buf, &pos, end, &exc_count)) { free_abc(abc); return NULL; }
        for (uint32_t e = 0; e < exc_count; e++) {
            for (int k = 0; k < 3; k++) if (!abc_read_u30(buf, &pos, end, &dummy)) { free_abc(abc); return NULL; }
            if (!(abc->major_version == 46 && abc->minor_version == 15)) {
                if (!abc_read_u30(buf, &pos, end, &dummy) || !abc_read_u30(buf, &pos, end, &dummy)) { free_abc(abc); return NULL; }
            }
        }
        if (!abc_skip_traits(buf, &pos, end, abc, NULL)) { free_abc(abc); return NULL; }
    }
    return abc;
}

/* ============================================================================
 * Script Unit & Session Inventory
 * ============================================================================ */
typedef struct {
    char *id;
    int tag_index;
    int tag_path[16];
    int tag_path_len;
    uint16_t tag_code;
    char *language;
    char *kind;
    char *display_name;
    size_t byte_start;
    size_t byte_len;
    char sha256[65];

    /* For nested AVM1 functions */
    int is_avm1_func;
    char *fn_name;
    char **params;
    size_t param_count;
    int *registers;
    size_t reg_count;
    int has_reg_count;
    int avm1_reg_count_val;
    int avm1_flags;
    char avm1_constants_sha256[65];
    char **inherited_constants;
    size_t inherited_constant_count;

    /* Payload pointers */
    const uint8_t *slice;
    AbcFile *abc;
    size_t abc_body_index;
} ScriptUnit;

typedef struct {
    uint16_t id;
    char *name;
} SymbolEntry;

typedef struct {
    char *path;
    char file_sha256[65];
    char normalized_sha256[65];
    char compression[4];
    uint8_t version;
    uint32_t file_length;
    int32_t stage_width_twips;
    int32_t stage_height_twips;
    double frame_rate;
    uint16_t frame_count;
    uint8_t *normalized_bytes;
    size_t normalized_len;
    size_t tag_count;
    SymbolEntry *symbols;
    size_t symbol_count;
    AbcFile **abc_files;
    size_t abc_count;
    ScriptUnit *scripts;
    size_t script_count;
    size_t script_cap;
} SwfSession;

static void free_session(SwfSession *s) {
    if (!s) return;
    free(s->path);
    free(s->normalized_bytes);
    for (size_t i = 0; i < s->symbol_count; i++) free(s->symbols[i].name);
    free(s->symbols);
    for (size_t i = 0; i < s->abc_count; i++) free_abc(s->abc_files[i]);
    free(s->abc_files);
    for (size_t i = 0; i < s->script_count; i++) {
        ScriptUnit *u = &s->scripts[i];
        free(u->id);
        free(u->language);
        free(u->kind);
        free(u->display_name);
        free(u->fn_name);
        for (size_t p = 0; p < u->param_count; p++) free(u->params[p]);
        free(u->params);
        free(u->registers);
        for (size_t c = 0; c < u->inherited_constant_count; c++) free(u->inherited_constants[c]);
        free(u->inherited_constants);
    }
    free(s->scripts);
    free(s);
}

static ScriptUnit *session_alloc_unit(SwfSession *s) {
    if (s->script_count >= s->script_cap) {
        s->script_cap = s->script_cap ? s->script_cap * 2 : 64;
        s->scripts = (ScriptUnit *)realloc(s->scripts, s->script_cap * sizeof(ScriptUnit));
    }
    ScriptUnit *u = &s->scripts[s->script_count++];
    memset(u, 0, sizeof(ScriptUnit));
    return u;
}

static const char *lookup_symbol(SwfSession *s, uint16_t id) {
    for (size_t i = 0; i < s->symbol_count; i++) {
        if (s->symbols[i].id == id) return s->symbols[i].name;
    }
    return NULL;
}

/* Read NUL-terminated string strictly or lossily */
static char *read_cstring_lossy(const uint8_t *buf, size_t len, size_t *pos) {
    size_t start = *pos;
    size_t end = start;
    while (end < len && buf[end] != 0) end++;
    *pos = (end < len) ? (end + 1) : len;
    return utf8_lossy_dup(buf + start, end - start);
}

static char *read_cstring_strict(const uint8_t *buf, size_t len, size_t *pos) {
    size_t start = *pos;
    size_t end = start;
    while (end < len && buf[end] != 0) end++;
    if (end >= len) { *pos = len; return NULL; }
    *pos = end + 1;
    return utf8_lossy_dup(buf + start, end - start);
}

/* Recursive discovery of nested DefineFunction / DefineFunction2 */
static void walk_nested_avm1(
    SwfSession *s,
    const char *parent_id,
    const char *parent_display,
    int tag_index,
    const int *tag_path,
    int tag_path_len,
    uint16_t tag_code,
    size_t base_byte_start,
    const uint8_t *data,
    size_t start,
    size_t end,
    char **inherited_constants,
    size_t inherited_count,
    int depth
) {
    if (depth > 32) return;
    size_t pos = start;
    char **active_constants = NULL;
    size_t active_count = inherited_count;
    if (active_count > 0) {
        active_constants = (char **)malloc(active_count * sizeof(char *));
        for (size_t i = 0; i < active_count; i++) active_constants[i] = strdup(inherited_constants[i]);
    }

    while (pos < end) {
        uint8_t code = 0;
        uint16_t length = 0;
        size_t payload_start = asm_avm1_scan_action(data, pos, end, &code, &length);
        if (!payload_start) break;
        if (code == 0x00) break;
        if (payload_start + length > end) break;
        const uint8_t *payload = data + payload_start;
        size_t payload_end = payload_start + length;
        pos = payload_end;

        if (code == 0x88 && length >= 2) {
            uint16_t count = (uint16_t)(payload[0] | (payload[1] << 8));
            for (size_t i = 0; i < active_count; i++) free(active_constants[i]);
            free(active_constants);
            active_constants = (char **)calloc(count > 0 ? count : 1, sizeof(char *));
            active_count = 0;
            size_t p = 2;
            for (uint16_t i = 0; i < count && p < length; i++) {
                active_constants[active_count++] = read_cstring_lossy(payload, length, &p);
            }
        } else if (code == 0x9B || code == 0x8E) {
            size_t p = 0;
            char *fn_name = read_cstring_strict(payload, length, &p);
            if (!fn_name) continue;
            if ((code == 0x9B && p + 2 > length) || (code == 0x8E && p + 5 > length)) {
                free(fn_name);
                continue;
            }
            uint16_t num_params = (uint16_t)(payload[p] | (payload[p + 1] << 8));
            p += 2;
            int reg_count = 0;
            int flags = 0;
            if (code == 0x8E) {
                reg_count = payload[p];
                flags = (int)(payload[p + 1] | (payload[p + 2] << 8));
                p += 3;
            }
            char **params = (char **)calloc(num_params > 0 ? num_params : 1, sizeof(char *));
            int *registers = (int *)calloc(num_params > 0 ? num_params : 1, sizeof(int));
            int valid = 1;
            for (uint16_t i = 0; i < num_params; i++) {
                if (code == 0x8E) {
                    if (p >= length) { valid = 0; break; }
                    registers[i] = payload[p++];
                }
                char *pname = read_cstring_strict(payload, length, &p);
                if (!pname) { valid = 0; break; }
                params[i] = pname;
            }
            if (!valid || p + 2 > length) {
                for (uint16_t i = 0; i < num_params; i++) free(params[i]);
                free(params); free(registers); free(fn_name);
                continue;
            }
            uint16_t code_size = (uint16_t)(payload[p] | (payload[p + 1] << 8));
            size_t body_offset = payload_end;
            size_t body_end = body_offset + code_size;
            if (body_end > end) {
                for (uint16_t i = 0; i < num_params; i++) free(params[i]);
                free(params); free(registers); free(fn_name);
                break;
            }
            pos = body_end;

            /* Compute constants_sha256 */
            StrBuf csb;
            sb_init(&csb);
            sb_append_char(&csb, '[');
            for (size_t i = 0; i < active_count; i++) {
                if (i > 0) sb_append_char(&csb, ',');
                sb_append_json_str(&csb, active_constants[i]);
            }
            sb_append_char(&csb, ']');
            char const_hash[65];
            compute_sha256_hex((const uint8_t *)csb.buf, csb.len, const_hash);
            sb_free(&csb);

            size_t fn_start = base_byte_start + body_offset;
            ScriptUnit *u = session_alloc_unit(s);
            char id_buf[256];
            snprintf(id_buf, sizeof(id_buf), "%s/function:%zx", parent_id, fn_start);
            u->id = strdup(id_buf);
            u->tag_index = tag_index;
            memcpy(u->tag_path, tag_path, tag_path_len * sizeof(int));
            u->tag_path_len = tag_path_len;
            u->tag_code = tag_code;
            u->language = strdup("avm1");
            u->kind = strdup("function");

            char label_buf[256];
            if (fn_name[0]) {
                snprintf(label_buf, sizeof(label_buf), "%s", fn_name);
            } else {
                snprintf(label_buf, sizeof(label_buf), "<anonymous@0x%zx>", body_offset);
            }
            size_t dl = strlen(parent_display) + 14 + strlen(label_buf) + 1;
            u->display_name = (char *)malloc(dl);
            snprintf(u->display_name, dl, "%s :: function %s", parent_display, label_buf);
            u->byte_start = fn_start;
            u->byte_len = code_size;
            u->slice = data + body_offset;
            compute_sha256_hex(u->slice, code_size, u->sha256);

            u->is_avm1_func = 1;
            u->params = params;
            u->param_count = num_params;
            u->registers = registers;
            u->reg_count = (code == 0x8E) ? num_params : 0;
            u->has_reg_count = (code == 0x8E) ? 1 : 0;
            u->avm1_reg_count_val = reg_count;
            u->avm1_flags = flags;
            memcpy(u->avm1_constants_sha256, const_hash, 65);
            u->inherited_constant_count = active_count;
            if (active_count > 0) {
                u->inherited_constants = (char **)malloc(active_count * sizeof(char *));
                for (size_t i = 0; i < active_count; i++) u->inherited_constants[i] = strdup(active_constants[i]);
            }
            u->fn_name = fn_name;

            char *pid_copy = strdup(id_buf);
            char *pdisp_copy = strdup(u->display_name);
            walk_nested_avm1(
                s, pid_copy, pdisp_copy, tag_index, tag_path, tag_path_len, tag_code,
                base_byte_start, data, body_offset, body_end, active_constants, active_count, depth + 1
            );
            free(pid_copy);
            free(pdisp_copy);
        } else if (code == 0x94 && length == 2) {
            uint16_t wsize = (uint16_t)(payload[0] | (payload[1] << 8));
            size_t body_offset = payload_end;
            size_t body_end = body_offset + wsize;
            if (body_end > end) break;
            pos = body_end;
            walk_nested_avm1(
                s, parent_id, parent_display, tag_index, tag_path, tag_path_len, tag_code,
                base_byte_start, data, body_offset, body_end, active_constants, active_count, depth + 1
            );
        } else if (code == 0x8F && length >= 7) {
            uint16_t sz[3] = {
                (uint16_t)(payload[1] | (payload[2] << 8)),
                (uint16_t)(payload[3] | (payload[4] << 8)),
                (uint16_t)(payload[5] | (payload[6] << 8))
            };
            for (int k = 0; k < 3; k++) {
                size_t b_off = pos;
                size_t b_end = b_off + sz[k];
                if (b_end > end) { pos = end; break; }
                pos = b_end;
                walk_nested_avm1(
                    s, parent_id, parent_display, tag_index, tag_path, tag_path_len, tag_code,
                    base_byte_start, data, b_off, b_end, active_constants, active_count, depth + 1
                );
            }
        }
    }

    for (size_t i = 0; i < active_count; i++) free(active_constants[i]);
    free(active_constants);
}

static void add_avm1_block(
    SwfSession *s,
    const char *id,
    int tag_index,
    const int *tag_path,
    int tag_path_len,
    uint16_t tag_code,
    const char *kind,
    const char *display_name,
    size_t byte_start,
    const uint8_t *slice,
    size_t byte_len
) {
    char *id_dup = strdup(id);
    char *disp_dup = strdup(display_name);

    ScriptUnit *u = session_alloc_unit(s);
    u->id = strdup(id_dup);
    u->tag_index = tag_index;
    memcpy(u->tag_path, tag_path, tag_path_len * sizeof(int));
    u->tag_path_len = tag_path_len;
    u->tag_code = tag_code;
    u->language = strdup("avm1");
    u->kind = strdup(kind);
    u->display_name = strdup(disp_dup);
    u->byte_start = byte_start;
    u->byte_len = byte_len;
    u->slice = slice;
    compute_sha256_hex(slice, byte_len, u->sha256);

    walk_nested_avm1(
        s, id_dup, disp_dup, tag_index, tag_path, tag_path_len, tag_code,
        byte_start, slice, 0, byte_len, NULL, 0, 0
    );

    free(id_dup);
    free(disp_dup);
}

static void format_tag_prefix(const int *tag_path, int len, char *out, size_t out_sz) {
    out[0] = '\0';
    for (int i = 0; i < len; i++) {
        char seg[32];
        snprintf(seg, sizeof(seg), "%stag:%d", (i > 0 ? "/" : ""), tag_path[i]);
        strncat(out, seg, out_sz - strlen(out) - 1);
    }
}

/* First pass on root tags to populate SymbolClass / ExportAssets */
static void collect_root_symbols(SwfSession *s, size_t start, size_t end) {
    size_t pos = start;
    while (pos < end) {
        uint16_t code = 0;
        uint32_t length = 0, is_long = 0;
        uint64_t data_start = asm_swf_read_tag_header(s->normalized_bytes, pos, end, &code, &length, &is_long);
        if (!data_start) break;
        size_t avail = end - (size_t)data_start;
        size_t clamped = (length > avail) ? avail : length;
        const uint8_t *slice = s->normalized_bytes + data_start;
        if ((code == 76 || code == 56) && clamped >= 2) {
            uint16_t count = (uint16_t)(slice[0] | (slice[1] << 8));
            size_t p = 2;
            for (uint16_t i = 0; i < count; i++) {
                if (p + 2 > clamped) break;
                uint16_t sym_id = (uint16_t)(slice[p] | (slice[p + 1] << 8));
                p += 2;
                char *name = read_cstring_strict(slice, clamped, &p);
                if (!name) break;
                s->symbols = (SymbolEntry *)realloc(s->symbols, (s->symbol_count + 1) * sizeof(SymbolEntry));
                s->symbols[s->symbol_count].id = sym_id;
                s->symbols[s->symbol_count].name = name;
                s->symbol_count++;
            }
        }
        pos = (size_t)data_start + clamped;
        if (code == 0) break;
    }
}

static void walk_tag_stream(
    SwfSession *s,
    size_t start,
    size_t end,
    const int *parent_path,
    int parent_len,
    const char *scope_label,
    int depth
) {
    if (depth > 8) return;
    size_t pos = start;
    int index = 0;
    while (pos < end) {
        uint16_t code = 0;
        uint32_t length = 0, is_long = 0;
        uint64_t data_start_u64 = asm_swf_read_tag_header(s->normalized_bytes, pos, end, &code, &length, &is_long);
        if (!data_start_u64 || length > 100 * 1024 * 1024) break;
        size_t data_start = (size_t)data_start_u64;
        size_t avail = end - data_start;
        size_t clamped = (length > avail) ? avail : length;
        const uint8_t *tag_slice = s->normalized_bytes + data_start;

        if (depth == 0) s->tag_count++;

        int tag_path[16];
        memcpy(tag_path, parent_path, parent_len * sizeof(int));
        tag_path[parent_len] = index;
        int tag_path_len = parent_len + 1;

        char prefix[128];
        format_tag_prefix(tag_path, tag_path_len, prefix, sizeof(prefix));

        if (code == 12) {
            char id[160], disp[256];
            snprintf(id, sizeof(id), "%s/actions", prefix);
            if (scope_label[0]) snprintf(disp, sizeof(disp), "%s :: FrameAction (tag %d)", scope_label, index);
            else snprintf(disp, sizeof(disp), "FrameAction (tag %d)", index);
            add_avm1_block(s, id, index, tag_path, tag_path_len, code, "frame_action", disp, data_start, tag_slice, clamped);
        } else if (code == 59 && clamped >= 2) {
            uint16_t sid = (uint16_t)(tag_slice[0] | (tag_slice[1] << 8));
            const char *sym = lookup_symbol(s, sid);
            char sym_buf[64];
            if (!sym) { snprintf(sym_buf, sizeof(sym_buf), "sprite_%u", sid); sym = sym_buf; }
            char id[160], disp[256];
            snprintf(id, sizeof(id), "%s/actions", prefix);
            if (scope_label[0]) snprintf(disp, sizeof(disp), "%s :: InitAction %s (tag %d)", scope_label, sym, index);
            else snprintf(disp, sizeof(disp), "InitAction %s (tag %d)", sym, index);
            add_avm1_block(s, id, index, tag_path, tag_path_len, code, "init_action", disp, data_start + 2, tag_slice + 2, clamped - 2);
        } else if (code == 7 && clamped >= 3) {
            size_t bpos = 2;
            if (skip_button_records(tag_slice, clamped, &bpos, 0) && bpos < clamped) {
                uint16_t bid = (uint16_t)(tag_slice[0] | (tag_slice[1] << 8));
                char id[160], disp[256];
                snprintf(id, sizeof(id), "%s/button:0", prefix);
                if (scope_label[0]) snprintf(disp, sizeof(disp), "%s :: Button %u actions (tag %d)", scope_label, bid, index);
                else snprintf(disp, sizeof(disp), "Button %u actions (tag %d)", bid, index);
                add_avm1_block(s, id, index, tag_path, tag_path_len, code, "button_action", disp, data_start + bpos, tag_slice + bpos, clamped - bpos);
            }
        } else if (code == 34 && clamped >= 5) {
            uint16_t bid = (uint16_t)(tag_slice[0] | (tag_slice[1] << 8));
            uint16_t action_off = (uint16_t)(tag_slice[3] | (tag_slice[4] << 8));
            if (action_off > 0 && 3u + action_off <= clamped) {
                size_t cpos = 3u + action_off;
                int cond_idx = 0;
                while (cpos + 4 <= clamped) {
                    uint16_t cond_size = (uint16_t)(tag_slice[cpos] | (tag_slice[cpos + 1] << 8));
                    size_t astart = cpos + 4;
                    size_t next = (cond_size == 0) ? clamped : (cpos + cond_size);
                    if (next > clamped || next < astart) break;
                    if (next > astart) {
                        char id[160], disp[256];
                        snprintf(id, sizeof(id), "%s/button2:%d", prefix, cond_idx);
                        if (scope_label[0]) snprintf(disp, sizeof(disp), "%s :: Button2 %u cond %d (tag %d)", scope_label, bid, cond_idx, index);
                        else snprintf(disp, sizeof(disp), "Button2 %u cond %d (tag %d)", bid, cond_idx, index);
                        add_avm1_block(s, id, index, tag_path, tag_path_len, code, "button_condition", disp, data_start + astart, tag_slice + astart, next - astart);
                        cond_idx++;
                    }
                    if (cond_size == 0) break;
                    cpos = next;
                }
            }
        } else if ((code == 26 || code == 70) && clamped >= 3) {
            int is_p3 = (code == 70);
            uint8_t flags = tag_slice[0];
            uint8_t flags2 = is_p3 && clamped >= 4 ? tag_slice[1] : 0;
            if (flags & 0x80) {
                size_t p = is_p3 ? 4 : 3;
                int ok = (p <= clamped);
                if (ok && is_p3 && ((flags2 & 0x08) || ((flags2 & 0x10) && (flags & 0x02)))) {
                    char *tmp = read_cstring_strict(tag_slice, clamped, &p);
                    if (!tmp) ok = 0; else free(tmp);
                }
                if (ok && (flags & 0x02)) { if (p + 2 > clamped) ok = 0; else p += 2; }
                if (ok && (flags & 0x04)) { if (!skip_matrix(tag_slice, clamped, &p)) ok = 0; }
                if (ok && (flags & 0x08)) { if (!skip_cxform_alpha(tag_slice, clamped, &p)) ok = 0; }
                if (ok && (flags & 0x10)) { if (p + 2 > clamped) ok = 0; else p += 2; }
                if (ok && (flags & 0x20)) {
                    char *tmp = read_cstring_strict(tag_slice, clamped, &p);
                    if (!tmp) ok = 0; else free(tmp);
                }
                if (ok && (flags & 0x40)) { if (p + 2 > clamped) ok = 0; else p += 2; }
                if (ok && is_p3) {
                    if ((flags2 & 0x01) && !skip_filter_list(tag_slice, clamped, &p)) ok = 0;
                    if (ok && (flags2 & 0x02)) { if (p >= clamped) ok = 0; else p++; }
                    if (ok && (flags2 & 0x04)) { if (p >= clamped) ok = 0; else p++; }
                    if (ok && (flags2 & 0x20)) { if (p >= clamped) ok = 0; else p++; }
                    if (ok && (flags2 & 0x40)) { if (p + 4 > clamped) ok = 0; else p += 4; }
                }
                size_t ev_sz = (s->version >= 6) ? 4 : 2;
                if (ok && p + 2 + ev_sz <= clamped) {
                    p += 2 + ev_sz;
                    int clip_idx = 0;
                    while (p + ev_sz <= clamped) {
                        uint32_t ev_flags = (ev_sz == 4)
                            ? (uint32_t)(tag_slice[p] | (tag_slice[p+1]<<8) | (tag_slice[p+2]<<16) | (tag_slice[p+3]<<24))
                            : (uint32_t)(tag_slice[p] | (tag_slice[p+1]<<8));
                        p += ev_sz;
                        if (ev_flags == 0) break;
                        if (p + 4 > clamped) break;
                        uint32_t rec_sz = (uint32_t)(tag_slice[p] | (tag_slice[p+1]<<8) | (tag_slice[p+2]<<16) | (tag_slice[p+3]<<24));
                        p += 4;
                        size_t rec_end = p + rec_sz;
                        if (rec_end > clamped) break;
                        size_t astart = p;
                        if (s->version >= 6 && (ev_flags & 0x00020000u)) {
                            if (astart >= rec_end) break;
                            astart++;
                        }
                        if (rec_end > astart) {
                            char id[160], disp[256];
                            snprintf(id, sizeof(id), "%s/clip:%d", prefix, clip_idx);
                            if (scope_label[0]) snprintf(disp, sizeof(disp), "%s :: ClipAction %d (tag %d)", scope_label, clip_idx, index);
                            else snprintf(disp, sizeof(disp), "ClipAction %d (tag %d)", clip_idx, index);
                            add_avm1_block(s, id, index, tag_path, tag_path_len, code, "clip_action", disp, data_start + astart, tag_slice + astart, rec_end - astart);
                            clip_idx++;
                        }
                        p = rec_end;
                    }
                }
            }
        } else if (code == 39 && clamped >= 4) {
            uint16_t sid = (uint16_t)(tag_slice[0] | (tag_slice[1] << 8));
            const char *sym = lookup_symbol(s, sid);
            char sym_buf[64];
            if (!sym) { snprintf(sym_buf, sizeof(sym_buf), "Sprite %u", sid); sym = sym_buf; }
            char child_scope[256];
            if (scope_label[0]) snprintf(child_scope, sizeof(child_scope), "%s :: %s (tag %d)", scope_label, sym, index);
            else snprintf(child_scope, sizeof(child_scope), "%s (tag %d)", sym, index);
            walk_tag_stream(s, data_start + 4, data_start + clamped, tag_path, tag_path_len, child_scope, depth + 1);
        } else if (code == 72 || code == 82) {
            AbcFile *abc = parse_do_abc(tag_slice, clamped, code == 82);
            if (abc) {
                s->abc_files = (AbcFile **)realloc(s->abc_files, (s->abc_count + 1) * sizeof(AbcFile *));
                s->abc_files[s->abc_count++] = abc;
                for (size_t b = 0; b < abc->body_count; b++) {
                    AbcBody *body = &abc->bodies[b];
                    const char *m_label = NULL;
                    char fallback_m[32];
                    if (body->method_index < (int)abc->method_count && abc->method_trait_names[body->method_index]) {
                        m_label = abc->method_trait_names[body->method_index];
                    } else if (body->method_index < (int)abc->method_count && abc->method_raw_names[body->method_index] && abc->method_raw_names[body->method_index][0]) {
                        m_label = abc->method_raw_names[body->method_index];
                    } else {
                        snprintf(fallback_m, sizeof(fallback_m), "method_%d", body->method_index);
                        m_label = fallback_m;
                    }
                    ScriptUnit *u = session_alloc_unit(s);
                    char id[160];
                    snprintf(id, sizeof(id), "%s/method:%d", prefix, body->method_index);
                    u->id = strdup(id);
                    u->tag_index = index;
                    memcpy(u->tag_path, tag_path, tag_path_len * sizeof(int));
                    u->tag_path_len = tag_path_len;
                    u->tag_code = code;
                    u->language = strdup("avm2");
                    u->kind = strdup("abc_method");
                    if (abc->abc_name && abc->abc_name[0]) {
                        size_t dl = strlen(abc->abc_name) + 4 + strlen(m_label) + 1;
                        u->display_name = (char *)malloc(dl);
                        snprintf(u->display_name, dl, "%s :: %s", abc->abc_name, m_label);
                    } else {
                        u->display_name = strdup(m_label);
                    }
                    u->byte_start = data_start + abc->abc_header_offset + body->code_offset;
                    u->byte_len = body->code_len;
                    u->slice = body->code;
                    compute_sha256_hex(body->code, body->code_len, u->sha256);
                    u->abc = abc;
                    u->abc_body_index = b;
                }
            }
        }

        pos = data_start + clamped;
        index++;
        if (code == 0) break;
    }
}

static SwfSession *open_swf_file(const char *path, char **out_err) {
    FILE *fp = fopen(path, "rb");
    if (!fp) { *out_err = strdup("Cannot open SWF file"); return NULL; }
    fseek(fp, 0, SEEK_END);
    long fsize = ftell(fp);
    fseek(fp, 0, SEEK_SET);
    if (fsize < 8 || fsize > 100 * 1024 * 1024) {
        fclose(fp);
        *out_err = strdup("SWF file length outside valid bounds");
        return NULL;
    }
    uint8_t *raw = (uint8_t *)malloc((size_t)fsize);
    if (fread(raw, 1, (size_t)fsize, fp) != (size_t)fsize) {
        free(raw); fclose(fp);
        *out_err = strdup("Failed to read SWF file");
        return NULL;
    }
    fclose(fp);

    if (memcmp(raw, "FWS", 3) != 0 && memcmp(raw, "CWS", 3) != 0 && memcmp(raw, "ZWS", 3) != 0) {
        free(raw);
        *out_err = strdup("Expected FWS, CWS or ZWS signature");
        return NULL;
    }
    uint8_t version = raw[3];
    uint32_t file_length = (uint32_t)(raw[4] | (raw[5]<<8) | (raw[6]<<16) | (raw[7]<<24));
    if (file_length < 8 || file_length > 100 * 1024 * 1024) {
        free(raw);
        *out_err = strdup("Declared SWF length is outside supported bounds");
        return NULL;
    }

    uint8_t *norm = NULL;
    size_t norm_len = 0;
    if (memcmp(raw, "FWS", 3) == 0) {
        norm_len = (size_t)fsize;
        norm = (uint8_t *)malloc(norm_len);
        memcpy(norm, raw, norm_len);
    } else if (memcmp(raw, "CWS", 3) == 0) {
        size_t body_len = 0;
        uint8_t *body = zlib_decompress_cws(raw + 8, (size_t)fsize - 8, file_length, &body_len);
        if (!body) { free(raw); *out_err = strdup("Zlib decompression failed"); return NULL; }
        norm_len = 8 + body_len;
        norm = (uint8_t *)malloc(norm_len);
        memcpy(norm, "FWS", 3);
        norm[3] = version;
        memcpy(norm + 4, raw + 4, 4);
        memcpy(norm + 8, body, body_len);
        free(body);
    } else {
        size_t expected_body = (file_length > 8) ? (file_length - 8) : 0;
        uint8_t *body = lzma_decompress_zws(raw, (size_t)fsize, expected_body);
        if (!body) { free(raw); *out_err = strdup("LZMA decompression failed"); return NULL; }
        norm_len = 8 + expected_body;
        norm = (uint8_t *)malloc(norm_len);
        memcpy(norm, "FWS", 3);
        norm[3] = version;
        memcpy(norm + 4, raw + 4, 4);
        memcpy(norm + 8, body, expected_body);
        free(body);
    }

    if (norm_len < 12) {
        free(raw); free(norm);
        *out_err = strdup("Decompressed SWF shorter than stage header");
        return NULL;
    }

    size_t bit_pos = 0;
    uint32_t nbits = 0;
    int32_t xmin = 0, xmax = 0, ymin = 0, ymax = 0;
    if (!asm_bit_read_u32(norm + 8, norm_len - 8, &bit_pos, 5, &nbits) ||
        !bit_read_s32(norm + 8, norm_len - 8, &bit_pos, nbits, &xmin) ||
        !bit_read_s32(norm + 8, norm_len - 8, &bit_pos, nbits, &xmax) ||
        !bit_read_s32(norm + 8, norm_len - 8, &bit_pos, nbits, &ymin) ||
        !bit_read_s32(norm + 8, norm_len - 8, &bit_pos, nbits, &ymax)) {
        free(raw); free(norm);
        *out_err = strdup("Truncated SWF RECT");
        return NULL;
    }
    size_t rect_end = 8 + ((bit_pos + 7) >> 3);
    if (rect_end + 4 > norm_len) {
        free(raw); free(norm);
        *out_err = strdup("Truncated frame header after RECT");
        return NULL;
    }
    uint16_t frame_rate_raw = (uint16_t)(norm[rect_end] | (norm[rect_end + 1] << 8));
    uint16_t frame_count = (uint16_t)(norm[rect_end + 2] | (norm[rect_end + 3] << 8));
    size_t tag_stream_start = rect_end + 4;

    SwfSession *s = (SwfSession *)calloc(1, sizeof(SwfSession));
    s->path = strdup(path);
    compute_sha256_hex(raw, (size_t)fsize, s->file_sha256);
    compute_sha256_hex(norm, norm_len, s->normalized_sha256);
    memcpy(s->compression, raw, 3);
    s->compression[3] = '\0';
    s->version = version;
    s->file_length = file_length;
    s->stage_width_twips = xmax - xmin;
    s->stage_height_twips = ymax - ymin;
    s->frame_rate = (double)frame_rate_raw / 256.0;
    s->frame_count = frame_count;
    s->normalized_bytes = norm;
    s->normalized_len = norm_len;
    free(raw);

    collect_root_symbols(s, tag_stream_start, norm_len);
    int root_path[1] = {0};
    walk_tag_stream(s, tag_stream_start, norm_len, root_path, 0, "", 0);
    return s;
}

/* ============================================================================
 * AVM1 & AVM2 Disassembler + Structured Decompiler in C + x86_64 ASM
 * ============================================================================ */
static const char *avm1_action_name(uint8_t code) {
    switch (code) {
        case 0x00: return "End";
        case 0x04: return "NextFrame";
        case 0x05: return "PrevFrame";
        case 0x06: return "Play";
        case 0x07: return "Stop";
        case 0x08: return "ToggleQuality";
        case 0x09: return "StopSounds";
        case 0x0A: return "Add";
        case 0x0B: return "Subtract";
        case 0x0C: return "Multiply";
        case 0x0D: return "Divide";
        case 0x0E: return "Equals";
        case 0x0F: return "Less";
        case 0x10: return "And";
        case 0x11: return "Or";
        case 0x12: return "Not";
        case 0x13: return "StringEquals";
        case 0x14: return "StringLength";
        case 0x15: return "StringExtract";
        case 0x17: return "Pop";
        case 0x18: return "ToInteger";
        case 0x1C: return "GetVariable";
        case 0x1D: return "SetVariable";
        case 0x20: return "SetTarget2";
        case 0x21: return "StringAdd";
        case 0x22: return "GetProperty";
        case 0x23: return "SetProperty";
        case 0x24: return "CloneSprite";
        case 0x25: return "RemoveSprite";
        case 0x26: return "Trace";
        case 0x27: return "StartDrag";
        case 0x28: return "EndDrag";
        case 0x29: return "StringLess";
        case 0x2A: return "Throw";
        case 0x2B: return "CastOp";
        case 0x2C: return "ImplementsOp";
        case 0x30: return "RandomNumber";
        case 0x34: return "GetTime";
        case 0x3A: return "Delete";
        case 0x3B: return "Delete2";
        case 0x3C: return "DefineLocal";
        case 0x3D: return "CallFunction";
        case 0x3E: return "Return";
        case 0x3F: return "Modulo";
        case 0x40: return "NewObject";
        case 0x41: return "DefineLocal2";
        case 0x42: return "InitArray";
        case 0x43: return "InitObject";
        case 0x44: return "TypeOf";
        case 0x47: return "Add2";
        case 0x48: return "Less2";
        case 0x49: return "Equals2";
        case 0x4A: return "ToNumber";
        case 0x4B: return "ToString";
        case 0x4C: return "PushDuplicate";
        case 0x4D: return "StackSwap";
        case 0x4E: return "GetMember";
        case 0x4F: return "SetMember";
        case 0x50: return "Increment";
        case 0x51: return "Decrement";
        case 0x52: return "CallMethod";
        case 0x53: return "NewMethod";
        case 0x54: return "InstanceOf";
        case 0x55: return "Enumerate2";
        case 0x60: return "BitAnd";
        case 0x61: return "BitOr";
        case 0x62: return "BitXor";
        case 0x63: return "BitLShift";
        case 0x64: return "BitRShift";
        case 0x65: return "BitURShift";
        case 0x66: return "StrictEquals";
        case 0x67: return "Greater";
        case 0x68: return "StringGreater";
        case 0x69: return "Extends";
        case 0x81: return "GotoFrame";
        case 0x83: return "GetURL";
        case 0x87: return "StoreRegister";
        case 0x88: return "ConstantPool";
        case 0x8A: return "WaitForFrame";
        case 0x8B: return "SetTarget";
        case 0x8C: return "GotoLabel";
        case 0x8E: return "DefineFunction2";
        case 0x8F: return "Try";
        case 0x94: return "With";
        case 0x96: return "Push";
        case 0x99: return "Jump";
        case 0x9A: return "GetURL2";
        case 0x9B: return "DefineFunction";
        case 0x9D: return "If";
        case 0x9E: return "Call";
        case 0x9F: return "GotoFrame2";
        default: return "UnknownAction";
    }
}

static const char *avm2_op_name(uint8_t op) {
    switch (op) {
        case 0x01: return "bkpt";
        case 0x02: return "nop";
        case 0x03: return "throw";
        case 0x04: return "getsuper";
        case 0x05: return "setsuper";
        case 0x06: return "dxns";
        case 0x07: return "dxnslate";
        case 0x08: return "kill";
        case 0x09: return "label";
        case 0x0C: return "ifnlt";
        case 0x0D: return "ifnle";
        case 0x0E: return "ifngt";
        case 0x0F: return "ifnge";
        case 0x10: return "jump";
        case 0x11: return "iftrue";
        case 0x12: return "iffalse";
        case 0x13: return "ifeq";
        case 0x14: return "ifne";
        case 0x15: return "iflt";
        case 0x16: return "ifle";
        case 0x17: return "ifgt";
        case 0x18: return "ifge";
        case 0x19: return "ifstricteq";
        case 0x1A: return "ifstrictne";
        case 0x1B: return "lookupswitch";
        case 0x1C: return "pushwith";
        case 0x1D: return "popscope";
        case 0x1E: return "nextname";
        case 0x1F: return "hasnext";
        case 0x20: return "pushnull";
        case 0x21: return "pushundefined";
        case 0x23: return "nextvalue";
        case 0x24: return "pushbyte";
        case 0x25: return "pushshort";
        case 0x26: return "pushtrue";
        case 0x27: return "pushfalse";
        case 0x28: return "pushnan";
        case 0x29: return "pop";
        case 0x2A: return "dup";
        case 0x2B: return "swap";
        case 0x2C: return "pushstring";
        case 0x2D: return "pushint";
        case 0x2E: return "pushuint";
        case 0x2F: return "pushdouble";
        case 0x30: return "pushscope";
        case 0x31: return "pushnamespace";
        case 0x32: return "hasnext2";
        case 0x40: return "newfunction";
        case 0x41: return "call";
        case 0x42: return "construct";
        case 0x43: return "callmethod";
        case 0x44: return "callstatic";
        case 0x45: return "callsuper";
        case 0x46: return "callproperty";
        case 0x47: return "returnvoid";
        case 0x48: return "returnvalue";
        case 0x49: return "constructsuper";
        case 0x4A: return "constructprop";
        case 0x4C: return "callproplex";
        case 0x4E: return "callsupervoid";
        case 0x4F: return "callpropvoid";
        case 0x53: return "applytype";
        case 0x55: return "newobject";
        case 0x56: return "newarray";
        case 0x57: return "newactivation";
        case 0x58: return "newclass";
        case 0x59: return "getdescendants";
        case 0x5A: return "newcatch";
        case 0x5D: return "findpropstrict";
        case 0x5E: return "findproperty";
        case 0x5F: return "finddef";
        case 0x60: return "getlex";
        case 0x61: return "setproperty";
        case 0x62: return "getlocal";
        case 0x63: return "setlocal";
        case 0x64: return "getglobalscope";
        case 0x65: return "getscopeobject";
        case 0x66: return "getproperty";
        case 0x68: return "initproperty";
        case 0x6A: return "deleteproperty";
        case 0x6C: return "getslot";
        case 0x6D: return "setslot";
        case 0x6E: return "getglobalslot";
        case 0x6F: return "setglobalslot";
        case 0x70: return "convert_s";
        case 0x73: return "convert_i";
        case 0x74: return "convert_u";
        case 0x75: return "convert_d";
        case 0x76: return "convert_b";
        case 0x77: return "convert_o";
        case 0x80: return "coerce";
        case 0x82: return "coerce_a";
        case 0x85: return "coerce_s";
        case 0x86: return "astype";
        case 0x87: return "astypelate";
        case 0x90: return "negate";
        case 0x91: return "increment";
        case 0x92: return "inclocal";
        case 0x93: return "decrement";
        case 0x94: return "declocal";
        case 0x95: return "typeof";
        case 0x96: return "not";
        case 0x97: return "bitnot";
        case 0xA0: return "add";
        case 0xA1: return "subtract";
        case 0xA2: return "multiply";
        case 0xA3: return "divide";
        case 0xA4: return "modulo";
        case 0xA5: return "lshift";
        case 0xA6: return "rshift";
        case 0xA7: return "urshift";
        case 0xA8: return "bitand";
        case 0xA9: return "bitor";
        case 0xAA: return "bitxor";
        case 0xAB: return "equals";
        case 0xAC: return "strictequals";
        case 0xAD: return "lessthan";
        case 0xAE: return "lessequals";
        case 0xAF: return "greaterthan";
        case 0xB0: return "greaterequals";
        case 0xB1: return "instanceof";
        case 0xB2: return "istype";
        case 0xB3: return "istypelate";
        case 0xB4: return "in";
        case 0xC0: return "increment_i";
        case 0xC1: return "decrement_i";
        case 0xC2: return "inclocal_i";
        case 0xC3: return "declocal_i";
        case 0xC4: return "negate_i";
        case 0xC5: return "add_i";
        case 0xC6: return "subtract_i";
        case 0xC7: return "multiply_i";
        case 0xD0: return "getlocal_0";
        case 0xD1: return "getlocal_1";
        case 0xD2: return "getlocal_2";
        case 0xD3: return "getlocal_3";
        case 0xD4: return "setlocal_0";
        case 0xD5: return "setlocal_1";
        case 0xD6: return "setlocal_2";
        case 0xD7: return "setlocal_3";
        case 0xEF: return "debug";
        case 0xF0: return "debugline";
        case 0xF1: return "debugfile";
        default: return "unknown_op";
    }
}

static const char *resolve_avm1_reg_name(const ScriptUnit *u, uint8_t r) {
    int reg_idx = 1;
    if (u->avm1_flags & 0x01) { if (r == reg_idx) return "this"; reg_idx++; }
    if (u->avm1_flags & 0x04) { if (r == reg_idx) return "arguments"; reg_idx++; }
    if (u->avm1_flags & 0x10) { if (r == reg_idx) return "super"; reg_idx++; }
    if (u->avm1_flags & 0x40) { if (r == reg_idx) return "_root"; reg_idx++; }
    if (u->avm1_flags & 0x80) { if (r == reg_idx) return "_parent"; reg_idx++; }
    if (u->avm1_flags & 0x100) { if (r == reg_idx) return "_global"; reg_idx++; }
    for (size_t k = 0; k < u->reg_count && k < u->param_count; k++) {
        if (u->registers[k] == (int)r && u->params[k][0]) return u->params[k];
    }
    return NULL;
}

static void disassemble_unit(
    const ScriptUnit *u,
    StrBuf *out_pcode,
    int *out_partial,
    size_t *out_instr_count,
    StrBuf *out_source,
    int *out_structured
) {
    sb_init(out_pcode);
    sb_init(out_source);
    *out_partial = 0;
    *out_instr_count = 0;
    *out_structured = 0;

    if (strcmp(u->language, "avm1") == 0) {
        size_t pos = 0, end = u->byte_len;
        const uint8_t *buf = u->slice;
        char **constants = NULL;
        size_t const_count = u->inherited_constant_count;
        if (const_count > 0) {
            constants = (char **)malloc(const_count * sizeof(char *));
            for (size_t i = 0; i < const_count; i++) constants[i] = strdup(u->inherited_constants[i]);
        }

        char *stack[256];
        int sp = 0;
        int can_struct = 1;
        size_t close_while_at = (size_t)-1;
        StrBuf stmts;
        sb_init(&stmts);
        const char *ind = u->is_avm1_func ? "    " : "";

        while (pos < end) {
            if (pos == close_while_at) {
                sb_append(&stmts, ind);
                sb_append(&stmts, "}\n");
                close_while_at = (size_t)-1;
            }
            size_t offset = pos;
            uint8_t code = 0;
            uint16_t length = 0;
            size_t pstart = asm_avm1_scan_action(buf, pos, end, &code, &length);
            if (!pstart || pstart + length > end) {
                *out_partial = 1;
                can_struct = 0;
                break;
            }
            const uint8_t *payload = buf + pstart;
            pos = pstart + length;

            if (*out_instr_count > 0) sb_append_char(out_pcode, '\n');
            (*out_instr_count)++;

            char line_hdr[64];
            const char *aname = avm1_action_name(code);
            snprintf(line_hdr, sizeof(line_hdr), "%04zx: %-18s ", offset, aname);
            sb_append(out_pcode, line_hdr);

            if (code == 0x88 && length >= 2) {
                uint16_t cnt = (uint16_t)(payload[0] | (payload[1] << 8));
                for (size_t i = 0; i < const_count; i++) free(constants[i]);
                free(constants);
                constants = (char **)calloc(cnt > 0 ? cnt : 1, sizeof(char *));
                const_count = 0;
                size_t p = 2;
                for (uint16_t i = 0; i < cnt && p < length; i++) {
                    constants[const_count++] = read_cstring_lossy(payload, length, &p);
                }
                char tmp[64];
                snprintf(tmp, sizeof(tmp), "count=%zu", const_count);
                sb_append(out_pcode, tmp);
            } else if (code == 0x96) {
                size_t p = 0;
                int first = 1;
                while (p < length) {
                    if (!first) sb_append(out_pcode, ", ");
                    first = 0;
                    uint8_t kind = payload[p++];
                    char atom_buf[256];
                    atom_buf[0] = '\0';
                    if (kind == 0) {
                        char *s = read_cstring_lossy(payload, length, &p);
                        StrBuf q; sb_init(&q); sb_append_json_str(&q, s);
                        sb_append(out_pcode, q.buf);
                        if (can_struct && sp < 250) stack[sp++] = strdup(q.buf);
                        sb_free(&q); free(s);
                    } else if (kind == 1 && p + 4 <= length) {
                        float fv = 0; memcpy(&fv, payload + p, 4); p += 4;
                        snprintf(atom_buf, sizeof(atom_buf), "%g", fv);
                        sb_append(out_pcode, atom_buf);
                        if (can_struct && sp < 250) stack[sp++] = strdup(atom_buf);
                    } else if (kind == 2) {
                        sb_append(out_pcode, "null");
                        if (can_struct && sp < 250) stack[sp++] = strdup("null");
                    } else if (kind == 3) {
                        sb_append(out_pcode, "undefined");
                        if (can_struct && sp < 250) stack[sp++] = strdup("undefined");
                    } else if (kind == 4 && p < length) {
                        uint8_t r = payload[p++];
                        snprintf(atom_buf, sizeof(atom_buf), "r:%u", r);
                        sb_append(out_pcode, atom_buf);
                        if (can_struct && sp < 250) {
                            const char *mapped = resolve_avm1_reg_name(u, r);
                            if (mapped) stack[sp++] = strdup(mapped);
                            else { snprintf(atom_buf, sizeof(atom_buf), "reg%u", r); stack[sp++] = strdup(atom_buf); }
                        }
                    } else if (kind == 5 && p < length) {
                        uint8_t b = payload[p++];
                        const char *bs = b ? "true" : "false";
                        sb_append(out_pcode, bs);
                        if (can_struct && sp < 250) stack[sp++] = strdup(bs);
                    } else if (kind == 6 && p + 8 <= length) {
                        uint32_t lo = 0, hi = 0;
                        memcpy(&lo, payload + p, 4);
                        memcpy(&hi, payload + p + 4, 4);
                        p += 8;
                        uint64_t bits = ((uint64_t)lo << 32) | hi;
                        double dv = 0; memcpy(&dv, &bits, 8);
                        snprintf(atom_buf, sizeof(atom_buf), "%g", dv);
                        sb_append(out_pcode, atom_buf);
                        if (can_struct && sp < 250) stack[sp++] = strdup(atom_buf);
                    } else if (kind == 7 && p + 4 <= length) {
                        int32_t iv = 0; memcpy(&iv, payload + p, 4); p += 4;
                        snprintf(atom_buf, sizeof(atom_buf), "%d", iv);
                        sb_append(out_pcode, atom_buf);
                        if (can_struct && sp < 250) stack[sp++] = strdup(atom_buf);
                    } else if ((kind == 8 && p < length) || (kind == 9 && p + 2 <= length)) {
                        uint16_t idx = (kind == 8) ? payload[p++] : (uint16_t)(payload[p] | (payload[p+1]<<8));
                        if (kind == 9) p += 2;
                        if (idx < const_count) {
                            StrBuf q; sb_init(&q); sb_append_json_str(&q, constants[idx]);
                            char c_hdr[32]; snprintf(c_hdr, sizeof(c_hdr), "c:%u(", idx);
                            sb_append(out_pcode, c_hdr); sb_append(out_pcode, q.buf); sb_append_char(out_pcode, ')');
                            if (can_struct && sp < 250) stack[sp++] = strdup(q.buf);
                            sb_free(&q);
                        } else {
                            snprintf(atom_buf, sizeof(atom_buf), "c:%u", idx);
                            sb_append(out_pcode, atom_buf);
                            if (can_struct && sp < 250) stack[sp++] = strdup(atom_buf);
                        }
                    } else {
                        *out_partial = 1;
                        can_struct = 0;
                        break;
                    }
                }
            } else {
                char op_hex[32];
                snprintf(op_hex, sizeof(op_hex), "(0x%02x)", code);
                sb_append(out_pcode, op_hex);
                const char *cur_ind = (close_while_at != (size_t)-1) ? (u->is_avm1_func ? "        " : "    ") : ind;
                if (can_struct) {
                    if (code == 0x00) {
                        /* End */
                    } else if (code == 0x1C && sp >= 1) {
                        char *top = stack[sp - 1];
                        size_t tl = strlen(top);
                        if (tl >= 2 && top[0] == '"' && top[tl - 1] == '"') {
                            memmove(top, top + 1, tl - 2);
                            top[tl - 2] = '\0';
                        }
                    } else if ((code == 0x1D || code == 0x3C) && sp >= 2) {
                        char *val = stack[--sp];
                        char *nm = stack[--sp];
                        size_t nl = strlen(nm);
                        if (nl >= 2 && nm[0] == '"' && nm[nl - 1] == '"') { memmove(nm, nm + 1, nl - 2); nm[nl - 2] = '\0'; }
                        sb_append(&stmts, cur_ind);
                        if (code == 0x3C) sb_append(&stmts, "var ");
                        sb_append(&stmts, nm); sb_append(&stmts, " = "); sb_append(&stmts, val); sb_append(&stmts, ";\n");
                        free(val); free(nm);
                    } else if (code == 0x26 && sp >= 1) {
                        char *val = stack[--sp];
                        sb_append(&stmts, cur_ind); sb_append(&stmts, "trace("); sb_append(&stmts, val); sb_append(&stmts, ");\n");
                        free(val);
                    } else if (code == 0x3E && sp >= 1) {
                        char *val = stack[--sp];
                        sb_append(&stmts, cur_ind); sb_append(&stmts, "return "); sb_append(&stmts, val); sb_append(&stmts, ";\n");
                        free(val);
                    } else if (code == 0x17 && sp >= 1) {
                        char *val = stack[--sp];
                        sb_append(&stmts, cur_ind); sb_append(&stmts, val); sb_append(&stmts, ";\n");
                        free(val);
                    } else if (code == 0x06) {
                        sb_append(&stmts, cur_ind); sb_append(&stmts, "play();\n");
                    } else if (code == 0x07) {
                        sb_append(&stmts, cur_ind); sb_append(&stmts, "stop();\n");
                    } else if (code == 0x41 && sp >= 1) {
                        char *nm = stack[--sp];
                        size_t nl = strlen(nm);
                        if (nl >= 2 && nm[0] == '"' && nm[nl - 1] == '"') { memmove(nm, nm + 1, nl - 2); nm[nl - 2] = '\0'; }
                        sb_append(&stmts, cur_ind); sb_append(&stmts, "var "); sb_append(&stmts, nm); sb_append(&stmts, ";\n");
                        free(nm);
                    } else if (code == 0x4E && sp >= 2) {
                        char *mem = stack[--sp];
                        char *obj = stack[--sp];
                        size_t ml = strlen(mem);
                        int is_str = (ml >= 2 && mem[0] == '"' && mem[ml - 1] == '"');
                        if (is_str) { memmove(mem, mem + 1, ml - 2); mem[ml - 2] = '\0'; }
                        size_t el = strlen(obj) + strlen(mem) + 6;
                        char *expr = (char *)malloc(el);
                        if (is_str) snprintf(expr, el, "%s.%s", obj, mem);
                        else snprintf(expr, el, "%s[%s]", obj, mem);
                        stack[sp++] = expr;
                        free(obj); free(mem);
                    } else if (code == 0x4F && sp >= 3) {
                        char *val = stack[--sp];
                        char *mem = stack[--sp];
                        char *obj = stack[--sp];
                        size_t ml = strlen(mem);
                        int is_str = (ml >= 2 && mem[0] == '"' && mem[ml - 1] == '"');
                        if (is_str) { memmove(mem, mem + 1, ml - 2); mem[ml - 2] = '\0'; }
                        sb_append(&stmts, cur_ind); sb_append(&stmts, obj);
                        if (is_str) { sb_append_char(&stmts, '.'); sb_append(&stmts, mem); }
                        else { sb_append_char(&stmts, '['); sb_append(&stmts, mem); sb_append_char(&stmts, ']'); }
                        sb_append(&stmts, " = "); sb_append(&stmts, val); sb_append(&stmts, ";\n");
                        free(val); free(mem); free(obj);
                    } else if (code == 0x42 && sp >= 1) {
                        char *cnt_s = stack[--sp];
                        int cnt = atoi(cnt_s);
                        free(cnt_s);
                        if (cnt < 0 || cnt > 128 || sp < cnt) { can_struct = 0; }
                        else {
                            StrBuf arr; sb_init(&arr);
                            sb_append_char(&arr, '[');
                            for (int a = 0; a < cnt; a++) {
                                if (a > 0) sb_append(&arr, ", ");
                                char *el = stack[--sp];
                                sb_append(&arr, el);
                                free(el);
                            }
                            sb_append_char(&arr, ']');
                            stack[sp++] = arr.buf;
                        }
                    } else if (code == 0x43 && sp >= 1) {
                        char *cnt_s = stack[--sp];
                        int cnt = atoi(cnt_s);
                        free(cnt_s);
                        if (cnt < 0 || cnt > 128 || sp < cnt * 2) { can_struct = 0; }
                        else {
                            StrBuf obj; sb_init(&obj);
                            sb_append_char(&obj, '{');
                            for (int a = 0; a < cnt; a++) {
                                if (a > 0) sb_append(&obj, ", ");
                                char *v = stack[--sp];
                                char *k = stack[--sp];
                                size_t kl = strlen(k);
                                if (kl >= 2 && k[0] == '"' && k[kl - 1] == '"') { memmove(k, k + 1, kl - 2); k[kl - 2] = '\0'; }
                                sb_append(&obj, k); sb_append(&obj, ": "); sb_append(&obj, v);
                                free(k); free(v);
                            }
                            sb_append_char(&obj, '}');
                            stack[sp++] = obj.buf;
                        }
                    } else if (code == 0x3D && sp >= 2) {
                        char *fn = stack[--sp];
                        char *cnt_s = stack[--sp];
                        int cnt = atoi(cnt_s);
                        free(cnt_s);
                        if (cnt < 0 || cnt > 32 || sp < cnt) { free(fn); can_struct = 0; }
                        else {
                            StrBuf call; sb_init(&call);
                            size_t fl = strlen(fn);
                            if (fl >= 2 && fn[0] == '"' && fn[fl - 1] == '"') { memmove(fn, fn + 1, fl - 2); fn[fl - 2] = '\0'; }
                            sb_append(&call, fn); sb_append_char(&call, '(');
                            for (int a = 0; a < cnt; a++) {
                                if (a > 0) sb_append(&call, ", ");
                                char *arg = stack[--sp];
                                sb_append(&call, arg);
                                free(arg);
                            }
                            sb_append_char(&call, ')');
                            stack[sp++] = call.buf;
                            free(fn);
                        }
                    } else if (code == 0x52 && sp >= 3) {
                        char *meth = stack[--sp];
                        char *obj = stack[--sp];
                        char *cnt_s = stack[--sp];
                        int cnt = atoi(cnt_s);
                        free(cnt_s);
                        if (cnt < 0 || cnt > 32 || sp < cnt) { free(meth); free(obj); can_struct = 0; }
                        else {
                            StrBuf call; sb_init(&call);
                            size_t ml = strlen(meth);
                            if (ml >= 2 && meth[0] == '"' && meth[ml - 1] == '"') { memmove(meth, meth + 1, ml - 2); meth[ml - 2] = '\0'; }
                            sb_append(&call, obj); sb_append_char(&call, '.'); sb_append(&call, meth); sb_append_char(&call, '(');
                            for (int a = 0; a < cnt; a++) {
                                if (a > 0) sb_append(&call, ", ");
                                char *arg = stack[--sp];
                                sb_append(&call, arg);
                                free(arg);
                            }
                            sb_append_char(&call, ')');
                            stack[sp++] = call.buf;
                            free(meth); free(obj);
                        }
                    } else if (code == 0x40 && sp >= 2) {
                        char *ctor = stack[--sp];
                        char *cnt_s = stack[--sp];
                        int cnt = atoi(cnt_s);
                        free(cnt_s);
                        if (cnt < 0 || cnt > 32 || sp < cnt) { free(ctor); can_struct = 0; }
                        else {
                            StrBuf call; sb_init(&call);
                            size_t cl = strlen(ctor);
                            if (cl >= 2 && ctor[0] == '"' && ctor[cl - 1] == '"') { memmove(ctor, ctor + 1, cl - 2); ctor[cl - 2] = '\0'; }
                            sb_append(&call, "new "); sb_append(&call, ctor); sb_append_char(&call, '(');
                            for (int a = 0; a < cnt; a++) {
                                if (a > 0) sb_append(&call, ", ");
                                char *arg = stack[--sp];
                                sb_append(&call, arg);
                                free(arg);
                            }
                            sb_append_char(&call, ')');
                            stack[sp++] = call.buf;
                            free(ctor);
                        }
                    } else if (code == 0x87 && length >= 1 && sp >= 1) {
                        uint8_t r = payload[0];
                        char rbuf[64];
                        const char *mapped = resolve_avm1_reg_name(u, r);
                        if (mapped) snprintf(rbuf, sizeof(rbuf), "%s", mapped);
                        else snprintf(rbuf, sizeof(rbuf), "reg%u", r);
                        sb_append(&stmts, cur_ind); sb_append(&stmts, rbuf); sb_append(&stmts, " = ");
                        sb_append(&stmts, stack[sp - 1]); sb_append(&stmts, ";\n");
                        free(stack[sp - 1]);
                        stack[sp - 1] = strdup(rbuf);
                    } else if (code == 0x4C && sp >= 1 && sp < 250) {
                        stack[sp] = strdup(stack[sp - 1]);
                        sp++;
                    } else if (code == 0x4D && sp >= 2) {
                        char *tmp = stack[sp - 1];
                        stack[sp - 1] = stack[sp - 2];
                        stack[sp - 2] = tmp;
                    } else if ((code == 0x9B || code == 0x8E) && sp < 250) {
                        size_t p = 0;
                        char *fn_name = read_cstring_strict(payload, length, &p);
                        if (fn_name && ((code == 0x9B && p + 2 <= length) || (code == 0x8E && p + 5 <= length))) {
                            uint16_t num_params = (uint16_t)(payload[p] | (payload[p + 1] << 8));
                            p += 2;
                            int flags = 0;
                            if (code == 0x8E) {
                                flags = (int)(payload[p + 1] | (payload[p + 2] << 8));
                                p += 3;
                            }
                            char **params = (char **)calloc(num_params > 0 ? num_params : 1, sizeof(char *));
                            int *registers = (int *)calloc(num_params > 0 ? num_params : 1, sizeof(int));
                            int valid = 1;
                            for (uint16_t i = 0; i < num_params; i++) {
                                if (code == 0x8E) {
                                    if (p >= length) { valid = 0; break; }
                                    registers[i] = payload[p++];
                                }
                                char *pname = read_cstring_strict(payload, length, &p);
                                if (!pname) { valid = 0; break; }
                                params[i] = pname;
                            }
                            if (valid && p + 2 <= length) {
                                uint16_t code_size = (uint16_t)(payload[p] | (payload[p + 1] << 8));
                                if (pos + code_size <= end) {
                                    ScriptUnit sub_u;
                                    memset(&sub_u, 0, sizeof(sub_u));
                                    sub_u.language = "avm1";
                                    sub_u.kind = "function";
                                    sub_u.is_avm1_func = 1;
                                    sub_u.fn_name = fn_name;
                                    sub_u.params = params;
                                    sub_u.param_count = num_params;
                                    sub_u.registers = registers;
                                    sub_u.reg_count = (code == 0x8E) ? num_params : 0;
                                    sub_u.avm1_flags = flags;
                                    sub_u.inherited_constants = constants;
                                    sub_u.inherited_constant_count = const_count;
                                    sub_u.slice = buf + pos;
                                    sub_u.byte_len = code_size;
                                    pos += code_size;

                                    StrBuf sub_pcode, sub_src;
                                    int sub_part = 0, sub_struct = 0;
                                    size_t sub_ic = 0;
                                    disassemble_unit(&sub_u, &sub_pcode, &sub_part, &sub_ic, &sub_src, &sub_struct);
                                    sb_free(&sub_pcode);
                                    if (sub_struct && sub_src.len > 0) {
                                        /* Trim trailing newline */
                                        while (sub_src.len > 0 && (sub_src.buf[sub_src.len - 1] == '\n' || sub_src.buf[sub_src.len - 1] == '\r')) {
                                            sub_src.buf[--sub_src.len] = '\0';
                                        }
                                        if (fn_name[0]) {
                                            sb_append(&stmts, cur_ind);
                                            sb_append(&stmts, sub_src.buf);
                                            sb_append_char(&stmts, '\n');
                                            sb_free(&sub_src);
                                        } else {
                                            stack[sp++] = sub_src.buf;
                                        }
                                    } else {
                                        sb_free(&sub_src);
                                        stack[sp++] = strdup("function() { /* nested */ }");
                                    }
                                    for (uint16_t i = 0; i < num_params; i++) free(params[i]);
                                    free(params); free(registers); free(fn_name);
                                    goto next_avm1_op;
                                }
                            }
                            for (uint16_t i = 0; i < num_params; i++) free(params[i]);
                            free(params); free(registers);
                        }
                        free(fn_name);
                        stack[sp++] = strdup("function() { /* nested */ }");
                    next_avm1_op:;
                    } else if (code == 0x9D && length >= 2 && sp >= 1) {
                        int16_t rel = (int16_t)(payload[0] | (payload[1] << 8));
                        size_t target = (size_t)((ssize_t)pos + (ssize_t)rel);
                        char *cond = stack[--sp];
                        size_t cl = strlen(cond);
                        char *neg_cond = NULL;
                        if (cl >= 4 && strncmp(cond, "(!", 2) == 0 && cond[cl - 1] == ')') {
                            neg_cond = (char *)malloc(cl - 2);
                            memcpy(neg_cond, cond + 2, cl - 3);
                            neg_cond[cl - 3] = '\0';
                        } else {
                            neg_cond = (char *)malloc(cl + 5);
                            snprintf(neg_cond, cl + 5, "(!%s)", cond);
                        }
                        /* Check if target >= 5 and buf[target-5] == 0x99 (backward jump = while loop) */
                        if (target > pos && target <= end && target >= 5 && buf[target - 5] == 0x99) {
                            sb_append(&stmts, cur_ind);
                            sb_append(&stmts, "while (");
                            sb_append(&stmts, neg_cond);
                            sb_append(&stmts, ") {\n");
                            close_while_at = target - 5;
                        } else {
                            sb_append(&stmts, cur_ind);
                            sb_append(&stmts, "if (");
                            sb_append(&stmts, neg_cond);
                            sb_append(&stmts, ") {\n");
                            close_while_at = target;
                        }
                        free(neg_cond);
                        free(cond);
                    } else if (code == 0x99) {
                        /* Jump handled by structured while/if */
                    } else if (code == 0x12 && sp >= 1) {
                        char *v = stack[--sp];
                        size_t el = strlen(v) + 6;
                        char *expr = (char *)malloc(el);
                        snprintf(expr, el, "(!%s)", v);
                        stack[sp++] = expr;
                        free(v);
                    } else if ((code == 0x50 || code == 0x51) && sp >= 1) {
                        char *v = stack[--sp];
                        size_t el = strlen(v) + 8;
                        char *expr = (char *)malloc(el);
                        snprintf(expr, el, "(%s %c 1)", v, code == 0x50 ? '+' : '-');
                        stack[sp++] = expr;
                        free(v);
                    } else if ((code == 0x0A || code == 0x47 || code == 0x21 || code == 0x0B || code == 0x0C || code == 0x0D || code == 0x3F ||
                                code == 0x0E || code == 0x49 || code == 0x13 || code == 0x66 || code == 0x0F || code == 0x48 || code == 0x67 ||
                                (code >= 0x60 && code <= 0x65)) && sp >= 2) {
                        char *rhs = stack[--sp];
                        char *lhs = stack[--sp];
                        const char *bop = "+";
                        if (code == 0x0B) bop = "-";
                        else if (code == 0x0C) bop = "*";
                        else if (code == 0x0D) bop = "/";
                        else if (code == 0x3F) bop = "%";
                        else if (code == 0x0E || code == 0x49 || code == 0x13) bop = "==";
                        else if (code == 0x66) bop = "===";
                        else if (code == 0x0F || code == 0x48) bop = "<";
                        else if (code == 0x67) bop = ">";
                        else if (code == 0x60) bop = "&";
                        else if (code == 0x61) bop = "|";
                        else if (code == 0x62) bop = "^";
                        else if (code == 0x63) bop = "<<";
                        else if (code == 0x64) bop = ">>";
                        else if (code == 0x65) bop = ">>>";
                        size_t el = strlen(lhs) + strlen(rhs) + 8;
                        char *expr = (char *)malloc(el);
                        snprintf(expr, el, "(%s %s %s)", lhs, bop, rhs);
                        stack[sp++] = expr;
                        free(lhs); free(rhs);
                    } else {
                        can_struct = 0;
                    }
                }
            }
            if (code == 0x00) break;
        }
        if (close_while_at != (size_t)-1) {
            sb_append(&stmts, ind);
            sb_append(&stmts, "}\n");
        }

        for (int i = 0; i < sp; i++) free(stack[i]);
        for (size_t i = 0; i < const_count; i++) free(constants[i]);
        free(constants);

        if (can_struct && !(*out_partial)) {
            *out_structured = 1;
            if (u->is_avm1_func) {
                if (u->fn_name && u->fn_name[0]) {
                    sb_append(out_source, "function ");
                    sb_append(out_source, u->fn_name);
                    sb_append_char(out_source, '(');
                } else {
                    sb_append(out_source, "function(");
                }
                for (size_t i = 0; i < u->param_count; i++) {
                    if (i > 0) sb_append(out_source, ", ");
                    sb_append(out_source, u->params[i]);
                }
                sb_append(out_source, ") {\n");
                if (stmts.len > 0) sb_append(out_source, stmts.buf);
                else sb_append(out_source, "    // empty action block\n");
                sb_append(out_source, "}\n");
            } else {
                if (stmts.len > 0) sb_append(out_source, stmts.buf);
                else sb_append(out_source, "// empty action block\n");
            }
        }
        sb_free(&stmts);
    } else {
        /* AVM2 disassembly using asm_avm2_read_u30 */
        const AbcFile *abc = u->abc;
        const uint8_t *code = u->slice;
        size_t pos = 0, end = u->byte_len;
        int can_struct = 1;
        char *stack[256];
        int sp = 0;
        StrBuf stmts;
        sb_init(&stmts);

        while (pos < end) {
            size_t offset = pos;
            uint8_t op = code[pos++];
            const char *opname = avm2_op_name(op);
            if (*out_instr_count > 0) sb_append_char(out_pcode, '\n');
            (*out_instr_count)++;

            char hdr[64];
            snprintf(hdr, sizeof(hdr), "%04zx: %-18s ", offset, opname);
            sb_append(out_pcode, hdr);

            uint32_t a = 0, b = 0;
            char op_buf[256];
            op_buf[0] = '\0';

            if (op == 0x24) {
                if (pos >= end) { *out_partial = 1; break; }
                int8_t v = (int8_t)code[pos++];
                snprintf(op_buf, sizeof(op_buf), "%d", v);
                sb_append(out_pcode, op_buf);
                if (can_struct && sp < 250) stack[sp++] = strdup(op_buf);
            } else if (op == 0x25) {
                if (!abc_read_u30(code, &pos, end, &a)) { *out_partial = 1; break; }
                snprintf(op_buf, sizeof(op_buf), "%d", (int32_t)a);
                sb_append(out_pcode, op_buf);
                if (can_struct && sp < 250) stack[sp++] = strdup(op_buf);
            } else if (op == 0x2C || op == 0x06 || op == 0xF1) {
                if (!abc_read_u30(code, &pos, end, &a)) { *out_partial = 1; break; }
                const char *s = (a < abc->string_count) ? abc->strings[a] : "";
                StrBuf q; sb_init(&q); sb_append_json_str(&q, s);
                sb_append(out_pcode, q.buf);
                if (op == 0x2C && can_struct && sp < 250) stack[sp++] = strdup(q.buf);
                sb_free(&q);
            } else if (op == 0x2D) {
                if (!abc_read_u30(code, &pos, end, &a)) { *out_partial = 1; break; }
                snprintf(op_buf, sizeof(op_buf), "%d", (a < abc->int_count) ? abc->integers[a] : 0);
                sb_append(out_pcode, op_buf);
                if (can_struct && sp < 250) stack[sp++] = strdup(op_buf);
            } else if (op == 0x2E) {
                if (!abc_read_u30(code, &pos, end, &a)) { *out_partial = 1; break; }
                snprintf(op_buf, sizeof(op_buf), "%u", (a < abc->uint_count) ? abc->uintegers[a] : 0u);
                sb_append(out_pcode, op_buf);
                if (can_struct && sp < 250) stack[sp++] = strdup(op_buf);
            } else if (op == 0x2F) {
                if (!abc_read_u30(code, &pos, end, &a)) { *out_partial = 1; break; }
                snprintf(op_buf, sizeof(op_buf), "%g", (a < abc->double_count) ? abc->doubles[a] : 0.0);
                sb_append(out_pcode, op_buf);
                if (can_struct && sp < 250) stack[sp++] = strdup(op_buf);
            } else if (op == 0x31) {
                if (!abc_read_u30(code, &pos, end, &a)) { *out_partial = 1; break; }
                sb_append(out_pcode, (a < abc->ns_count) ? abc->namespaces[a] : "*");
                can_struct = 0;
            } else if (op == 0x04 || op == 0x05 || op == 0x59 || op == 0x5D || op == 0x5E || op == 0x5F || op == 0x60 || op == 0x61 || op == 0x66 || op == 0x68 || op == 0x6A || op == 0x80 || op == 0x86 || op == 0xB2) {
                if (!abc_read_u30(code, &pos, end, &a)) { *out_partial = 1; break; }
                const char *mn = (a < abc->mn_count) ? abc->multinames[a] : "*";
                sb_append(out_pcode, mn);
                if (can_struct) {
                    if ((op == 0x5D || op == 0x5E || op == 0x60) && sp < 250) {
                        stack[sp++] = strdup(mn);
                    } else if (op == 0x66 && sp >= 1) {
                        char *obj = stack[--sp];
                        size_t l = strlen(obj) + 1 + strlen(mn) + 1;
                        char *expr = (char *)malloc(l);
                        snprintf(expr, l, "%s.%s", obj, mn);
                        stack[sp++] = expr;
                        free(obj);
                    } else if ((op == 0x61 || op == 0x68) && sp >= 2) {
                        char *val = stack[--sp];
                        char *obj = stack[--sp];
                        sb_append(&stmts, "    ");
                        if (strcmp(obj, "this") != 0) { sb_append(&stmts, obj); sb_append_char(&stmts, '.'); }
                        sb_append(&stmts, mn); sb_append(&stmts, " = "); sb_append(&stmts, val); sb_append(&stmts, ";\n");
                        free(val); free(obj);
                    } else if (op != 0x80) {
                        can_struct = 0;
                    }
                }
            } else if (op == 0x08 || op == 0x62 || op == 0x63 || op == 0x92 || op == 0x94 || op == 0xC2 || op == 0xC3) {
                if (!abc_read_u30(code, &pos, end, &a)) { *out_partial = 1; break; }
                snprintf(op_buf, sizeof(op_buf), "r%u", a);
                sb_append(out_pcode, op_buf);
                if (can_struct) {
                    if (op == 0x62 && sp < 250) {
                        snprintf(op_buf, sizeof(op_buf), "loc%u", a);
                        stack[sp++] = strdup(op_buf);
                    } else if (op == 0x63 && sp >= 1) {
                        char *val = stack[--sp];
                        snprintf(op_buf, sizeof(op_buf), "    var loc%u = ", a);
                        sb_append(&stmts, op_buf); sb_append(&stmts, val); sb_append(&stmts, ";\n");
                        free(val);
                    } else {
                        can_struct = 0;
                    }
                }
            } else if (op == 0x41 || op == 0x42 || op == 0x49 || op == 0x53 || op == 0x55 || op == 0x56) {
                if (!abc_read_u30(code, &pos, end, &a)) { *out_partial = 1; break; }
                snprintf(op_buf, sizeof(op_buf), "argc=%u", a);
                sb_append(out_pcode, op_buf);
                if (can_struct && op == 0x49 && sp >= (int)a + 1) {
                    StrBuf args_sb; sb_init(&args_sb);
                    char *args_tmp[64];
                    for (int i = (int)a - 1; i >= 0; i--) args_tmp[i] = stack[--sp];
                    free(stack[--sp]);
                    for (uint32_t i = 0; i < a; i++) {
                        if (i > 0) sb_append(&args_sb, ", ");
                        sb_append(&args_sb, args_tmp[i]);
                        free(args_tmp[i]);
                    }
                    sb_append(&stmts, "    super("); sb_append(&stmts, args_sb.buf); sb_append(&stmts, ");\n");
                    sb_free(&args_sb);
                } else {
                    can_struct = 0;
                }
            } else if (op == 0x40 || op == 0x58 || op == 0x5A || op == 0x65 || op == 0x6C || op == 0x6D || op == 0x6E || op == 0x6F || op == 0xF0) {
                if (!abc_read_u30(code, &pos, end, &a)) { *out_partial = 1; break; }
                snprintf(op_buf, sizeof(op_buf), "%u", a);
                sb_append(out_pcode, op_buf);
                if (op != 0xF0) can_struct = 0;
            } else if (op == 0x43 || op == 0x44) {
                if (!abc_read_u30(code, &pos, end, &a) || !abc_read_u30(code, &pos, end, &b)) { *out_partial = 1; break; }
                snprintf(op_buf, sizeof(op_buf), "method=%u argc=%u", a, b);
                sb_append(out_pcode, op_buf);
                can_struct = 0;
            } else if (op == 0x45 || op == 0x46 || op == 0x4A || op == 0x4C || op == 0x4E || op == 0x4F) {
                if (!abc_read_u30(code, &pos, end, &a) || !abc_read_u30(code, &pos, end, &b)) { *out_partial = 1; break; }
                const char *mn = (a < abc->mn_count) ? abc->multinames[a] : "*";
                snprintf(op_buf, sizeof(op_buf), "%s argc=%u", mn, b);
                sb_append(out_pcode, op_buf);
                if (can_struct && b < 64 && sp >= (int)b + 1 && (op == 0x46 || op == 0x4A || op == 0x4C || op == 0x4F)) {
                    char *args_tmp[64];
                    for (int i = (int)b - 1; i >= 0; i--) args_tmp[i] = stack[--sp];
                    char *recv = stack[--sp];
                    StrBuf call_sb; sb_init(&call_sb);
                    if (op == 0x4A) {
                        sb_append(&call_sb, "new "); sb_append(&call_sb, mn);
                    } else if (strcmp(recv, mn) == 0 || strcmp(recv, "this") == 0) {
                        sb_append(&call_sb, mn);
                    } else {
                        sb_append(&call_sb, recv); sb_append_char(&call_sb, '.'); sb_append(&call_sb, mn);
                    }
                    sb_append_char(&call_sb, '(');
                    for (uint32_t i = 0; i < b; i++) {
                        if (i > 0) sb_append(&call_sb, ", ");
                        sb_append(&call_sb, args_tmp[i]);
                        free(args_tmp[i]);
                    }
                    sb_append_char(&call_sb, ')');
                    free(recv);
                    if (op == 0x4F) {
                        sb_append(&stmts, "    "); sb_append(&stmts, call_sb.buf); sb_append(&stmts, ";\n");
                        sb_free(&call_sb);
                    } else {
                        stack[sp++] = call_sb.buf;
                    }
                } else {
                    can_struct = 0;
                }
            } else if (op == 0x32) {
                if (!abc_read_u30(code, &pos, end, &a) || !abc_read_u30(code, &pos, end, &b)) { *out_partial = 1; break; }
                snprintf(op_buf, sizeof(op_buf), "r%u r%u", a, b);
                sb_append(out_pcode, op_buf);
                can_struct = 0;
            } else if (op >= 0x0C && op <= 0x1A) {
                if (pos + 3 > end) { *out_partial = 1; break; }
                int32_t rel = (int32_t)code[pos] | ((int32_t)code[pos+1] << 8) | ((int32_t)((int8_t)code[pos+2]) << 16);
                pos += 3;
                snprintf(op_buf, sizeof(op_buf), "%+d -> 0x%04zx", rel, (size_t)((ptrdiff_t)pos + rel));
                sb_append(out_pcode, op_buf);
                can_struct = 0;
            } else if (op == 0x1B) {
                if (pos + 3 > end) { *out_partial = 1; break; }
                int32_t def_rel = (int32_t)code[pos] | ((int32_t)code[pos+1] << 8) | ((int32_t)((int8_t)code[pos+2]) << 16);
                pos += 3;
                uint32_t max_idx = 0;
                if (!abc_read_u30(code, &pos, end, &max_idx) || pos + (size_t)(max_idx + 1) * 3 > end) { *out_partial = 1; break; }
                pos += (size_t)(max_idx + 1) * 3;
                snprintf(op_buf, sizeof(op_buf), "default=%+d cases=%u", def_rel, max_idx + 1);
                sb_append(out_pcode, op_buf);
                can_struct = 0;
            } else if (op == 0xEF) {
                if (pos >= end) { *out_partial = 1; break; }
                uint8_t dt = code[pos++];
                if (!abc_read_u30(code, &pos, end, &a) || pos >= end) { *out_partial = 1; break; }
                uint8_t reg = code[pos++];
                if (!abc_read_u30(code, &pos, end, &b)) { *out_partial = 1; break; }
                snprintf(op_buf, sizeof(op_buf), "type=%u r%u extra=%u", dt, reg, b);
                sb_append(out_pcode, op_buf);
            } else {
                if (strcmp(opname, "unknown_op") == 0) {
                    *out_partial = 1;
                    can_struct = 0;
                    break;
                }
                snprintf(op_buf, sizeof(op_buf), "(0x%02x)", op);
                sb_append(out_pcode, op_buf);
                if (can_struct) {
                    if (op == 0x02 || op == 0x09 || op == 0x30 || op == 0x1D || (op >= 0x70 && op <= 0x77) || op == 0x82 || op == 0x85) {
                        /* nop / scope / coerce */
                    } else if (op == 0xD0 && sp < 250) stack[sp++] = strdup("this");
                    else if (op == 0xD1 && sp < 250) stack[sp++] = strdup("loc1");
                    else if (op == 0xD2 && sp < 250) stack[sp++] = strdup("loc2");
                    else if (op == 0xD3 && sp < 250) stack[sp++] = strdup("loc3");
                    else if (op >= 0xD4 && op <= 0xD7 && sp >= 1) {
                        char *val = stack[--sp];
                        char lbuf[32]; snprintf(lbuf, sizeof(lbuf), "    var loc%u = ", op - 0xD4);
                        sb_append(&stmts, lbuf); sb_append(&stmts, val); sb_append(&stmts, ";\n");
                        free(val);
                    } else if (op == 0x20 && sp < 250) stack[sp++] = strdup("null");
                    else if (op == 0x21 && sp < 250) stack[sp++] = strdup("undefined");
                    else if (op == 0x26 && sp < 250) stack[sp++] = strdup("true");
                    else if (op == 0x27 && sp < 250) stack[sp++] = strdup("false");
                    else if (op == 0x28 && sp < 250) stack[sp++] = strdup("NaN");
                    else if (op == 0x29 && sp >= 1) {
                        char *val = stack[--sp];
                        sb_append(&stmts, "    "); sb_append(&stmts, val); sb_append(&stmts, ";\n");
                        free(val);
                    } else if (op == 0x47) {
                        sb_append(&stmts, "    return;\n");
                    } else if (op == 0x48 && sp >= 1) {
                        char *val = stack[--sp];
                        sb_append(&stmts, "    return "); sb_append(&stmts, val); sb_append(&stmts, ";\n");
                        free(val);
                    } else if ((op == 0xA0 || op == 0xC5 || op == 0xA1 || op == 0xC6 || op == 0xA2 || op == 0xC7 || op == 0xA3 || op == 0xA4 || (op >= 0xAB && op <= 0xB0)) && sp >= 2) {
                        char *rhs = stack[--sp];
                        char *lhs = stack[--sp];
                        const char *bop = "+";
                        if (op == 0xA1 || op == 0xC6) bop = "-";
                        else if (op == 0xA2 || op == 0xC7) bop = "*";
                        else if (op == 0xA3) bop = "/";
                        else if (op == 0xA4) bop = "%";
                        else if (op == 0xAB) bop = "==";
                        else if (op == 0xAC) bop = "===";
                        else if (op == 0xAD) bop = "<";
                        else if (op == 0xAE) bop = "<=";
                        else if (op == 0xAF) bop = ">";
                        else if (op == 0xB0) bop = ">=";
                        size_t el = strlen(lhs) + strlen(rhs) + 8;
                        char *expr = (char *)malloc(el);
                        snprintf(expr, el, "(%s %s %s)", lhs, bop, rhs);
                        stack[sp++] = expr;
                        free(lhs); free(rhs);
                    } else {
                        can_struct = 0;
                    }
                }
            }
        }

        for (int i = 0; i < sp; i++) free(stack[i]);

        if (can_struct && !(*out_partial)) {
            *out_structured = 1;
            sb_append(out_source, "// AsmSWF (x86_64 GAS) structured AVM2 reconstruction\nfunction () {\n");
            if (stmts.len > 0) sb_append(out_source, stmts.buf);
            sb_append(out_source, "}\n");
        }
        sb_free(&stmts);
    }
}

/* ============================================================================
 * Minimal JSON field extractor for Worker Requests
 * ============================================================================ */
static char *json_extract_string(const char *json, const char *key) {
    char pat[64];
    snprintf(pat, sizeof(pat), "\"%s\"", key);
    const char *p = strstr(json, pat);
    if (!p) return NULL;
    p += strlen(pat);
    while (*p && (*p == ' ' || *p == '\t' || *p == ':')) p++;
    if (*p != '"') return NULL;
    p++;
    StrBuf sb;
    sb_init(&sb);
    while (*p && *p != '"') {
        if (*p == '\\' && p[1]) {
            p++;
            switch (*p) {
                case '"': sb_append_char(&sb, '"'); break;
                case '\\': sb_append_char(&sb, '\\'); break;
                case '/': sb_append_char(&sb, '/'); break;
                case 'n': sb_append_char(&sb, '\n'); break;
                case 'r': sb_append_char(&sb, '\r'); break;
                case 't': sb_append_char(&sb, '\t'); break;
                default: sb_append_char(&sb, *p); break;
            }
        } else {
            sb_append_char(&sb, *p);
        }
        p++;
    }
    return sb.buf;
}

static int json_extract_int(const char *json, const char *key, int def_val) {
    char pat[64];
    snprintf(pat, sizeof(pat), "\"%s\"", key);
    const char *p = strstr(json, pat);
    if (!p) return def_val;
    p += strlen(pat);
    while (*p && (*p == ' ' || *p == '\t' || *p == ':')) p++;
    if ((*p >= '0' && *p <= '9') || *p == '-') {
        return atoi(p);
    }
    return def_val;
}

static void append_info_json(StrBuf *sb, const SwfSession *s) {
    sb_append(sb, "{\"path\":");
    sb_append_json_str(sb, s->path);
    char num[512];
    snprintf(num, sizeof(num),
        ",\"sha256\":\"%s\",\"normalized_sha256\":\"%s\",\"compression\":\"%s\",\"version\":%u,\"file_length\":%u,\"stage_width_twips\":%d,\"stage_height_twips\":%d,\"frame_rate\":%g,\"frame_count\":%u,\"tag_count\":%zu,\"script_count\":%zu,\"symbol_count\":%zu,\"abc_count\":%zu,\"diagnostic_count\":0}",
        s->file_sha256, s->normalized_sha256, s->compression, s->version, s->file_length,
        s->stage_width_twips, s->stage_height_twips, s->frame_rate, s->frame_count,
        s->tag_count, s->script_count, s->symbol_count, s->abc_count
    );
    sb_append(sb, num);
}

static void append_unit_json(StrBuf *sb, const ScriptUnit *u) {
    sb_append(sb, "{\"id\":");
    sb_append_json_str(sb, u->id);
    char tmp[256];
    snprintf(tmp, sizeof(tmp), ",\"tag_index\":%d,\"tag_path\":[", u->tag_index);
    sb_append(sb, tmp);
    for (int i = 0; i < u->tag_path_len; i++) {
        snprintf(tmp, sizeof(tmp), "%s%d", (i > 0 ? "," : ""), u->tag_path[i]);
        sb_append(sb, tmp);
    }
    snprintf(tmp, sizeof(tmp), "],\"tag_code\":%u,\"language\":", u->tag_code);
    sb_append(sb, tmp);
    sb_append_json_str(sb, u->language);
    sb_append(sb, ",\"kind\":");
    sb_append_json_str(sb, u->kind);
    sb_append(sb, ",\"display_name\":");
    sb_append_json_str(sb, u->display_name);
    snprintf(tmp, sizeof(tmp), ",\"byte_start\":%zu,\"byte_len\":%zu,\"sha256\":\"%s\"", u->byte_start, u->byte_len, u->sha256);
    sb_append(sb, tmp);
    if (u->is_avm1_func) {
        sb_append(sb, ",\"parameters\":[");
        for (size_t i = 0; i < u->param_count; i++) {
            if (i > 0) sb_append_char(sb, ',');
            sb_append_json_str(sb, u->params[i]);
        }
        sb_append(sb, "],\"avm1_registers\":[");
        for (size_t i = 0; i < u->reg_count; i++) {
            snprintf(tmp, sizeof(tmp), "%s%d", (i > 0 ? "," : ""), u->registers[i]);
            sb_append(sb, tmp);
        }
        sb_append_char(sb, ']');
        if (u->has_reg_count) {
            snprintf(tmp, sizeof(tmp), ",\"avm1_register_count\":%d", u->avm1_reg_count_val);
            sb_append(sb, tmp);
        }
        snprintf(tmp, sizeof(tmp), ",\"avm1_flags\":%d,\"avm1_constants_sha256\":\"%s\"", u->avm1_flags, u->avm1_constants_sha256);
        sb_append(sb, tmp);
    }
    sb_append(sb, ",\"diagnostics\":[]}");
}

static double now_ms(void) {
#ifdef _WIN32
    LARGE_INTEGER freq, cnt;
    QueryPerformanceFrequency(&freq);
    QueryPerformanceCounter(&cnt);
    return (double)cnt.QuadPart * 1000.0 / (double)freq.QuadPart;
#else
    return 0.0;
#endif
}

static void run_worker(void) {
#ifdef _WIN32
    _setmode(_fileno(stdin), _O_BINARY);
    _setmode(_fileno(stdout), _O_BINARY);
#endif
    SwfSession *session = NULL;
    size_t line_cap = 65536;
    char *line = (char *)malloc(line_cap);

    while (fgets(line, (int)line_cap, stdin)) {
        while (strlen(line) == line_cap - 1 && line[strlen(line) - 1] != '\n') {
            line_cap *= 2;
            line = (char *)realloc(line, line_cap);
            if (!fgets(line + strlen(line), (int)(line_cap - strlen(line)), stdin)) break;
        }
        double t0 = now_ms();
        char *req_id = json_extract_string(line, "request_id");
        char *op = json_extract_string(line, "operation");
        const char *args_ptr = strstr(line, "\"args\"");
        if (!args_ptr) args_ptr = line;

        StrBuf out;
        sb_init(&out);
        sb_append(&out, "{\"request_id\":");
        sb_append_json_str(&out, req_id);

        if (!op) {
            sb_append(&out, ",\"error\":\"Missing operation\",\"elapsed_ms\":0.0}\n");
        } else if (strcmp(op, "open") == 0) {
            char *path = json_extract_string(args_ptr, "path");
            char *err = NULL;
            SwfSession *opened = path ? open_swf_file(path, &err) : NULL;
            free(path);
            double elapsed = now_ms() - t0;
            if (!opened) {
                sb_append(&out, ",\"error\":");
                sb_append_json_str(&out, err ? err : "Missing path");
                free(err);
            } else {
                free_session(session);
                session = opened;
                sb_append(&out, ",\"result\":{\"info\":");
                append_info_json(&out, session);
                sb_append(&out, ",\"diagnostics\":[]}");
            }
            char el_buf[64]; snprintf(el_buf, sizeof(el_buf), ",\"elapsed_ms\":%.4f}\n", elapsed);
            sb_append(&out, el_buf);
        } else if (strcmp(op, "info") == 0) {
            double elapsed = now_ms() - t0;
            if (!session) {
                sb_append(&out, ",\"error\":\"No SWF session is open\"");
            } else {
                sb_append(&out, ",\"result\":");
                append_info_json(&out, session);
            }
            char el_buf[64]; snprintf(el_buf, sizeof(el_buf), ",\"elapsed_ms\":%.4f}\n", elapsed);
            sb_append(&out, el_buf);
        } else if (strcmp(op, "scripts") == 0) {
            int offset = json_extract_int(args_ptr, "offset", 0);
            int limit = json_extract_int(args_ptr, "limit", 200);
            if (limit < 1) limit = 1;
            if (limit > 500) limit = 500;
            double elapsed = now_ms() - t0;
            if (!session) {
                sb_append(&out, ",\"error\":\"No SWF session is open\"");
            } else {
                size_t total = session->script_count;
                size_t start = (offset > 0 && (size_t)offset < total) ? (size_t)offset : (offset <= 0 ? 0 : total);
                size_t end = (start + (size_t)limit < total) ? (start + (size_t)limit) : total;
                char hdr[128];
                snprintf(hdr, sizeof(hdr), ",\"result\":{\"offset\":%zu,\"limit\":%d,\"total\":%zu,\"items\":[", start, limit, total);
                sb_append(&out, hdr);
                for (size_t i = start; i < end; i++) {
                    if (i > start) sb_append_char(&out, ',');
                    append_unit_json(&out, &session->scripts[i]);
                }
                sb_append(&out, "]}");
            }
            char el_buf[64]; snprintf(el_buf, sizeof(el_buf), ",\"elapsed_ms\":%.4f}\n", elapsed);
            sb_append(&out, el_buf);
        } else if (strcmp(op, "disassemble") == 0 || strcmp(op, "decompile") == 0) {
            char *uid = json_extract_string(args_ptr, "id");
            int offset = json_extract_int(args_ptr, "offset", 0);
            int limit = json_extract_int(args_ptr, "limit", 0);
            int eff_limit = (limit <= 0 || limit > 65536) ? 65536 : limit;
            const ScriptUnit *found = NULL;
            if (session && uid) {
                for (size_t i = 0; i < session->script_count; i++) {
                    if (strcmp(session->scripts[i].id, uid) == 0) { found = &session->scripts[i]; break; }
                }
            }
            if (!found) {
                double elapsed = now_ms() - t0;
                sb_append(&out, ",\"error\":\"Unknown script unit\"");
                char el_buf[64]; snprintf(el_buf, sizeof(el_buf), ",\"elapsed_ms\":%.4f}\n", elapsed);
                sb_append(&out, el_buf);
            } else {
                StrBuf pcode, source;
                int partial = 0, structured = 0;
                size_t instr_count = 0;
                disassemble_unit(found, &pcode, &partial, &instr_count, &source, &structured);
                size_t bstart = 0, blen = 0, total_cp = 0;
                utf8_slice_by_chars(pcode.buf, pcode.len, (size_t)(offset > 0 ? offset : 0), (size_t)eff_limit, &bstart, &blen, &total_cp);
                char *chunk = (char *)malloc(blen + 1);
                memcpy(chunk, pcode.buf + bstart, blen);
                chunk[blen] = '\0';
                double elapsed = now_ms() - t0;

                if (strcmp(op, "disassemble") == 0) {
                    sb_append(&out, ",\"result\":{\"unit_id\":");
                    sb_append_json_str(&out, found->id);
                    sb_append(&out, ",\"language\":");
                    sb_append_json_str(&out, found->language);
                    sb_append(&out, ",\"state\":");
                    sb_append_json_str(&out, partial ? "partial" : "decoded");
                    char nums[160];
                    snprintf(nums, sizeof(nums), ",\"offset\":%d,\"limit\":%d,\"pcode_total\":%zu,\"pcode\":", offset > 0 ? offset : 0, eff_limit, total_cp);
                    sb_append(&out, nums);
                    sb_append_json_str(&out, chunk);
                    snprintf(nums, sizeof(nums), ",\"instruction_total\":%zu,\"diagnostics\":[]}", instr_count);
                    sb_append(&out, nums);
                } else {
                    sb_append(&out, ",\"result\":{\"unit_id\":");
                    sb_append_json_str(&out, found->id);
                    sb_append(&out, ",\"language\":");
                    sb_append_json_str(&out, found->language);
                    sb_append(&out, ",\"state\":");
                    sb_append_json_str(&out, structured ? "structured" : "pcode_only");
                    sb_append(&out, ",\"source\":");
                    if (structured) sb_append_json_str(&out, source.buf);
                    else sb_append(&out, "null");
                    sb_append(&out, ",\"pcode\":");
                    sb_append_json_str(&out, chunk);
                    char nums[128];
                    snprintf(nums, sizeof(nums), ",\"pcode_total\":%zu,\"diagnostics\":[]}", total_cp);
                    sb_append(&out, nums);
                }
                char el_buf[64]; snprintf(el_buf, sizeof(el_buf), ",\"elapsed_ms\":%.4f}\n", elapsed);
                sb_append(&out, el_buf);
                free(chunk);
                sb_free(&pcode);
                sb_free(&source);
            }
            free(uid);
        } else {
            sb_append(&out, ",\"error\":\"Unknown worker operation\",\"elapsed_ms\":0.0}\n");
        }

        fwrite(out.buf, 1, out.len, stdout);
        fflush(stdout);
        sb_free(&out);
        free(req_id);
        free(op);
    }
    free(line);
    free_session(session);
}

int main(int argc, char **argv) {
    if (argc <= 1 || strcmp(argv[1], "worker") == 0) {
        run_worker();
        return 0;
    }
    if (strcmp(argv[1], "info") == 0 && argc >= 3) {
        char *err = NULL;
        SwfSession *s = open_swf_file(argv[2], &err);
        if (!s) { fprintf(stderr, "%s\n", err ? err : "Error"); free(err); return 1; }
        StrBuf sb; sb_init(&sb);
        append_info_json(&sb, s);
        printf("%s\n", sb.buf);
        sb_free(&sb);
        free_session(s);
        return 0;
    }
    fprintf(stderr, "Usage: asmswf [worker | info <swf>]\n");
    return 2;
}
