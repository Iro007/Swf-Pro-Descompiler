//! Quality score for one decompiled script file.
//! Mirror of flashdec_py/flashdec/scoring.py: keep both rule sets identical.
use serde_json::{Value, json};

const HARD_MARKERS: &[&str] = &[
    "Decompilation error",
    "§§", // JPEXS unresolved stack / goto placeholders
    "// no decompilable",
    "Native reconstruction incomplete",
    "retained as P-code",
    "Fuente no reconstruida",
    "pcode_only",
    "Error during",
    "decompilation timeout",
    "//unresolved jump",
];
const SOFT_MARKERS: &[&str] = &[
    "// Unsupported",
    "/* unsupported",
    "unknown_opcode",
    "Code may be obfuscated",
];
const HARD_PENALTY: i64 = 30;
const SOFT_PENALTY: i64 = 5;
const UNBALANCED_PENALTY: i64 = 40;
const MAX_MARKER_HITS: usize = 10;

#[derive(Debug, Clone)]
pub struct Score {
    pub value: i64,
    pub hard: usize,
    pub soft: usize,
    pub balanced: bool,
    pub reasons: Vec<String>,
}

impl Score {
    pub fn to_json(&self) -> Value {
        json!({"value": self.value, "hard": self.hard, "soft": self.soft,
               "balanced": self.balanced, "reasons": self.reasons})
    }
}

fn strip_strings_and_comments(text: &str) -> Vec<char> {
    let chars: Vec<char> = text.chars().collect();
    let (mut out, mut i, n) = (Vec::with_capacity(chars.len()), 0, chars.len());
    while i < n {
        let c = chars[i];
        let next = chars.get(i + 1).copied().unwrap_or('\0');
        if c == '/' && next == '/' {
            while i < n && chars[i] != '\n' {
                i += 1;
            }
        } else if c == '/' && next == '*' {
            i += 2;
            while i < n && !(chars[i] == '*' && chars.get(i + 1) == Some(&'/')) {
                i += 1;
            }
            i = (i + 2).min(n);
        } else if c == '"' || c == '\'' {
            i += 1;
            while i < n && chars[i] != c {
                i += if chars[i] == '\\' { 2 } else { 1 };
            }
            i += 1;
        } else {
            out.push(c);
            i += 1;
        }
    }
    out
}

pub fn balanced(text: &str) -> bool {
    let mut stack = vec![];
    for c in strip_strings_and_comments(text) {
        match c {
            '(' | '[' | '{' => stack.push(c),
            ')' | ']' | '}' => {
                let open = match c {
                    ')' => '(',
                    ']' => '[',
                    _ => '{',
                };
                if stack.pop() != Some(open) {
                    return false;
                }
            }
            _ => {}
        }
    }
    stack.is_empty()
}

/// None means the engine produced nothing for this path.
pub fn score(text: Option<&str>) -> Option<Score> {
    let text = text?;
    if text.trim().is_empty() {
        return None;
    }
    let mut result = Score { value: 100, hard: 0, soft: 0, balanced: true, reasons: vec![] };
    for marker in HARD_MARKERS {
        let hits = text.matches(marker).count().min(MAX_MARKER_HITS);
        if hits > 0 {
            result.hard += hits;
            result.reasons.push(format!("'{marker}' x{hits}"));
        }
    }
    for marker in SOFT_MARKERS {
        let hits = text.matches(marker).count().min(MAX_MARKER_HITS);
        if hits > 0 {
            result.soft += hits;
            result.reasons.push(format!("'{marker}' x{hits}"));
        }
    }
    result.balanced = balanced(text);
    if !result.balanced {
        result.reasons.push("unbalanced brackets".into());
        result.value -= UNBALANCED_PENALTY;
    }
    result.value -= result.hard as i64 * HARD_PENALTY + result.soft as i64 * SOFT_PENALTY;
    Some(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn same_rules_as_python() {
        assert_eq!(score(Some("var a = 1;")).unwrap().value, 100);
        assert!(score(None).is_none() && score(Some("  \n")).is_none());
        assert_eq!(score(Some("§§push(1);")).unwrap().value, 70);
        assert_eq!(
            score(Some("// Incomplete function f(); original method:1 retained as P-code")).unwrap().value,
            70
        );
        assert_eq!(score(Some("function f() {")).unwrap().value, 60);
        assert!(balanced("trace(\"}\"); // {\n/* ( */ var x = [1];"));
        assert!(!balanced("if (a) { b(;"));
    }
}
