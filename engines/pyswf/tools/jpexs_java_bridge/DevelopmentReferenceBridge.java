/* PySWF Studio development-only adapter. GPL-3.0.
 * Calls ffdec_lib 26.3.0 (LGPL-3.0) and minimal-json (MIT) through their APIs.
 * No decompiler algorithm is copied here. No user/agent configuration is changed.
 */
import com.eclipsesource.json.*;
import com.jpexs.decompiler.flash.SWF;
import com.jpexs.decompiler.flash.abc.ABC;
import com.jpexs.decompiler.flash.abc.ScriptPack;
import com.jpexs.decompiler.flash.abc.types.ConvertData;
import com.jpexs.decompiler.flash.abc.types.MethodBody;
import com.jpexs.decompiler.flash.abc.types.traits.*;
import com.jpexs.decompiler.flash.action.parser.script.ActionScript2Parser;
import com.jpexs.decompiler.flash.configuration.Configuration;
import com.jpexs.decompiler.flash.exporters.modes.ScriptExportMode;
import com.jpexs.decompiler.flash.helpers.HighlightedTextWriter;
import com.jpexs.decompiler.flash.helpers.hilight.Highlighting;
import com.jpexs.decompiler.flash.importers.FFDecAs3ScriptReplacer;
import com.jpexs.decompiler.flash.importers.As3ScriptReplaceException;
import com.jpexs.decompiler.flash.tags.*;
import com.jpexs.decompiler.flash.tags.base.ASMSource;
import com.jpexs.decompiler.flash.tags.base.PlaceObjectTypeTag;
import com.jpexs.decompiler.flash.timeline.Timelined;
import com.jpexs.decompiler.flash.types.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;

public final class DevelopmentReferenceBridge {
    private static double ms(long started) { return (System.nanoTime()-started)/1e6; }
    private static String digest(byte[] bytes) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
    }
    private static String id(ASMSource source, Map<Tag,String> paths) {
        Tag tag = source.getSourceTag();
        String path = paths.get(tag);
        if (path == null) throw new IllegalArgumentException("Source tag has no canonical path");
        if (source instanceof BUTTONCONDACTION action && tag instanceof DefineButton2Tag button)
            return path+"/button2:"+button.actions.indexOf(action);
        if (source instanceof CLIPACTIONRECORD action && tag instanceof PlaceObjectTypeTag place)
            return path+"/clip:"+place.getClipActions().clipActionRecords.indexOf(action);
        if (tag instanceof DefineButtonTag) return path+"/button:0";
        return path+"/actions";
    }
    private static void paths(Timelined container, String parent, Map<Tag,String> paths) {
        int index=0;
        for (Tag tag: container.getTags()) {
            String path=parent.isEmpty()?"tag:"+index:parent+"/"+index;
            paths.put(tag,path);
            if (tag instanceof DefineSpriteTag sprite) paths(sprite,path,paths);
            index++;
        }
    }
    private static JsonObject sourceRecord(String id, String language, byte[] code, String source, boolean include) throws Exception {
        JsonObject result = new JsonObject().add("document_id",id).add("language",language)
            .add("bytecode_sha256",digest(code)).add("bytecode_bytes",code.length)
            .add("generation", "available").add("source_sha256",digest(source.getBytes(StandardCharsets.UTF_8)))
            .add("source_characters",source.length()).add("syntax","not_checked")
            .add("compilation","not_run").add("behavior","not_run");
        if (include) result.add("source",source);
        return result;
    }
    private static JsonObject failure(String id, String language, String error) {
        return new JsonObject().add("document_id",id).add("language",language).add("generation","failed")
            .add("diagnostic",error).add("syntax","not_checked").add("compilation","not_run").add("behavior","not_run");
    }
    private static JsonObject decompile(SWF swf, boolean include) throws Exception {
        long start=System.nanoTime();
        Map<Tag,String> paths=new IdentityHashMap<>();
        paths(swf,"",paths);
        JsonArray units=new JsonArray(), documents=new JsonArray();
        for (var entry: swf.getASMs(false).entrySet()) {
            ASMSource source=entry.getValue();
            String id;
            try { id=id(source,paths); }
            catch (Exception error) { units.add(failure("unmapped:"+entry.getKey(),"as2",error.toString())); continue; }
            try {
                byte[] code=source.getActionBytes().getRangeData();
                long absolute=source.getActionBytes().getPos();
                JsonArray tagPath=ParityBridgeInventory.path(id);
                var nested=ParityBridgeInventory.functions(code,absolute,id,tagPath);
                ParityBridgeInventory.OffsetWriter writer=new ParityBridgeInventory.OffsetWriter();
                String text="";String sourceError=null;
                try{source.getActionScriptSource(writer,source.getActions());text=writer.toString();writer.finishHilights();}
                catch(Exception error){sourceError=error.toString();}
                JsonObject action=sourceError==null?sourceRecord(id,"as2",code,text,include):failure(id,"as2",sourceError).add("bytecode_sha256",digest(code)).add("bytecode_bytes",code.length);
                units.add(action.add("scope","action").add("source_kind","action_source").add("tag_path",tagPath).add("normalized_byte_start",absolute).add("identity_verified",true));
                String spanText=text.replace("\r\n","\n");
                for(var function:nested){
                    List<Highlighting> matches=new ArrayList<>();
                    for(Highlighting highlight:writer.methodHilights)if(highlight.getProperties().fileOffset==function.headerOffset)matches.add(highlight);
                    JsonObject record=function.record;
                    if(sourceError==null&&matches.size()==1){Highlighting h=matches.get(0);String fragment=spanText.substring(h.startPos,h.startPos+h.len);
                        record.add("generation","available").add("source_kind","function_definition").add("source_sha256",digest(fragment.getBytes(StandardCharsets.UTF_8))).add("source_characters",fragment.length()).add("source_utf8_bytes",fragment.getBytes(StandardCharsets.UTF_8).length).add("source_document",id).add("source_header_byte_offset",function.headerOffset).add("source_mapping","graph_source_action_file_offset");if(include)record.add("source",fragment);
                    }else record.add("generation","failed").add("source_kind","unmapped_function").add("source_mapping","unverified").add("diagnostic",sourceError==null?"No unique function source highlight matched the exact defining action offset":sourceError);
                    record.add("syntax","not_checked").add("compilation","not_run").add("behavior","not_run").add("source_fidelity","not_verified");units.add(record);
                }
            } catch (Exception error) { units.add(failure(id,"as2",error.toString())); }
        }
        Map<ABC,String> abcPaths=new IdentityHashMap<>();
        Map<ABC,String> abcHashes=new IdentityHashMap<>();
        for (ABCContainerTag container: swf.getAbcList()) abcPaths.put(container.getABC(),paths.get((Tag)container));
        for(ABCContainerTag container:swf.getAbcList()){Tag tag=(Tag)container;byte[] raw=tag.getOriginalData();int offset=0;if(tag.getId()==82){offset=4;while(offset<raw.length&&raw[offset]!=0)offset++;offset++;}abcHashes.put(container.getABC(),digest(Arrays.copyOfRange(raw,offset,raw.length)));}
        Map<String,JsonObject> methods=new LinkedHashMap<>();
        Map<String,JsonObject> classes=new LinkedHashMap<>();
        Map<String,List<String>> scriptSources=new LinkedHashMap<>();
        Set<String> methodHighlights=new HashSet<>();
        for (ScriptPack pack: swf.getAS3Packs()) {
            String prefix=abcPaths.get(pack.abc);
            String documentId=prefix+"/script:"+pack.scriptIndex+"/traits:"+pack.traitIndices;
            try {
                HighlightedTextWriter writer=new HighlightedTextWriter(Configuration.getCodeFormatting(),true);
                pack.toSource(swf.getAbcIndex(),writer,pack.abc.script_info.get(pack.scriptIndex).traits.traits,new ConvertData(),ScriptExportMode.AS,false,false,false);
                String source=writer.toString();
                writer.finishHilights();
                String spanSource=source.replace("\r\n","\n");
                JsonObject document=new JsonObject().add("document_id",documentId).add("name",pack.getPathScriptName())
                    .add("source_sha256",digest(source.getBytes(StandardCharsets.UTF_8))).add("generation","available");
                if (include) document.add("source",source);
                documents.add(document);
                scriptSources.computeIfAbsent(prefix+"/script:"+pack.scriptIndex,k->new ArrayList<>()).add(source);
                for (Highlighting highlight: writer.methodHilights) {
                    int index=(int)highlight.getProperties().index;
                    int bodyIndex=pack.abc.findBodyIndex(index);
                    if (bodyIndex<0) continue;
                    MethodBody body=pack.abc.bodies.get(bodyIndex);
                    String id=prefix+"/method:"+index;
                    String fragment=spanSource.substring(highlight.startPos,highlight.startPos+highlight.len);
                    JsonObject record=sourceRecord(id,"as3",body.getCodeBytes(),fragment,include)
                        .add("body_index",bodyIndex).add("method_index",index).add("scope","method").add("abc_sha256",abcHashes.get(pack.abc)).add("source_kind","method_definition").add("source_document",documentId);
                    methodHighlights.add(id);
                    boolean scriptInitializer=false;for(var script:pack.abc.script_info)if(script.init_index==index)scriptInitializer=true;
                    if(scriptInitializer||fragment.trim().isEmpty()){record.add("generation","represented_by_document").add("source_kind",scriptInitializer?"script_declarations":"class_declarations").add("represented_by_document",scriptInitializer?prefix+"/script:"+pack.scriptIndex:documentId).add("pcode_available",true);if(include)record.add("pcode",body.getCode().toASMSource(pack.abc));}
                    if (body.convertException!=null) record.add("generation","failed").add("diagnostic",body.convertException.toString());
                    JsonObject previous=methods.get(id);if(previous==null||previous.getString("generation","").equals("represented_by_document")&&!fragment.trim().isEmpty())methods.put(id,record);
                }
                for(Highlighting highlight:writer.classHilights){int index=(int)highlight.getProperties().index;if(index<0||index>=pack.abc.instance_info.size())continue;
                    Set<Integer> owned=new TreeSet<>();var instance=pack.abc.instance_info.get(index);var cls=pack.abc.class_info.get(index);owned.add(instance.iinit_index);owned.add(cls.cinit_index);owned.addAll(ParityBridgeInventory.traitMethods(instance.instance_traits));owned.addAll(ParityBridgeInventory.traitMethods(cls.static_traits));
                    byte[] aggregate=ParityBridgeInventory.aggregate(pack.abc,owned);String classId=prefix+"/class:"+index;
                    JsonObject record=sourceRecord(classId,"as3",aggregate,source,include).add("scope","class").add("class_index",index).add("abc_sha256",abcHashes.get(pack.abc)).add("source_kind","class_document").add("source_document",documentId);
                    JsonArray indices=new JsonArray();boolean complete=true;for(int method:owned){indices.add(method);int bi=pack.abc.findBodyIndex(method);if(bi>=0&&(pack.abc.bodies.get(bi).convertException!=null||!methodHighlights.contains(prefix+"/method:"+method)&&!ParityBridgeInventory.empty(pack.abc.bodies.get(bi).getCodeBytes())))complete=false;}
                    record.add("method_indices",indices).add("completeness",complete?"complete":"partial");if(!complete)record.add("generation","partial_source").add("diagnostic","Class source does not expose every owned body or an initializer conversion failed");
                    classes.putIfAbsent(classId,record);
                }
            } catch (Exception error) { documents.add(failure(documentId,"as3",error.toString())); }
        }
        for (ABCContainerTag container: swf.getAbcList()) {
            ABC abc=container.getABC();
            for (int index=0;index<abc.bodies.size();index++) {
                MethodBody body=abc.bodies.get(index);
                String id=abcPaths.get(abc)+"/method:"+body.method_info;
                JsonObject record=methods.get(id);
                if (record==null) {
                    byte[] code=body.getCodeBytes();
                    // Exact side-effect-free initializer encodings. They may
                    // legitimately be omitted from a class source document.
                    boolean empty=Arrays.equals(code,new byte[]{0x47}) || Arrays.equals(code,new byte[]{(byte)0xd0,0x30,0x47});
                    record=empty?sourceRecord(id,"as3",code,"",include).add("generation","represented_by_document").add("source_kind","structural_initializer"):
                        failure(id,"as3","No method source span exposed; not counted as verified").add("bytecode_sha256",digest(code));
                    record.add("body_index",index).add("method_index",body.method_info);
                }
                record.add("scope","method").add("abc_sha256",abcHashes.get(abc)).add("bytecode_bytes",body.getCodeBytes().length);
                if(include&&record.getString("generation","").equals("represented_by_document")&&record.get("pcode")==null)record.add("pcode",body.getCode().toASMSource(abc)).add("pcode_available",true);
                units.add(record);
            }
            for(int index=0;index<abc.class_info.size();index++){String id=abcPaths.get(abc)+"/class:"+index;JsonObject record=classes.get(id);if(record==null){Set<Integer> owned=new TreeSet<>();var instance=abc.instance_info.get(index);var cls=abc.class_info.get(index);owned.add(instance.iinit_index);owned.add(cls.cinit_index);owned.addAll(ParityBridgeInventory.traitMethods(instance.instance_traits));owned.addAll(ParityBridgeInventory.traitMethods(cls.static_traits));byte[] aggregate=ParityBridgeInventory.aggregate(abc,owned);record=failure(id,"as3","No unique class source document exposed").add("bytecode_sha256",digest(aggregate)).add("bytecode_bytes",aggregate.length).add("completeness","partial");}units.add(record.add("scope","class").add("class_index",index).add("abc_sha256",abcHashes.get(abc)));}
            for(int index=0;index<abc.script_info.size();index++){var script=abc.script_info.get(index);String id=abcPaths.get(abc)+"/script:"+index;Set<Integer> owned=new TreeSet<>();owned.add(script.init_index);owned.addAll(ParityBridgeInventory.traitMethods(script.traits));byte[] aggregate=ParityBridgeInventory.aggregate(abc,owned);List<String> pieces=scriptSources.getOrDefault(id,List.of());String text=String.join("\n",pieces);
                JsonObject record=sourceRecord(id,"as3",aggregate,text,include).add("scope","script").add("script_index",index).add("abc_sha256",abcHashes.get(abc)).add("source_kind","script_document");boolean complete=!pieces.isEmpty();
                for(int method:owned){int bi=abc.findBodyIndex(method);if(bi>=0&&(abc.bodies.get(bi).convertException!=null||method==script.init_index&&abc.bodies.get(bi).convertedItems==null||method!=script.init_index&&!methodHighlights.contains(abcPaths.get(abc)+"/method:"+method)))complete=false;}
                for(Trait trait:script.traits.traits)if(trait instanceof TraitClass cls){JsonObject classRecord=classes.get(abcPaths.get(abc)+"/class:"+cls.class_info);if(classRecord==null||!classRecord.getString("completeness","").equals("complete"))complete=false;}
                record.add("completeness",complete?"complete":"partial").add("initializer_representation","document_declarations_or_statements; not method source");if(!complete)record.add("generation","partial_source").add("diagnostic","Script source or declared class representation is incomplete");units.add(record);
            }
        }
        return new JsonObject().add("units",units).add("documents",documents).add("generate_ms",ms(start));
    }
    private static JsonObject compile(SWF swf, JsonObject request) throws Exception {
        Map<Tag,String> paths=new IdentityHashMap<>();
        paths(swf,"",paths);
        String wanted=request.getString("document_id","");
        String text=request.getString("source","");
        boolean found=false;
        for (ASMSource source: swf.getASMs(false).values()) if (id(source,paths).equals(wanted)) {
            source.setActions(new ActionScript2Parser(swf,source).actionsFromString(text,swf.getCharset()));
            source.setModified(); found=true; break;
        }
        if (!found) {
          List<ScriptPack> matchingPacks=new ArrayList<>();
          for (ScriptPack pack: swf.getAS3Packs()) {
            String prefix=null;
            for (ABCContainerTag container:swf.getAbcList()) if (container.getABC()==pack.abc) prefix=paths.get((Tag)container);
            String id=prefix+"/script:"+pack.scriptIndex+"/traits:"+pack.traitIndices;
            boolean match=id.equals(wanted)||wanted.equals(prefix+"/script:"+pack.scriptIndex);
            for(int traitIndex:pack.traitIndices){Trait trait=pack.abc.script_info.get(pack.scriptIndex).traits.traits.get(traitIndex);if(trait instanceof TraitClass cls&&wanted.equals(prefix+"/class:"+cls.class_info))match=true;}
            if(match)matchingPacks.add(pack);
          }
          if(matchingPacks.size()>1)throw new IllegalArgumentException("Document covers multiple script packs; select an exact /traits document before editing");
          if(matchingPacks.size()==1){new FFDecAs3ScriptReplacer(false).replaceScript(matchingPacks.get(0),text,List.of());found=true;}
        }
        if (!found) throw new IllegalArgumentException("Reference document not found: "+wanted);
        ByteArrayOutputStream bytes=new ByteArrayOutputStream();
        swf.saveTo(bytes);
        byte[] candidate=bytes.toByteArray();
        new SWF(new ByteArrayInputStream(candidate),false,false,"UTF-8");
        return new JsonObject().add("compilation","passed").add("swf_reopened",true)
            .add("swf_base64",Base64.getEncoder().encodeToString(candidate)).add("behavior","not_run");
    }
    private static JsonObject handle(JsonObject request) {
        String requestId=request.getString("request_id","");
        try {
            if ("ping".equals(request.getString("operation","")))
                return new JsonObject().add("request_id",requestId).add("ok",true).add("engine","jpexs").add("version","26.3.0");
            String operation=request.getString("operation","");
            if (!operation.equals("decompile")&&!operation.equals("compile")) throw new IllegalArgumentException("Unsupported reference operation");
            byte[] bytes=Base64.getDecoder().decode(request.getString("swf_base64",""));
            long parseStart=System.nanoTime();
            SWF swf=new SWF(new ByteArrayInputStream(bytes),false,false,"UTF-8");
            double parseMs=ms(parseStart);
            JsonObject result=operation.equals("compile")?compile(swf,request):decompile(swf,request.getBoolean("include_source",false));
            return result.add("request_id",requestId).add("ok",true).add("engine","jpexs").add("version","26.3.0").add("parse_ms",parseMs);
        } catch (Exception error) {
            JsonObject response=new JsonObject().add("request_id",requestId).add("ok",false).add("engine","jpexs").add("diagnostic",error.toString());
            if (error instanceof As3ScriptReplaceException compileError) {
                JsonArray diagnostics=new JsonArray();
                for (var item:compileError.getExceptionItems()) diagnostics.add(new JsonObject().add("file",item.getFile()).add("line",item.getLine()).add("column",item.getCol()).add("message",item.getMessage()));
                response.add("diagnostics",diagnostics);
            }
            return response;
        }
    }
    public static void main(String[] args) throws Exception {
        Configuration.autoDeobfuscate.set(false);
        String player=System.getProperty("pyswf.playerglobal");
        String air=System.getProperty("pyswf.airglobal");
        if (player!=null) Configuration.playerLibLocation.set(player);
        if (air!=null) Configuration.airLibLocation.set(air);
        BufferedReader input=new BufferedReader(new InputStreamReader(System.in,StandardCharsets.UTF_8));
        // JPEXS libraries occasionally print progress. Reserve stdout for JSONL.
        PrintStream protocol=System.out;
        System.setOut(System.err);
        String line;
        while ((line=input.readLine())!=null) {
            JsonObject response;
            try { response=handle(Json.parse(line).asObject()); }
            catch (Exception error) { response=new JsonObject().add("request_id","").add("ok",false).add("diagnostic",error.toString()); }
            long serializeStart=System.nanoTime();
            String serialized=response.toString();
            response.add("serialize_ms",ms(serializeStart));
            protocol.println(response.toString()); protocol.flush();
        }
    }
}
