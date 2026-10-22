/** Display labels only. Protocol IDs and project archives retain their existing values. */
const engineNames:Record<string,string>={python:'PySWF',native:'PySWF',rust:'RustSWF',js:'JsSWF',go:'GoSWF',asm:'AsmSWF (x86_64)',auto:'Automático',jpexs:'JPEXS',pyflashkit:'pyflashkit',rabcdasm:'RABCDAsm',flasm:'Flasm',flex_swfdump:'Flex SWFDump',flare:'Flare',flashdec_py:'flashdec-py',flashdec_rs:'flashdec-rs',flex:'Apache Flex',mtasc:'MTASC',haxe:'Haxe',haxeflixel:'HaxeFlixel',ruffle:'Ruffle'};
export const displayEngineName=(id:string,fallback?:string)=>engineNames[id]??fallback??id;

