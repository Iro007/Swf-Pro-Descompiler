package main

import (
	"encoding/binary"
	"fmt"
)

type rangeDecoder struct {
	buf       []byte
	pos       int
	rangeVal  uint32
	code      uint32
	corrupted bool
}

func newRangeDecoder(buf []byte, offset int) *rangeDecoder {
	rd := &rangeDecoder{
		buf:      buf,
		pos:      offset,
		rangeVal: 0xFFFFFFFF,
	}
	if offset+5 > len(buf) {
		rd.corrupted = true
		return rd
	}
	if buf[offset] != 0 {
		rd.corrupted = true
	}
	rd.pos = offset + 1
	for i := 0; i < 4; i++ {
		rd.code = (rd.code << 8) | uint32(rd.readByte())
	}
	if rd.code == rd.rangeVal {
		rd.corrupted = true
	}
	return rd
}

func (rd *rangeDecoder) readByte() byte {
	if rd.pos < len(rd.buf) {
		b := rd.buf[rd.pos]
		rd.pos++
		return b
	}
	return 0
}

func (rd *rangeDecoder) normalize() {
	if rd.rangeVal < 0x01000000 {
		rd.rangeVal <<= 8
		rd.code = (rd.code << 8) | uint32(rd.readByte())
	}
}

func (rd *rangeDecoder) decodeBit(probs []uint16, idx int) uint32 {
	rd.normalize()
	prob := uint32(probs[idx])
	bound := (rd.rangeVal >> 11) * prob
	if rd.code < bound {
		rd.rangeVal = bound
		probs[idx] = uint16(prob + ((2048 - prob) >> 5))
		return 0
	}
	rd.rangeVal -= bound
	rd.code -= bound
	probs[idx] = uint16(prob - (prob >> 5))
	return 1
}

func (rd *rangeDecoder) decodeBitTree(probs []uint16, numBits int) uint32 {
	m := uint32(1)
	for i := 0; i < numBits; i++ {
		m = (m << 1) | rd.decodeBit(probs, int(m))
	}
	return m - (uint32(1) << numBits)
}

func (rd *rangeDecoder) decodeReverseBitTree(probs []uint16, offset int, numBits int) uint32 {
	m := uint32(1)
	symbol := uint32(0)
	for i := 0; i < numBits; i++ {
		bit := rd.decodeBit(probs, offset+int(m))
		m = (m << 1) | bit
		symbol |= bit << i
	}
	return symbol
}

func (rd *rangeDecoder) decodeDirectBits(numBits int) uint32 {
	res := uint32(0)
	for i := 0; i < numBits; i++ {
		rd.normalize()
		rd.rangeVal >>= 1
		rd.code -= rd.rangeVal
		t := 0 - (rd.code >> 31)
		rd.code += rd.rangeVal & t
		res = (res << 1) | (1 - (t & 1))
	}
	return res
}

func initProbs(n int) []uint16 {
	a := make([]uint16, n)
	for i := range a {
		a[i] = 1024
	}
	return a
}

type lenDecoder struct {
	choice   []uint16
	low      [][]uint16
	mid      [][]uint16
	high     []uint16
	posStates int
}

func newLenDecoder(posStates int) *lenDecoder {
	ld := &lenDecoder{
		choice:    initProbs(2),
		low:       make([][]uint16, posStates),
		mid:       make([][]uint16, posStates),
		high:      initProbs(256),
		posStates: posStates,
	}
	for i := 0; i < posStates; i++ {
		ld.low[i] = initProbs(8)
		ld.mid[i] = initProbs(8)
	}
	return ld
}

func (ld *lenDecoder) decode(rd *rangeDecoder, posState int) uint32 {
	if rd.decodeBit(ld.choice, 0) == 0 {
		return rd.decodeBitTree(ld.low[posState], 3)
	}
	if rd.decodeBit(ld.choice, 1) == 0 {
		return 8 + rd.decodeBitTree(ld.mid[posState], 3)
	}
	return 16 + rd.decodeBitTree(ld.high, 8)
}

func decompressLzmaSwf(zwsData []byte, expectedUncompressedLen int) ([]byte, error) {
	if len(zwsData) < 17 {
		return nil, fmt.Errorf("Truncated ZWS header")
	}
	props := zwsData[12]
	if props >= 9*5*5 {
		return nil, fmt.Errorf("Invalid LZMA properties byte")
	}
	lc := int(props % 9)
	rem := int(props / 9)
	lp := rem % 5
	pb := rem / 5
	dictSize := binary.LittleEndian.Uint32(zwsData[13:17])
	if dictSize < 4096 {
		dictSize = 4096
	}

	posStates := 1 << pb
	posStateMask := uint32(posStates - 1)
	litPosMask := uint32((1 << lp) - 1)
	numLitTables := 1 << (lc + lp)
	litProbs := make([][]uint16, numLitTables)
	for i := 0; i < numLitTables; i++ {
		litProbs[i] = initProbs(0x300)
	}

	isMatch := initProbs(12 * posStates)
	isRep := initProbs(12)
	isRepG0 := initProbs(12)
	isRepG1 := initProbs(12)
	isRepG2 := initProbs(12)
	isRep0Long := initProbs(12 * posStates)

	posSlot := make([][]uint16, 4)
	for i := 0; i < 4; i++ {
		posSlot[i] = initProbs(64)
	}
	posDecoders := initProbs(115)
	alignDecoder := initProbs(16)

	lenDec := newLenDecoder(posStates)
	repLenDec := newLenDecoder(posStates)

	rd := newRangeDecoder(zwsData, 17)
	out := make([]byte, expectedUncompressedLen)
	outPos := 0
	state := 0
	var rep0, rep1, rep2, rep3 uint32
	var prevByte byte

	for outPos < expectedUncompressedLen {
		posState := int(uint32(outPos) & posStateMask)
		if rd.decodeBit(isMatch, state*posStates+posState) == 0 {
			litState := ((uint32(outPos) & litPosMask) << lc) | (uint32(prevByte) >> (8 - lc))
			probs := litProbs[litState]
			symbol := uint32(1)
			if state >= 7 {
				matchByte := byte(0)
				if outPos >= int(rep0+1) {
					matchByte = out[outPos-int(rep0)-1]
				}
				for symbol < 0x100 {
					matchBit := uint32((matchByte >> 7) & 1)
					matchByte <<= 1
					bit := rd.decodeBit(probs, int(((1+matchBit)<<8)+symbol))
					symbol = (symbol << 1) | bit
					if matchBit != bit {
						break
					}
				}
			}
			for symbol < 0x100 {
				symbol = (symbol << 1) | rd.decodeBit(probs, int(symbol))
			}
			prevByte = byte(symbol & 0xFF)
			out[outPos] = prevByte
			outPos++
			if state < 4 {
				state = 0
			} else if state < 10 {
				state -= 3
			} else {
				state -= 6
			}
		} else {
			var length uint32
			if rd.decodeBit(isRep, state) == 1 {
				if rd.decodeBit(isRepG0, state) == 0 {
					if rd.decodeBit(isRep0Long, state*posStates+posState) == 0 {
						if state < 7 {
							state = 9
						} else {
							state = 11
						}
						if outPos < int(rep0+1) {
							return nil, fmt.Errorf("LZMA rep0 out of bounds")
						}
						prevByte = out[outPos-int(rep0)-1]
						out[outPos] = prevByte
						outPos++
						continue
					}
				} else {
					var dist uint32
					if rd.decodeBit(isRepG1, state) == 0 {
						dist = rep1
					} else {
						if rd.decodeBit(isRepG2, state) == 0 {
							dist = rep2
						} else {
							dist = rep3
							rep3 = rep2
						}
						rep2 = rep1
					}
					rep1 = rep0
					rep0 = dist
				}
				length = repLenDec.decode(rd, posState) + 2
				if state < 7 {
					state = 8
				} else {
					state = 11
				}
			} else {
				rep3 = rep2
				rep2 = rep1
				rep1 = rep0
				length = lenDec.decode(rd, posState) + 2
				lenToPosState := int(length - 2)
				if lenToPosState > 3 {
					lenToPosState = 3
				}
				slot := rd.decodeBitTree(posSlot[lenToPosState], 6)
				if slot >= 4 {
					numDirectBits := int((slot >> 1) - 1)
					dist := (2 | (slot & 1)) << numDirectBits
					if slot < 14 {
						dist += rd.decodeReverseBitTree(posDecoders, int(dist-slot), numDirectBits)
					} else {
						dist += rd.decodeDirectBits(numDirectBits-4) << 4
						dist += rd.decodeReverseBitTree(alignDecoder, 0, 4)
					}
					rep0 = dist
				} else {
					rep0 = slot
				}
				if rep0 == 0xFFFFFFFF {
					break
				}
				if state < 7 {
					state = 7
				} else {
					state = 10
				}
			}
			dist := int(rep0 + 1)
			if dist > outPos {
				return nil, fmt.Errorf("LZMA match distance exceeds output position")
			}
			for i := uint32(0); i < length && outPos < expectedUncompressedLen; i++ {
				prevByte = out[outPos-dist]
				out[outPos] = prevByte
				outPos++
			}
		}
	}
	return out, nil
}
