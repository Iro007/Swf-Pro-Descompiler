    .intel_syntax noprefix
    .text

/* ============================================================================
 * uint64_t asm_swf_read_tag_header(
 *     const uint8_t *buf,   // rcx
 *     size_t pos,           // rdx
 *     size_t end,           // r8
 *     uint16_t *out_code,   // r9
 *     uint32_t *out_len,    // [rsp + 40]
 *     uint32_t *out_long    // [rsp + 48]
 * )
 * Returns new pos after header, or 0 on truncation.
 * ============================================================================ */
    .globl asm_swf_read_tag_header
asm_swf_read_tag_header:
    lea     rax, [rdx + 2]
    cmp     rax, r8
    ja      .Ltag_fail

    movzx   r10d, word ptr [rcx + rdx]
    mov     r11d, r10d
    shr     r11d, 6
    mov     word ptr [r9], r11w

    and     r10d, 0x3F
    mov     r9, qword ptr [rsp + 40]
    mov     r11, qword ptr [rsp + 48]

    cmp     r10d, 0x3F
    je      .Ltag_long

    mov     dword ptr [r9], r10d
    mov     dword ptr [r11], 0
    ret

.Ltag_long:
    lea     rax, [rdx + 6]
    cmp     rax, r8
    ja      .Ltag_fail

    mov     r10d, dword ptr [rcx + rdx + 2]
    mov     dword ptr [r9], r10d
    mov     dword ptr [r11], 1
    ret

.Ltag_fail:
    xor      eax, eax
    ret

/* ============================================================================
 * size_t asm_avm2_read_u30(
 *     const uint8_t *buf,   // rcx
 *     size_t pos,           // rdx
 *     size_t end,           // r8
 *     uint32_t *out_val     // r9
 * )
 * Fast 1..5 byte variable-length U30/S32 reader in x86_64 assembly.
 * Returns new pos (>0) on success, or 0 on EOF.
 * ============================================================================ */
    .globl asm_avm2_read_u30
asm_avm2_read_u30:
    xor     r10d, r10d          /* accumulated result */

    /* byte 0 */
    cmp     rdx, r8
    jae     .Lu30_fail
    movzx   eax, byte ptr [rcx + rdx]
    inc     rdx
    mov     r10d, eax
    and     r10d, 0x7F
    test    al, al
    jns     .Lu30_done

    /* byte 1 */
    cmp     rdx, r8
    jae     .Lu30_fail
    movzx   eax, byte ptr [rcx + rdx]
    inc     rdx
    mov     r11d, eax
    and     r11d, 0x7F
    shl     r11d, 7
    or      r10d, r11d
    test    al, al
    jns     .Lu30_done

    /* byte 2 */
    cmp     rdx, r8
    jae     .Lu30_fail
    movzx   eax, byte ptr [rcx + rdx]
    inc     rdx
    mov     r11d, eax
    and     r11d, 0x7F
    shl     r11d, 14
    or      r10d, r11d
    test    al, al
    jns     .Lu30_done

    /* byte 3 */
    cmp     rdx, r8
    jae     .Lu30_fail
    movzx   eax, byte ptr [rcx + rdx]
    inc     rdx
    mov     r11d, eax
    and     r11d, 0x7F
    shl     r11d, 21
    or      r10d, r11d
    test    al, al
    jns     .Lu30_done

    /* byte 4 */
    cmp     rdx, r8
    jae     .Lu30_fail
    movzx   eax, byte ptr [rcx + rdx]
    inc     rdx
    shl     eax, 28
    or      r10d, eax

.Lu30_done:
    mov     dword ptr [r9], r10d
    mov     rax, rdx
    ret

.Lu30_fail:
    xor     eax, eax
    ret

/* ============================================================================
 * size_t asm_avm1_scan_action(
 *     const uint8_t *buf,   // rcx
 *     size_t pos,           // rdx
 *     size_t end,           // r8
 *     uint8_t *out_code,    // r9
 *     uint16_t *out_len     // [rsp + 40]
 * )
 * Returns payload start offset (or 0 on truncation).
 * ============================================================================ */
    .globl asm_avm1_scan_action
asm_avm1_scan_action:
    cmp     rdx, r8
    jae     .Lavm1_fail
    movzx   eax, byte ptr [rcx + rdx]
    inc     rdx
    mov     byte ptr [r9], al
    mov     r10, qword ptr [rsp + 40]
    cmp     al, 0x80
    jb      .Lavm1_no_payload

    lea     r11, [rdx + 2]
    cmp     r11, r8
    ja      .Lavm1_fail
    movzx   eax, word ptr [rcx + rdx]
    mov     word ptr [r10], ax
    lea     rax, [rdx + 2]
    ret

.Lavm1_no_payload:
    mov     word ptr [r10], 0
    mov     rax, rdx
    ret

.Lavm1_fail:
    xor     eax, eax
    ret

/* ============================================================================
 * int asm_bit_read_u32(
 *     const uint8_t *buf,   // rcx
 *     size_t byte_len,      // rdx
 *     size_t *bit_pos,      // r8
 *     uint32_t count,       // r9d
 *     uint32_t *out_val     // [rsp + 40]
 * )
 * Returns 1 on success, 0 on out-of-bounds.
 * ============================================================================ */
    .globl asm_bit_read_u32
asm_bit_read_u32:
    push    rbx
    push    rsi
    mov     rsi, qword ptr [rsp + 56] /* out_val (40 + 16 for 2 pushes) */
    xor     eax, eax
    test    r9d, r9d
    jz      .Lbit_zero

    mov     r10, qword ptr [r8]       /* current bit_pos */
    xor     r11d, r11d                /* accumulated value */

.Lbit_loop:
    mov     rax, r10
    shr     rax, 3                    /* byte_idx = bit_pos >> 3 */
    cmp     rax, rdx
    jae     .Lbit_fail

    movzx   ebx, byte ptr [rcx + rax]
    mov     eax, r10d
    and     eax, 7
    neg     eax
    add     eax, 7                    /* shift = 7 - (bit_pos & 7) */
    push    rcx
    mov     ecx, eax
    shr     ebx, cl
    pop     rcx
    and     ebx, 1
    shl     r11d, 1
    or      r11d, ebx
    inc     r10
    dec     r9d
    jnz     .Lbit_loop

    mov     qword ptr [r8], r10
    mov     dword ptr [rsi], r11d
    mov     eax, 1
    pop     rsi
    pop     rbx
    ret

.Lbit_zero:
    mov     dword ptr [rsi], 0
    mov     eax, 1
    pop     rsi
    pop     rbx
    ret

.Lbit_fail:
    xor     eax, eax
    pop     rsi
    pop     rbx
    ret

/* ============================================================================
 * void asm_sha256_transform(uint32_t state[8], const uint8_t block[64])
 * rcx = state, rdx = block
 * Full 64-round SHA-256 block compression in x86_64 assembly.
 * ============================================================================ */
    .section .rdata,"dr"
    .align 16
.Lsha256_k:
    .long 0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5
    .long 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5
    .long 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3
    .long 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174
    .long 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc
    .long 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da
    .long 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7
    .long 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967
    .long 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13
    .long 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85
    .long 0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3
    .long 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070
    .long 0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5
    .long 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3
    .long 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208
    .long 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2

    .text
    .globl asm_sha256_transform
asm_sha256_transform:
    push    rbp
    push    rbx
    push    rsi
    push    rdi
    push    r12
    push    r13
    push    r14
    push    r15
    sub     rsp, 264                  /* 64 * 4 bytes for W[64] + alignment */

    mov     rsi, rcx                  /* rsi = state */
    mov     rdi, rdx                  /* rdi = block */

    /* Load and byte-swap first 16 words into W[0..15] */
    xor     ecx, ecx
.Lw_init:
    mov     eax, dword ptr [rdi + rcx*4]
    bswap   eax
    mov     dword ptr [rsp + rcx*4], eax
    inc     ecx
    cmp     ecx, 16
    jb      .Lw_init

    /* Expand W[16..63] */
.Lw_expand:
    /* s0 = ROR(W[i-15], 7) ^ ROR(W[i-15], 18) ^ (W[i-15] >> 3) */
    mov     eax, dword ptr [rsp + rcx*4 - 60]
    mov     edx, eax
    mov     r8d, eax
    ror     eax, 7
    ror     edx, 18
    shr     r8d, 3
    xor     eax, edx
    xor     eax, r8d

    /* s1 = ROR(W[i-2], 17) ^ ROR(W[i-2], 19) ^ (W[i-2] >> 10) */
    mov     ebx, dword ptr [rsp + rcx*4 - 8]
    mov     edx, ebx
    mov     r8d, ebx
    ror     ebx, 17
    ror     edx, 19
    shr     r8d, 10
    xor     ebx, edx
    xor     ebx, r8d

    add     eax, ebx
    add     eax, dword ptr [rsp + rcx*4 - 64]
    add     eax, dword ptr [rsp + rcx*4 - 28]
    mov     dword ptr [rsp + rcx*4], eax

    inc     ecx
    cmp     ecx, 64
    jb      .Lw_expand

    /* Load working variables a..h into r8d..r15d */
    mov     r8d,  dword ptr [rsi + 0]
    mov     r9d,  dword ptr [rsi + 4]
    mov     r10d, dword ptr [rsi + 8]
    mov     r11d, dword ptr [rsi + 12]
    mov     r12d, dword ptr [rsi + 16]
    mov     r13d, dword ptr [rsi + 20]
    mov     r14d, dword ptr [rsi + 24]
    mov     r15d, dword ptr [rsi + 28]

    lea     rbp, [rip + .Lsha256_k]
    xor     ecx, ecx

.Lround_loop:
    /* S1 = ROR(e, 6) ^ ROR(e, 11) ^ ROR(e, 25) */
    mov     eax, r12d
    mov     edx, r12d
    mov     ebx, r12d
    ror     eax, 6
    ror     edx, 11
    ror     ebx, 25
    xor     eax, edx
    xor     eax, ebx

    /* ch = (e & f) ^ (~e & g) */
    mov     edx, r12d
    and     edx, r13d
    mov     ebx, r12d
    not     ebx
    and     ebx, r14d
    xor     edx, ebx

    /* temp1 = h + S1 + ch + K[i] + W[i] */
    add     eax, r15d
    add     eax, edx
    add     eax, dword ptr [rbp + rcx*4]
    add     eax, dword ptr [rsp + rcx*4]
    mov     edi, eax                  /* edi = temp1 */

    /* S0 = ROR(a, 2) ^ ROR(a, 13) ^ ROR(a, 22) */
    mov     eax, r8d
    mov     edx, r8d
    mov     ebx, r8d
    ror     eax, 2
    ror     edx, 13
    ror     ebx, 22
    xor     eax, edx
    xor     eax, ebx

    /* maj = (a & b) ^ (a & c) ^ (b & c) */
    mov     edx, r8d
    and     edx, r9d
    mov     ebx, r8d
    and     ebx, r10d
    xor     edx, ebx
    mov     ebx, r9d
    and     ebx, r10d
    xor     edx, ebx

    add     eax, edx                  /* eax = temp2 = S0 + maj */

    /* Shift working registers: h=g, g=f, f=e, e=d+temp1, d=c, c=b, b=a, a=temp1+temp2 */
    mov     r15d, r14d
    mov     r14d, r13d
    mov     r13d, r12d
    mov     r12d, r11d
    add     r12d, edi
    mov     r11d, r10d
    mov     r10d, r9d
    mov     r9d,  r8d
    lea     r8d,  [rdi + rax]

    inc     ecx
    cmp     ecx, 64
    jb      .Lround_loop

    /* Add compressed chunk back to state */
    add     dword ptr [rsi + 0],  r8d
    add     dword ptr [rsi + 4],  r9d
    add     dword ptr [rsi + 8],  r10d
    add     dword ptr [rsi + 12], r11d
    add     dword ptr [rsi + 16], r12d
    add     dword ptr [rsi + 20], r13d
    add     dword ptr [rsi + 24], r14d
    add     dword ptr [rsi + 28], r15d

    add     rsp, 264
    pop     r15
    pop     r14
    pop     r13
    pop     r12
    pop     rdi
    pop     rsi
    pop     rbx
    pop     rbp
    ret
