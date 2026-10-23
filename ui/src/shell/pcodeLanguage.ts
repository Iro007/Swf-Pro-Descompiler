import {StreamLanguage} from '@codemirror/language';

interface PcodeState {lineStart:boolean}

/**
 * P-code (AVM2 instructions or AVM1 actions) as the suite prints it: one instruction per line,
 * `mnemonic operands // comment`, `#n` constant-pool references and `label:` jump targets.
 */
export const pcodeLanguage=StreamLanguage.define<PcodeState>({
  name:'pcode',
  startState:()=>({lineStart:true}),
  token(stream,state){
    if(stream.sol())state.lineStart=true;
    if(stream.eatSpace())return null;
    if(stream.match('//')){stream.skipToEnd();return 'comment';}
    if(stream.match(/^"(?:[^"\\]|\\.)*"?/))return 'string';
    if(stream.match(/^#\d+/))return 'atom';
    if(stream.match(/^-?(?:0x[0-9a-f]+|\d+(?:\.\d+)?(?:e[+-]?\d+)?)\b/i)){state.lineStart=false;return 'number';}
    if(state.lineStart){
      state.lineStart=false;
      if(stream.match(/^[A-Za-z_$][\w$.]*:(?!:)/))return 'labelName';
      if(stream.match(/^[A-Za-z_$][\w$]*/))return 'keyword';
    }
    if(stream.match(/^[A-Za-z_$][\w$.:]*/))return 'variableName';
    stream.next();
    return null;
  },
  languageData:{commentTokens:{line:'//'}},
});
