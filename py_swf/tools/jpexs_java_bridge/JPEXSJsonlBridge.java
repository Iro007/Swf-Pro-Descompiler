/*
 * Minimal persistent JSONL adapter for the JPEXS ffdec_lib API.
 *
 * This source is compiled separately against the pinned JPEXS library. It is
 * intentionally dependency-free: the bridge only needs the JDK and the
 * ffdec_lib classpath, while Python owns transport, cancellation and API
 * validation.
 */

import com.jpexs.decompiler.flash.AbortRetryIgnoreHandler;
import com.jpexs.decompiler.flash.SWF;
import com.jpexs.decompiler.flash.exporters.modes.ScriptExportMode;
import com.jpexs.decompiler.flash.exporters.settings.ScriptExportSettings;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.ArrayList;
import java.util.Base64;
import java.util.Comparator;
import java.util.List;
import java.util.stream.Collectors;

public final class JPEXSJsonlBridge {
    private JPEXSJsonlBridge() {}

    private static String field(String json, String name) {
        String needle = "\"" + name + "\"";
        int start = json.indexOf(needle);
        if (start < 0) return "";
        int colon = json.indexOf(':', start + needle.length());
        if (colon < 0) return "";
        int quote = json.indexOf('"', colon + 1);
        if (quote < 0) return "";
        StringBuilder out = new StringBuilder();
        boolean escaped = false;
        for (int i = quote + 1; i < json.length(); i++) {
            char c = json.charAt(i);
            if (escaped) {
                switch (c) {
                    case 'n': out.append('\n'); break;
                    case 'r': out.append('\r'); break;
                    case 't': out.append('\t'); break;
                    default: out.append(c); break;
                }
                escaped = false;
            } else if (c == '\\') {
                escaped = true;
            } else if (c == '"') {
                return out.toString();
            } else {
                out.append(c);
            }
        }
        return "";
    }

    private static String json(String value) {
        if (value == null) return "null";
        StringBuilder out = new StringBuilder("\"");
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            switch (c) {
                case '\\': out.append("\\\\"); break;
                case '"': out.append("\\\""); break;
                case '\n': out.append("\\n"); break;
                case '\r': out.append("\\r"); break;
                case '\t': out.append("\\t"); break;
                default:
                    if (c < 0x20) out.append(String.format("\\u%04x", (int) c));
                    else out.append(c);
            }
        }
        return out.append('"').toString();
    }

    private static String response(String requestId, boolean ok, String kind, String error, List<Path> files, Path root) throws IOException {
        StringBuilder out = new StringBuilder("{");
        out.append("\"request_id\":").append(json(requestId));
        out.append(",\"ok\":").append(ok ? "true" : "false");
        if (kind != null) out.append(",\"kind\":").append(json(kind));
        if (error != null) out.append(",\"error\":").append(json(error));
        out.append(",\"sections\":[");
        for (int i = 0; i < files.size(); i++) {
            if (i > 0) out.append(',');
            Path file = files.get(i);
            String source = Files.readString(file, StandardCharsets.UTF_8);
            out.append("{\"name\":").append(json(root.relativize(file).toString().replace('\\', '/')));
            out.append(",\"source\":").append(json(source)).append(",\"error\":null}");
        }
        return out.append("]}").toString();
    }

    private static void deleteTree(Path root) {
        try {
            if (root != null && Files.exists(root)) {
                Files.walk(root).sorted(Comparator.reverseOrder()).forEach(path -> {
                    try { Files.deleteIfExists(path); } catch (IOException ignored) { }
                });
            }
        } catch (IOException ignored) { }
    }

    private static String handle(String request) {
        String requestId = field(request, "request_id");
        if (!"decompile".equals(field(request, "operation"))) {
            return "{\"request_id\":" + json(requestId) + ",\"ok\":false,\"error\":\"Unsupported operation\",\"sections\":[]}";
        }
        Path root = null;
        try {
            byte[] swfBytes = Base64.getDecoder().decode(field(request, "swf_base64"));
            root = Files.createTempDirectory("pyswf-jpexs-java-");
            Path out = Files.createDirectory(root.resolve("out"));
            SWF swf = new SWF(new ByteArrayInputStream(swfBytes), false, false, "UTF-8");
            ScriptExportSettings settings = new ScriptExportSettings(ScriptExportMode.AS, false, false, false, false);
            AbortRetryIgnoreHandler handler = new AbortRetryIgnoreHandler() {
                @Override public int handle(Throwable thrown) { return IGNORE; }
                @Override public AbortRetryIgnoreHandler getNewInstance() { return this; }
            };
            swf.exportActionScript(handler, out.toString(), settings, false, null);
            List<Path> files = Files.walk(out)
                    .filter(path -> path.toString().endsWith(".as") || path.toString().endsWith(".pcode"))
                    .sorted()
                    .collect(Collectors.toList());
            if (files.isEmpty()) return "{\"request_id\":" + json(requestId) + ",\"ok\":false,\"error\":\"JPEXS produced no script files\",\"sections\":[]}";
            String kind = "82".equals(field(request, "tag_type")) ? "avm2" : "avm1";
            return response(requestId, true, kind, null, files, out);
        } catch (Throwable error) {
            return "{\"request_id\":" + json(requestId) + ",\"ok\":false,\"error\":" + json(error.toString()) + ",\"sections\":[]}";
        } finally {
            deleteTree(root);
        }
    }

    public static void main(String[] args) throws Exception {
        java.io.BufferedReader reader = new java.io.BufferedReader(new java.io.InputStreamReader(System.in, StandardCharsets.UTF_8));
        String line;
        while ((line = reader.readLine()) != null) {
            if (!line.trim().isEmpty()) {
                System.out.println(handle(line));
                System.out.flush();
            }
        }
    }
}
