"""Quoted ActionScript/P-code strings with Unicode preserved."""

import json
import re


def parse_string(value: str) -> str:
    if len(value) < 2 or value[0] not in "\"'" or value[-1] != value[0]:
        raise ValueError("Expected a quoted string")
    body = value[1:-1]
    escapes = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}
    result = []
    index = 0
    while index < len(body):
        char = body[index]
        if char != "\\":
            result.append(char)
            index += 1
            continue
        index += 1
        if index == len(body):
            raise ValueError("Unterminated escape sequence")
        char = body[index]
        if char in ("x", "u"):
            length = 2 if char == "x" else 4
            digits = body[index + 1:index + 1 + length]
            if len(digits) != length or not re.fullmatch(r"[0-9a-fA-F]+", digits):
                raise ValueError("Invalid Unicode/hex escape")
            result.append(chr(int(digits, 16)))
            index += length
        else:
            result.append(escapes.get(char, char))
        index += 1
    # Combine escaped UTF-16 surrogate pairs without corrupting literal UTF-8.
    return "".join(result).encode("utf-16-le", "surrogatepass").decode("utf-16-le")


def quote_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def strip_asm_comment(line: str) -> str:
    quoted = escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
        elif char == "\\" and quoted:
            escaped = True
        elif char == '"':
            quoted = not quoted
        elif not quoted and line[index:index + 2] == "//":
            return line[:index].rstrip()
    return line
