/* GPL-3.0. Adapter identities and writer provenance; no decompiler copied. */
import com.eclipsesource.json.*;
import com.jpexs.decompiler.flash.abc.ABC;
import com.jpexs.decompiler.flash.abc.types.traits.*;
import com.jpexs.decompiler.flash.helpers.HighlightedTextWriter;
import com.jpexs.decompiler.flash.helpers.hilight.HighlightData;
import com.jpexs.decompiler.graph.GraphSourceItem;
import com.jpexs.decompiler.flash.configuration.Configuration;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;

public final class ParityBridgeInventory {
    public static String hash(byte[] bytes) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
    }
    public static JsonArray path(String documentId) {
        JsonArray result=new JsonArray();
        String prefix=documentId.substring(4,documentId.indexOf("/actions")>=0?documentId.indexOf("/actions"):documentId.indexOf("/button")>=0?documentId.indexOf("/button"):documentId.indexOf("/clip")>=0?documentId.indexOf("/clip"):documentId.length());
        for(String part:prefix.split("/")) result.add(Integer.parseInt(part));
        return result;
    }
    /** FunctionActionItem renders inside GraphTargetItem's exact source offset. */
    public static final class OffsetWriter extends HighlightedTextWriter {
        private final Stack<GraphSourceItem> sources=new Stack<>();
        public OffsetWriter(){super(Configuration.getCodeFormatting(),true);}
        @Override public HighlightedTextWriter startOffset(GraphSourceItem src,GraphSourceItem line,int pos,HighlightData data){
            sources.push(src);return super.startOffset(src,line,pos,data);
        }
        @Override public HighlightedTextWriter endOffset(){sources.pop();return super.endOffset();}
        @Override public HighlightedTextWriter startFunction(String name){
            super.startFunction(name);
            if(!sources.empty()&&sources.peek()!=null){
                HighlightData data=new HighlightData();data.fileOffset=sources.peek().getFileOffset();
                addCurrentMethodData(data);
            }
            return this;
        }
    }
    private static final class Cursor {
        final byte[] data;int pos;
        Cursor(byte[] data){this.data=data;}
        byte[] take(int size){if(size<0||pos+size>data.length)throw new IllegalArgumentException("Truncated AVM1 container");byte[] out=Arrays.copyOfRange(data,pos,pos+size);pos+=size;return out;}
        int u8(){return take(1)[0]&255;}
        int u16(){return u8()|u8()<<8;}
        String string(){int start=pos;while(pos<data.length&&data[pos]!=0)pos++;if(pos==data.length)throw new IllegalArgumentException("Unterminated AVM1 string");String result=new String(data,start,pos-start,StandardCharsets.UTF_8);pos++;return result;}
    }
    public static final class FunctionUnit {
        public final long headerOffset,bodyOffset;
        public final byte[] code;
        public final JsonObject record;
        FunctionUnit(long header,long body,byte[] code,JsonObject record){this.headerOffset=header;this.bodyOffset=body;this.code=code;this.record=record;}
    }
    private static boolean lineage(byte[] code,boolean inherited){
        Cursor r=new Cursor(code);List<Integer> pools=new ArrayList<>();Integer branch=null;int steps=0;
        while(r.pos<code.length){if(++steps>100000)throw new IllegalArgumentException("AVM1 instruction limit");int offset=r.pos,op=r.u8();if(op==0)break;if(op<128)continue;byte[] payload=r.take(r.u16());
            if((op==0x99||op==0x9d||op==0x8a||op==0x8d)&&branch==null)branch=offset;
            if(op==0x88)pools.add(offset);
            if(op==0x9b||op==0x8e||op==0x94){if(payload.length<2)throw new IllegalArgumentException("Truncated inline body");r.take((payload[payload.length-2]&255)|((payload[payload.length-1]&255)<<8));}
            else if(op==0x8f){if(payload.length<7)throw new IllegalArgumentException("Truncated Try header");int total=0;for(int p:new int[]{1,3,5})total+=(payload[p]&255)|((payload[p+1]&255)<<8);r.take(total);}
        }
        if(!inherited)return false;
        if(branch==null)return true;
        if(pools.size()>1)return false;
        for(int offset:pools)if(offset>branch)return false;
        return true;
    }
    public static List<FunctionUnit> functions(byte[] code,long absolute,String parent,JsonArray tagPath) throws Exception{
        List<FunctionUnit> out=new ArrayList<>();walk(code,absolute,parent,tagPath,List.of(),true,0,out);return out;
    }
    private static void walk(byte[] code,long absolute,String parent,JsonArray tagPath,List<String> inherited,boolean inheritedVerified,int depth,List<FunctionUnit> out)throws Exception{
        if(depth>=64||out.size()>100000)throw new IllegalArgumentException("AVM1 function limit");
        Cursor r=new Cursor(code);List<String> pool=new ArrayList<>(inherited);boolean verified=lineage(code,inheritedVerified);int steps=0;
        while(r.pos<code.length){if(++steps>100000)throw new IllegalArgumentException("AVM1 instruction limit");int start=r.pos,op=r.u8();if(op==0)return;if(op<128)continue;Cursor h=new Cursor(r.take(r.u16()));
            if(op==0x88){int count=h.u16();pool=new ArrayList<>();for(int i=0;i<count;i++)pool.add(h.string());}
            else if(op==0x9b||op==0x8e){String name=h.string();int count=h.u16();Integer registerCount=null;int flags=0;JsonArray registers=new JsonArray(),parameters=new JsonArray();
                if(op==0x8e){registerCount=h.u8();flags=h.u16();if((flags&~0x1ff)!=0)throw new IllegalArgumentException("Reserved Function2 flags");}
                for(int i=0;i<count;i++){if(op==0x8e){int reg=h.u8();if(reg>registerCount)throw new IllegalArgumentException("Parameter register exceeds register_count");registers.add(reg);}parameters.add(h.string());}
                int size=h.u16();if(h.pos!=h.data.length)throw new IllegalArgumentException("Extra function header bytes");long bodyOffset=absolute+r.pos;byte[] body=r.take(size);String id=parent+"/function:"+Long.toHexString(bodyOffset);
                JsonArray constants=new JsonArray();for(String value:pool)constants.add(value);
                JsonObject context=new JsonObject().add("parameters",parameters).add("registers",registers).add("flags",flags).add("constants",constants).add("pool_runtime_verified",verified).add("constants_sha256",hash(constants.toString().getBytes(StandardCharsets.UTF_8)));
                context.add("register_count",registerCount==null?Json.NULL:Json.value(registerCount));
                JsonObject record=new JsonObject().add("document_id",id).add("tag_path",tagPath).add("language","as2").add("scope","function").add("kind","function").add("name",name).add("parent_document_id",parent)
                    .add("bytecode_sha256",hash(body)).add("bytecode_bytes",body.length).add("bytecode_offset",bodyOffset).add("normalized_byte_start",bodyOffset).add("byte_start",bodyOffset).add("offset_basis","decompressed_swf_including_8_byte_header").add("avm1_context",context).add("identity_verified",true).add("read_only",true).add("compiler_eligible",false).add("source_edit_reason","Nested functions require enclosing action recompilation and captured context verification");
                out.add(new FunctionUnit(absolute+start,bodyOffset,body,record));walk(body,bodyOffset,id,tagPath,pool,verified,depth+1,out);
            }else if(op==0x94){int size=h.u16();if(h.pos!=h.data.length)throw new IllegalArgumentException("Extra With header");long base=absolute+r.pos;walk(r.take(size),base,parent,tagPath,pool,verified,depth+1,out);}
            else if(op==0x8f){int flags=h.u8();int[] sizes={h.u16(),h.u16(),h.u16()};if((flags&~7)!=0||((flags&1)==0&&sizes[1]!=0)||((flags&2)==0&&sizes[2]!=0))throw new IllegalArgumentException("Try sizes conflict with flags");if((flags&4)!=0)h.u8();else h.string();if(h.pos!=h.data.length)throw new IllegalArgumentException("Extra Try header");for(int size:sizes){long base=absolute+r.pos;walk(r.take(size),base,parent,tagPath,pool,verified,depth+1,out);}}
            if((op==0x88)&&h.pos!=h.data.length)throw new IllegalArgumentException("Extra constant pool bytes");
        }
    }
    public static Set<Integer> traitMethods(Traits traits){Set<Integer> result=new TreeSet<>();for(Trait trait:traits.traits){if(trait instanceof TraitMethodGetterSetter method)result.add(method.method_info);else if(trait instanceof TraitFunction function)result.add(function.method_info);}return result;}
    public static byte[] aggregate(ABC abc,Set<Integer> methods)throws Exception{
        ByteArrayOutputStream out=new ByteArrayOutputStream();
        for(int index:methods){int bodyIndex=abc.findBodyIndex(index);if(bodyIndex<0)continue;byte[] bytes=abc.bodies.get(bodyIndex).getCodeBytes();int len=bytes.length;for(int i=0;i<4;i++)out.write((len>>>(i*8))&255);out.write(bytes);}
        return out.toByteArray();
    }
    public static boolean empty(byte[] code){return Arrays.equals(code,new byte[]{0x47})||Arrays.equals(code,new byte[]{(byte)0xd0,0x30,0x47});}
}
