"""Expose Flare's generated first-frame body without repairing its source.

Flare emits a consolidated ``movie { frame { ... } }`` document. Its wrapper
is not ActionScript. Only a movie with exactly one root DoAction in its single
frame is admitted here; events, sprites and multiple action records need their
own placement adapter. This check establishes source placement, not correctness.
Independent compilation and bounded runtime checks remain separate operations.
"""
from __future__ import annotations

import hashlib
import re

from .script_inventory import enumerate_scripts
from .swf_parser import SWFFile


def _skip_space_comments(text, start):
    while start < len(text):
        if text[start].isspace():
            start += 1
        elif text.startswith('//', start):
            end = text.find('\n', start + 2)
            start = len(text) if end < 0 else end + 1
        elif text.startswith('/*', start):
            end = text.find('*/', start + 2)
            if end < 0:
                raise ValueError('Unterminated Flare output comment')
            start = end + 2
        else:
            break
    return start


def _body_end(text, start):
    """Find a wrapper's closing brace, ignoring strings and comments."""
    depth, pos = 1, start
    while pos < len(text):
        skipped = _skip_space_comments(text, pos)
        if skipped != pos:
            pos = skipped
            continue
        char = text[pos]
        if char in ('"', "'"):
            quote = char
            pos += 1
            while pos < len(text) and text[pos] != quote:
                pos += 2 if text[pos] == '\\' else 1
            if pos >= len(text):
                raise ValueError('Unterminated Flare output string')
        elif char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                return pos
        pos += 1
    raise ValueError('Unterminated Flare output wrapper')


def flare_complete_action_source(data: bytes, result: dict) -> dict:
    """Return the exact generated body only when its placement is unambiguous.

    The source is a slice of the raw Latin-1 decoded output. No declaration,
    literal, body, or whitespace is repaired using original authored sources.
    """
    base = {'status':'unverified', 'source_origin':'Flare raw output; wrapper removal only',
            'source_fidelity':'not_verified', 'compilation':'not_run', 'behavior':'not_run'}
    try:
        movie = SWFFile()
        movie.read_bytes(data)
        units = list(enumerate_scripts(data, include_nested_avm1=True))
        roots = [unit for unit in units if unit.kind != 'function']
        if (movie.frame_count != 1 or len(roots) != 1 or roots[0].language != 'as2'
                or roots[0].kind != 'frame' or len(roots[0].tag_path) != 1
                or any(unit.diagnostic for unit in units)
                or any(tag.tag_type == 39 for tag in movie.tags)
                or sum(tag.tag_type == 1 for tag in movie.tags) != 1
                or any(tag.tag_type == 1 for tag in movie.tags[:roots[0].tag_path[0]])):
            return {**base, 'status':'not_supported',
                    'diagnostic':'Flare placement supports exactly one root DoAction in frame 1 of a single-frame movie without sprites or event actions'}
        if result.get('warnings'):
            raise ValueError('Flare emitted warnings; retain raw output without complete-source admission')
        if result.get('ok') is not True or result.get('canonical_inventory', {}).get('input_sha256') != hashlib.sha256(data).hexdigest():
            raise ValueError('Flare result is not successful output for this exact SWF input')
        raw = result.get('raw_sources', [])
        if len(raw) != 1 or raw[0].get('encoding') != 'latin-1':
            raise ValueError('Exactly one raw Latin-1 Flare document is required')
        raw = raw[0]
        text = raw.get('source')
        if not isinstance(text, str):
            raise ValueError('Flare raw source was not requested or is unavailable')
        payload = text.encode('latin-1')
        if hashlib.sha256(payload).hexdigest() != raw.get('sha256') or len(payload) != raw.get('bytes'):
            raise ValueError('Flare raw document does not match its recorded bytes and SHA-256')
        prefix = re.match(r"\s*movie\s+'[^'\r\n]*'\s*\{", text)
        if not prefix:
            raise ValueError('Flare output has no supported movie wrapper')
        frame_start = _skip_space_comments(text, prefix.end())
        frame = re.match(r'frame\s+1\s*\{', text[frame_start:])
        if not frame:
            raise ValueError('Flare movie does not start with its single frame 1 wrapper')
        source_start = frame_start + frame.end()
        source_end = _body_end(text, source_start)
        end = _skip_space_comments(text, source_end + 1)
        if end >= len(text) or text[end] != '}' or _skip_space_comments(text, end + 1) != len(text):
            raise ValueError('Flare contains additional frame, event, or trailing output outside the supported action')
        source = text[source_start:source_end]
        if not source.strip():
            raise ValueError('Flare generated an empty action body')
        return {**base, 'status':'complete_generated_actions', 'source':source,
                'source_sha256':hashlib.sha256(source.encode('utf-8')).hexdigest(),
                'raw_sha256':raw['sha256'], 'raw_file':raw['file'],
                'document_id':roots[0].document_id, 'input_sha256':hashlib.sha256(data).hexdigest(),
                'source_span_characters':[source_start,source_end],
                'placement_scope':'single root DoAction; nested functions retained in generated enclosing body',
                'completeness_authority':'All emitted statements retained; one supported placement; semantic correctness is not inferred'}
    except (OSError, ValueError, IndexError, UnicodeError) as error:
        return {**base, 'diagnostic':str(error)}
