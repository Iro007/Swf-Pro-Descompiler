import {SuiteProfile} from './types';
export const compileProfiles:{id:SuiteProfile;label:string;engine:string;hint:string}[]=[
  {id:'auto-as3',label:'AS3 · automático',engine:'python',hint:'Prueba PySWF y utiliza Flex cuando está disponible.'},
  {id:'native-as3',label:'AS3 · PySWF',engine:'python',hint:'Compila el subconjunto compatible de ActionScript 3 con PySWF.'},
  {id:'as3-flex',label:'AS3 · Apache Flex',engine:'flex',hint:'Compila proyectos ActionScript 3 con el SDK Flex configurado.'},
  {id:'auto-as2',label:'AS1/AS2 · automático',engine:'python',hint:'Compila un script de cuadro AVM1 con los motores disponibles.'},
  {id:'native-as2',label:'AS1/AS2 · PySWF',engine:'python',hint:'Compila el subconjunto compatible de scripts AVM1 con PySWF.'},
  {id:'as2-jpexs',label:'AS1/AS2 · JPEXS',engine:'jpexs',hint:'Compila un script de cuadro con JPEXS.'},
  {id:'as2-mtasc',label:'AS2 · MTASC',engine:'mtasc',hint:'Compila clases ActionScript 2 con MTASC.'},
  {id:'haxe',label:'Haxe · SWF',engine:'haxe',hint:'Compila fuentes Haxe o un archivo .hxml para Flash.'},
  {id:'haxeflixel',label:'HaxeFlixel · Flash',engine:'haxeflixel',hint:'Compila Project.xml, sus fuentes y recursos con Lime.'},
];
