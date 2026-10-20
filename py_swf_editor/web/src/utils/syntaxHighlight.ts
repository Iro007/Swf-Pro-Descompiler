import React from "react";

/**
 * Simple syntax highlighter for ActionScript / AVM2 assembly / AVM1 assembly
 * No external dependencies - lightweight regex-based highlighting
 */

export interface HighlightToken {
  type: string;
  text: string;
}

const AS_KEYWORDS = new Set([
  'package', 'class', 'interface', 'extends', 'implements', 'public', 'private',
  'protected', 'internal', 'static', 'final', 'override', 'function', 'var',
  'const', 'if', 'else', 'for', 'while', 'do', 'switch', 'case', 'default',
  'break', 'continue', 'return', 'throw', 'try', 'catch', 'finally', 'new',
  'this', 'super', 'null', 'true', 'false', 'undefined', 'typeof', 'instanceof',
  'in', 'as', 'is', 'import', 'use', 'namespace', 'include', 'dynamic', 'native',
  'get', 'set', 'override', 'prototype', 'constructor', 'arguments', 'void',
  'int', 'uint', 'Number', 'String', 'Boolean', 'Object', 'Array', 'Date',
  'Error', 'RegExp', 'XML', 'XMLList', 'Vector', 'Dictionary', 'ByteArray',
]);

const AS_TYPES = new Set([
  'int', 'uint', 'Number', 'String', 'Boolean', 'Object', 'Array', 'Date',
  'Error', 'RegExp', 'XML', 'XMLList', 'Vector', 'Dictionary', 'ByteArray',
  'Sprite', 'MovieClip', 'DisplayObject', 'DisplayObjectContainer', 'Shape',
  'Graphics', 'Bitmap', 'TextField', 'SimpleButton', 'Loader', 'URLLoader',
  'Event', 'MouseEvent', 'KeyboardEvent', 'TimerEvent', 'ProgressEvent',
]);

const AVM2_OPCODES = new Set([
  'bkpt', 'nop', 'throw', 'getsuper', 'setsuper', 'dxns', 'dxnslate', 'kill',
  'label', 'ifnlt', 'ifnle', 'ifngt', 'ifnge', 'jump', 'iftrue', 'iffalse',
  'ifeq', 'ifne', 'iflt', 'ifle', 'ifgt', 'ifge', 'ifstricteq', 'ifstrictne',
  'lookupswitch', 'pushwith', 'popscope', 'nextname', 'hasnext', 'pushnull',
  'pushundefined', 'nextvalue', 'pushbyte', 'pushshort', 'pushtrue', 'pushfalse',
  'pushnan', 'pop', 'dup', 'swap', 'pushstring', 'pushint', 'pushuint', 'pushdouble',
  'pushscope', 'pushnamespace', 'hasnext2', 'li8', 'li16', 'li32', 'lf32', 'lf64',
  'si8', 'si16', 'si32', 'sf32', 'sf64', 'newfunction', 'call', 'construct',
  'callmethod', 'callstatic', 'callsuper', 'callproperty', 'returnvoid', 'returnvalue',
  'constructsuper', 'constructprop', 'callproplex', 'callsupervoid', 'callpropvoid',
  'sxi1', 'sxi8', 'sxi16', 'applytype', 'newobject', 'newarray', 'newactivation',
  'newclass', 'getdescendants', 'newcatch', 'findpropstrict', 'findproperty', 'finddef',
  'getlex', 'setproperty', 'getlocal', 'setlocal', 'getglobalscope', 'getscopeobject',
  'getproperty', 'initproperty', 'deleteproperty', 'getslot', 'setslot', 'getglobalslot',
  'setglobalslot', 'convert_s', 'esc_xelem', 'esc_xattr', 'convert_i', 'convert_u',
  'convert_d', 'convert_b', 'convert_o', 'checkfilter', 'coerce', 'coerce_b', 'coerce_a',
  'coerce_i', 'coerce_d', 'coerce_s', 'astype', 'astypelate', 'coerce_u', 'coerce_o',
  'negate', 'increment', 'inclocal', 'decrement', 'declocal', 'typeof', 'not', 'bitnot',
  'add', 'subtract', 'multiply', 'divide', 'modulo', 'lshift', 'rshift', 'urshift',
  'bitand', 'bitor', 'bitxor', 'equals', 'strictequals', 'lessthan', 'lessequals',
  'greaterthan', 'greaterequals', 'instanceof', 'istype', 'istypelate', 'in',
  'increment_i', 'decrement_i', 'inclocal_i', 'declocal_i', 'negate_i', 'add_i',
  'subtract_i', 'multiply_i', 'getlocal_0', 'getlocal_1', 'getlocal_2', 'getlocal_3',
  'setlocal_0', 'setlocal_1', 'setlocal_2', 'setlocal_3', 'debug', 'debugline',
  'debugfile', 'bkptline', 'timestamp',
]);

const AVM1_OPCODES = new Set([
  'next_frame', 'prev_frame', 'play', 'stop', 'toggle_quality', 'stop_sounds',
  'add', 'subtract', 'multiply', 'divide', 'equals', 'less_than', 'and', 'or', 'not',
  'string_equals', 'string_length', 'string_extract', 'pop', 'to_integer',
  'get_variable', 'set_variable', 'set_target2', 'string_add', 'get_property',
  'set_property', 'clone_sprite', 'remove_sprite', 'trace', 'start_drag', 'end_drag',
  'string_less_than', 'throw', 'cast_op', 'implements_op', 'random_number',
  'mb_string_length', 'char_to_ascii', 'ascii_to_char', 'get_time', 'mb_string_extract',
  'mb_char_to_ascii', 'mb_ascii_to_char', 'delete', 'delete2', 'define_local',
  'call_function', 'return', 'modulo', 'new_object', 'define_local2', 'init_array',
  'init_object', 'type_of', 'target_path', 'enumerate', 'add2', 'less2', 'equals2',
  'to_number', 'to_string', 'push_duplicate', 'stack_swap', 'get_member', 'set_member',
  'increment', 'decrement', 'call_method', 'new_method', 'instance_of', 'enumerate2',
  'bit_and', 'bit_or', 'bit_xor', 'bit_lshift', 'bit_rshift', 'bit_urshift',
  'strict_equals', 'greater', 'string_greater', 'extends', 'goto_frame', 'get_url',
  'store_register', 'constant_pool', 'strict_mode', 'wait_for_frame', 'set_target',
  'goto_label', 'wait_for_frame2', 'define_function2', 'with', 'push', 'jump',
  'get_url2', 'define_function', 'if', 'goto_frame2',
]);

function tokenizeAS(code: string): HighlightToken[] {
  const tokens: HighlightToken[] = [];
  let i = 0;
  
  while (i < code.length) {
    const ch = code[i];
    
    // Whitespace
    if (/\s/.test(ch)) {
      let j = i;
      while (j < code.length && /\s/.test(code[j])) j++;
      tokens.push({ type: 'whitespace', text: code.slice(i, j) });
      i = j;
      continue;
    }
    
    // Comments
    if (ch === '/' && i + 1 < code.length) {
      if (code[i + 1] === '/') {
        let j = i + 2;
        while (j < code.length && code[j] !== '\n') j++;
        tokens.push({ type: 'comment', text: code.slice(i, j) });
        i = j;
        continue;
      }
      if (code[i + 1] === '*') {
        let j = i + 2;
        while (j < code.length - 1 && !(code[j] === '*' && code[j + 1] === '/')) j++;
        j += 2;
        tokens.push({ type: 'comment', text: code.slice(i, j) });
        i = j;
        continue;
      }
    }
    
    // Strings
    if (ch === '"' || ch === "'") {
      const quote = ch;
      let j = i + 1;
      while (j < code.length) {
        if (code[j] === '\\' && j + 1 < code.length) {
          j += 2;
        } else if (code[j] === quote) {
          j++;
          break;
        } else {
          j++;
        }
      }
      tokens.push({ type: 'string', text: code.slice(i, j) });
      i = j;
      continue;
    }
    
    // Numbers
    if (/\d/.test(ch) || (ch === '.' && i + 1 < code.length && /\d/.test(code[i + 1]))) {
      let j = i;
      if (code[j] === '0' && j + 1 < code.length && /[xX]/.test(code[j + 1])) {
        j += 2;
        while (j < code.length && /[0-9a-fA-F]/.test(code[j])) j++;
      } else {
        while (j < code.length && /[\d.]/.test(code[j])) j++;
        if (j < code.length && /[eE]/.test(code[j])) {
          j++;
          if (j < code.length && /[+-]/.test(code[j])) j++;
          while (j < code.length && /\d/.test(code[j])) j++;
        }
      }
      tokens.push({ type: 'number', text: code.slice(i, j) });
      i = j;
      continue;
    }
    
    // Labels (L_123:)
    if (ch === 'L' && i + 1 < code.length && code[i + 1] === '_') {
      let j = i + 2;
      while (j < code.length && /\d/.test(code[j])) j++;
      if (j < code.length && code[j] === ':') j++;
      tokens.push({ type: 'label', text: code.slice(i, j) });
      i = j;
      continue;
    }
    
    // Identifiers / keywords
    if (/[a-zA-Z_$]/.test(ch)) {
      let j = i + 1;
      while (j < code.length && /[a-zA-Z0-9_$]/.test(code[j])) j++;
      const word = code.slice(i, j);
      
      let type = 'identifier';
      if (AS_KEYWORDS.has(word)) type = 'keyword';
      else if (AS_TYPES.has(word)) type = 'type';
      else if (AVM2_OPCODES.has(word.toLowerCase())) type = 'opcode';
      else if (AVM1_OPCODES.has(word.toLowerCase())) type = 'opcode';
      else if (/^[A-Z]/.test(word)) type = 'class';
      
      tokens.push({ type, text: word });
      i = j;
      continue;
    }
    
    // Operators / punctuation
    if (/[+\-*/%=<>!&|^~?:;.,{}()\[\]]/.test(ch)) {
      // Check for multi-char operators
      if (i + 1 < code.length) {
        const two = ch + code[i + 1];
        if (['===', '!==', '==', '!=', '<=', '>=', '<<', '>>', '>>>', '&&', '||', '++', '--', '+=', '-=', '*=', '/=', '%=', '&=', '|=', '^='].includes(two)) {
          tokens.push({ type: 'operator', text: two });
          i += 2;
          continue;
        }
        if (i + 2 < code.length) {
          const three = two + code[i + 2];
          if (['===', '!==', '<<=', '>>=', '>>>='].includes(three)) {
            tokens.push({ type: 'operator', text: three });
            i += 3;
            continue;
          }
        }
      }
      tokens.push({ type: 'operator', text: ch });
      i++;
      continue;
    }
    
    // Unknown char
    tokens.push({ type: 'unknown', text: ch });
    i++;
  }
  
  return tokens;
}

export function highlightCode(code: string, language: 'as' | 'avm2' | 'avm1' = 'as'): HighlightToken[] {
  return tokenizeAS(code);
}

export function renderHighlighted(tokens: HighlightToken[]): React.ReactNode {
  const styleMap: Record<string, string> = {
    keyword: 'text-emerald-400',
    type: 'text-blue-400',
    class: 'text-yellow-300',
    opcode: 'text-purple-400',
    string: 'text-green-300',
    number: 'text-orange-300',
    comment: 'text-slate-500 italic',
    label: 'text-cyan-400',
    operator: 'text-slate-300',
    identifier: 'text-slate-200',
    whitespace: '',
    unknown: 'text-red-400',
  };

  return React.createElement(
    React.Fragment,
    null,
    tokens.map((token, i) => React.createElement(
      "span",
      { key: i, className: styleMap[token.type] || "" },
      token.text,
    )),
  );
}
