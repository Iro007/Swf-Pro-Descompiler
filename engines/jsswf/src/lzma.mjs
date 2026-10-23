/**
 * Pure JavaScript LZMA1 decoder for SWF ZWS containers (SWF version >= 13).
 * Decodes raw LZMA streams given the 5-byte properties header and expected output size.
 */

export function decompressLzma(props, compressed, expectedSize) {
  if (props.length < 5) {
    throw new Error("Truncated ZWS LZMA properties");
  }
  const prop0 = props[0];
  if (prop0 >= 225) {
    throw new Error("Invalid LZMA properties byte");
  }
  const lc = prop0 % 9;
  const rem = Math.floor(prop0 / 9);
  const lp = rem % 5;
  const pb = Math.floor(rem / 5);

  const dictSize = props.readUInt32LE(1);
  if (dictSize > 64 * 1024 * 1024) {
    throw new Error("LZMA dictionary exceeds 64 MiB");
  }

  const out = Buffer.alloc(expectedSize);
  let outPos = 0;

  let inPos = 0;
  const readByte = () => (inPos < compressed.length ? compressed[inPos++] : 0);
  if (compressed.length < 5) {
    throw new Error("Truncated LZMA stream");
  }
  readByte();
  let code = 0;
  for (let i = 0; i < 4; i++) {
    code = ((code << 8) | readByte()) >>> 0;
  }
  let range = 0xffffffff;

  const decodeBit = (probs, idx) => {
    const prob = probs[idx];
    const bound = ((range >>> 11) * prob) >>> 0;
    if (code < bound) {
      range = bound;
      probs[idx] = (prob + ((2048 - prob) >>> 5)) & 0xffff;
      if (range < 0x01000000) {
        code = ((code << 8) | readByte()) >>> 0;
        range = (range << 8) >>> 0;
      }
      return 0;
    } else {
      range = (range - bound) >>> 0;
      code = (code - bound) >>> 0;
      probs[idx] = (prob - (prob >>> 5)) & 0xffff;
      if (range < 0x01000000) {
        code = ((code << 8) | readByte()) >>> 0;
        range = (range << 8) >>> 0;
      }
      return 1;
    }
  };

  const decodeDirectBits = (numBits) => {
    let res = 0;
    for (let i = numBits; i > 0; i--) {
      range >>>= 1;
      code = (code - range) >>> 0;
      const t = 0 - (code >>> 31);
      code = (code + (range & t)) >>> 0;
      if (range < 0x01000000) {
        code = ((code << 8) | readByte()) >>> 0;
        range = (range << 8) >>> 0;
      }
      res = ((res << 1) + (t + 1)) >>> 0;
    }
    return res;
  };

  const decodeTree = (probs, offset, numBits) => {
    let m = 1;
    for (let i = 0; i < numBits; i++) {
      m = (m << 1) | decodeBit(probs, offset + m);
    }
    return m - (1 << numBits);
  };

  const decodeReverseTree = (probs, offset, numBits) => {
    let m = 1;
    let symbol = 0;
    for (let i = 0; i < numBits; i++) {
      const bit = decodeBit(probs, offset + m);
      m = (m << 1) | bit;
      symbol |= bit << i;
    }
    return symbol;
  };

  const initProbs = (n) => {
    const arr = new Uint16Array(n);
    arr.fill(1024);
    return arr;
  };

  const posStateMask = (1 << pb) - 1;
  const litPosMask = (1 << lp) - 1;
  const numLitStates = 1 << (lc + lp);
  const litProbs = initProbs(numLitStates * 0x300);

  const isMatch = initProbs(12 << 4);
  const isRep = initProbs(12);
  const isRepG0 = initProbs(12);
  const isRepG1 = initProbs(12);
  const isRepG2 = initProbs(12);
  const isRep0Long = initProbs(12 << 4);

  const posSlot = initProbs(4 * 64);
  const posDecoders = initProbs(115);
  const alignProbs = initProbs(16);

  const makeLenDecoder = () => ({
    choice: initProbs(2),
    low: initProbs(16 * 8),
    mid: initProbs(16 * 8),
    high: initProbs(256),
  });
  const lenDecoder = makeLenDecoder();
  const repLenDecoder = makeLenDecoder();

  const decodeLen = (dec, posState) => {
    if (decodeBit(dec.choice, 0) === 0) {
      return decodeTree(dec.low, posState << 3, 3);
    }
    if (decodeBit(dec.choice, 1) === 0) {
      return 8 + decodeTree(dec.mid, posState << 3, 3);
    }
    return 16 + decodeTree(dec.high, 0, 8);
  };

  let state = 0;
  let rep0 = 0, rep1 = 0, rep2 = 0, rep3 = 0;

  while (outPos < expectedSize) {
    const posState = outPos & posStateMask;
    if (decodeBit(isMatch, (state << 4) + posState) === 0) {
      const prevByte = outPos > 0 ? out[outPos - 1] : 0;
      const litState = ((outPos & litPosMask) << lc) + (prevByte >>> (8 - lc));
      const baseOffset = litState * 0x300;
      let symbol = 1;
      if (state >= 7) {
        let matchByte = outPos > rep0 ? out[outPos - rep0 - 1] : 0;
        do {
          const matchBit = (matchByte >>> 7) & 1;
          matchByte <<= 1;
          const bit = decodeBit(litProbs, baseOffset + ((1 + matchBit) << 8) + symbol);
          symbol = (symbol << 1) | bit;
          if (matchBit !== bit) {
            while (symbol < 0x100) {
              symbol = (symbol << 1) | decodeBit(litProbs, baseOffset + symbol);
            }
            break;
          }
        } while (symbol < 0x100);
      } else {
        while (symbol < 0x100) {
          symbol = (symbol << 1) | decodeBit(litProbs, baseOffset + symbol);
        }
      }
      out[outPos++] = symbol & 0xff;
      state = state < 4 ? 0 : state < 10 ? state - 3 : state - 6;
    } else {
      let len;
      if (decodeBit(isRep, state) === 1) {
        if (decodeBit(isRepG0, state) === 0) {
          if (decodeBit(isRep0Long, (state << 4) + posState) === 0) {
            state = state < 7 ? 9 : 11;
            if (rep0 >= outPos) {
              throw new Error("LZMA rep0 out of bounds");
            }
            out[outPos] = out[outPos - rep0 - 1];
            outPos++;
            continue;
          }
        } else {
          let dist;
          if (decodeBit(isRepG1, state) === 0) {
            dist = rep1;
          } else {
            if (decodeBit(isRepG2, state) === 0) {
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
        len = decodeLen(repLenDecoder, posState) + 2;
        state = state < 7 ? 8 : 11;
      } else {
        rep3 = rep2;
        rep2 = rep1;
        rep1 = rep0;
        len = decodeLen(lenDecoder, posState) + 2;
        state = state < 7 ? 7 : 10;
        const slot = decodeTree(posSlot, (len < 6 ? len - 2 : 3) << 6, 6);
        if (slot >= 4) {
          const numDirectBits = (slot >>> 1) - 1;
          rep0 = (2 | (slot & 1)) << numDirectBits;
          if (slot < 14) {
            rep0 += decodeReverseTree(posDecoders, rep0 - slot, numDirectBits);
          } else {
            rep0 += decodeDirectBits(numDirectBits - 4) << 4;
            rep0 += decodeReverseTree(alignProbs, 0, 4);
          }
        } else {
          rep0 = slot;
        }
      }
      if (rep0 >= outPos) {
        throw new Error("LZMA distance out of bounds");
      }
      for (let i = 0; i < len && outPos < expectedSize; i++) {
        out[outPos] = out[outPos - rep0 - 1];
        outPos++;
      }
    }
  }
  return out;
}
