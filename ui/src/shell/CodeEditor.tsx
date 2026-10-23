import {useEffect,useRef} from 'react';
import {Annotation,Compartment,EditorState,Extension,Transaction} from '@codemirror/state';
import {EditorView,drawSelection,highlightActiveLine,highlightActiveLineGutter,highlightSpecialChars,keymap,lineNumbers,placeholder as placeholderExtension} from '@codemirror/view';
import {defaultKeymap,history,historyKeymap,indentWithTab} from '@codemirror/commands';
import {HighlightStyle,bracketMatching,indentOnInput,syntaxHighlighting} from '@codemirror/language';
import {highlightSelectionMatches,searchKeymap} from '@codemirror/search';
import {javascript} from '@codemirror/lang-javascript';
import {tags} from '@lezer/highlight';
import {pcodeLanguage} from './pcodeLanguage';
import {useLatest} from './util';

export type CodeLanguage='actionscript'|'pcode'|'text';

/** Colours come from CSS tokens, so the editor follows the light/dark theme without rebuilding. */
const highlightStyle=HighlightStyle.define([
  {tag:[tags.keyword,tags.modifier,tags.operatorKeyword,tags.controlKeyword,tags.definitionKeyword,tags.moduleKeyword,tags.self],color:'var(--syn-keyword)'},
  {tag:[tags.string,tags.special(tags.string),tags.regexp],color:'var(--syn-string)'},
  {tag:[tags.number,tags.bool,tags.null,tags.atom],color:'var(--syn-number)'},
  {tag:[tags.comment,tags.lineComment,tags.blockComment,tags.docComment],color:'var(--syn-comment)',fontStyle:'italic'},
  {tag:[tags.typeName,tags.className,tags.namespace,tags.standard(tags.typeName)],color:'var(--syn-type)'},
  {tag:[tags.function(tags.variableName),tags.function(tags.propertyName),tags.definition(tags.function(tags.variableName))],color:'var(--syn-function)'},
  {tag:[tags.propertyName,tags.attributeName],color:'var(--syn-property)'},
  {tag:[tags.labelName],color:'var(--syn-label)'},
  {tag:[tags.operator,tags.punctuation,tags.bracket,tags.separator],color:'var(--syn-punct)'},
  {tag:tags.invalid,color:'var(--danger)'},
]);

const theme=EditorView.theme({
  '&':{height:'100%',color:'var(--code-fg)',backgroundColor:'var(--code-bg)',fontSize:'12.5px'},
  '&.cm-focused':{outline:'none'},
  '.cm-scroller':{fontFamily:'var(--mono)',lineHeight:'1.6',overflow:'auto'},
  '.cm-content':{caretColor:'var(--fg)',padding:'6px 0'},
  '.cm-line':{padding:'0 12px 0 6px'},
  '.cm-gutters':{backgroundColor:'var(--code-gutter)',color:'var(--code-gutter-fg)',border:'none',borderRight:'1px solid var(--line)'},
  '.cm-activeLine':{backgroundColor:'var(--code-active-line)'},
  '.cm-activeLineGutter':{backgroundColor:'var(--code-active-line)',color:'var(--fg)'},
  '.cm-cursor, .cm-dropCursor':{borderLeftColor:'var(--fg)'},
  '&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection':{backgroundColor:'var(--code-selection)'},
  '.cm-selectionMatch':{backgroundColor:'var(--code-match)'},
  '.cm-matchingBracket, &.cm-focused .cm-matchingBracket':{backgroundColor:'var(--code-match)',outline:'1px solid var(--line-strong)'},
  '.cm-placeholder':{color:'var(--fg-dim)',fontStyle:'italic'},
  '.cm-panels':{backgroundColor:'var(--bg-panel)',color:'var(--fg)',borderColor:'var(--line)'},
  '.cm-panels.cm-panels-bottom':{borderTop:'1px solid var(--line)'},
  '.cm-panels input, .cm-panels button':{font:'inherit',fontSize:'12px'},
  '.cm-panels input[type=text], .cm-panels .cm-textfield':{backgroundColor:'var(--bg-input)',color:'var(--fg)',border:'1px solid var(--line-strong)',borderRadius:'3px',padding:'2px 6px'},
  '.cm-panels .cm-button':{backgroundImage:'none',backgroundColor:'var(--bg-raised)',color:'var(--fg)',border:'1px solid var(--line-strong)',borderRadius:'3px'},
  '.cm-searchMatch':{backgroundColor:'var(--code-match)'},
  '.cm-searchMatch.cm-searchMatch-selected':{backgroundColor:'var(--code-selection)'},
});

const external=Annotation.define<boolean>();
const languageFor=(language:CodeLanguage):Extension=>language==='actionscript'?javascript({typescript:true}):language==='pcode'?pcodeLanguage:[];

interface Props {
  value:string;
  /** Changing the key starts a new document (fresh undo history). */
  docKey:string;
  /**
   * The text typed in the editor is never overwritten by `value` while this stays the same: the editor owns the
   * text during editing. Change it when the owner wants to replace the content (loaded, discarded, applied).
   */
  syncKey:string;
  language:CodeLanguage;
  label:string;
  readOnly?:boolean;
  placeholder?:string;
  testId?:string;
  onChange?:(value:string)=>void;
}

/** CodeMirror 6 view: syntax highlighting, line numbers, search, history; editable or read-only. */
export default function CodeEditor({value,docKey,syncKey,language,label,readOnly=false,placeholder,testId,onChange}:Props){
  const host=useRef<HTMLDivElement>(null);
  const view=useRef<EditorView|null>(null);
  const changed=useLatest(onChange);
  const dynamic=useRef({language:new Compartment(),readOnly:new Compartment(),label:new Compartment(),placeholder:new Compartment()});
  const latest=useLatest({value,language,label,readOnly,placeholder});

  const stateFor=(doc:string)=>EditorState.create({
    doc,
    extensions:[
      lineNumbers(),highlightActiveLineGutter(),highlightSpecialChars(),history(),drawSelection(),indentOnInput(),bracketMatching(),
      highlightActiveLine(),highlightSelectionMatches(),syntaxHighlighting(highlightStyle),EditorState.tabSize.of(4),
      keymap.of([...defaultKeymap,...historyKeymap,...searchKeymap,indentWithTab]),
      theme,
      dynamic.current.language.of(languageFor(latest.current.language)),
      dynamic.current.readOnly.of(EditorState.readOnly.of(latest.current.readOnly)),
      dynamic.current.label.of(EditorView.contentAttributes.of({'aria-label':latest.current.label,'aria-readonly':String(latest.current.readOnly)})),
      dynamic.current.placeholder.of(latest.current.placeholder?placeholderExtension(latest.current.placeholder):[]),
      EditorView.updateListener.of(update=>{
        if(update.docChanged&&!update.transactions.some(transaction=>transaction.annotation(external)))changed.current?.(update.state.doc.toString());
      }),
    ],
  });

  useEffect(()=>{
    if(!host.current)return;
    const editor=new EditorView({state:stateFor(latest.current.value),parent:host.current});
    view.current=editor;
    return()=>{editor.destroy();view.current=null;};
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[]);

  const firstKey=useRef(docKey);
  useEffect(()=>{
    if(firstKey.current===docKey||!view.current)return;
    firstKey.current=docKey;
    view.current.setState(stateFor(latest.current.value));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[docKey]);

  useEffect(()=>{
    const editor=view.current;
    const next=latest.current.value;
    if(!editor||editor.state.doc.toString()===next)return;
    editor.dispatch({changes:{from:0,to:editor.state.doc.length,insert:next},annotations:[external.of(true),Transaction.addToHistory.of(false)]});
  },[syncKey,docKey]);

  useEffect(()=>{view.current?.dispatch({effects:dynamic.current.language.reconfigure(languageFor(language))});},[language]);
  useEffect(()=>{view.current?.dispatch({effects:dynamic.current.readOnly.reconfigure(EditorState.readOnly.of(readOnly))});},[readOnly]);
  useEffect(()=>{view.current?.dispatch({effects:dynamic.current.label.reconfigure(EditorView.contentAttributes.of({'aria-label':label,'aria-readonly':String(readOnly)}))});},[label,readOnly]);
  useEffect(()=>{view.current?.dispatch({effects:dynamic.current.placeholder.reconfigure(placeholder?placeholderExtension(placeholder):[])});},[placeholder]);

  return <div ref={host} className={`code-editor ${readOnly?'read-only':''}`} data-testid={testId}/>;
}
