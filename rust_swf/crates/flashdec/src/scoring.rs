//! Quality score for one decompiled script file.
//! Rules come from shared/scoring-rules.json, which flashdec_py also reads;
//! shared/scoring-vectors.json holds the golden cases both test suites check.
use serde_json::{Value, json};
use std::sync::OnceLock;

const RULES_JSON: &str = include_str!("../../../../shared/scoring-rules.json");

struct Rules {
    base_score: i64,
    hard_markers: Vec<String>,
    soft_markers: Vec<String>,
    hard_penalty: i64,
    soft_penalty: i64,
    unbalanced_penalty: i64,
    max_marker_hits: usize,
}

fn rules() -> &'static Rules {
    static RULES: OnceLock<Rules> = OnceLock::new();
    RULES.get_or_init(|| {
        let v: Value = serde_json::from_str(RULES_JSON).expect("shared/scoring-rules.json is valid JSON");
        let int = |key: &str| v[key].as_i64().unwrap_or_else(|| panic!("scoring rule {key} must be an integer"));
        let list = |key: &str| -> Vec<String> {
            v[key]
                .as_array()
                .unwrap_or_else(|| panic!("scoring rule {key} must be a list"))
                .iter()
                .map(|m| m.as_str().expect("markers are strings").to_owned())
                .collect()
        };
        Rules {
            base_score: int("base_score"),
            hard_markers: list("hard_markers"),
            soft_markers: list("soft_markers"),
            hard_penalty: int("hard_penalty"),
            soft_penalty: int("soft_penalty"),
            unbalanced_penalty: int("unbalanced_penalty"),
            max_marker_hits: int("max_marker_hits") as usize,
        }
    })
}

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
    let rules = rules();
    let mut result = Score { value: rules.base_score, hard: 0, soft: 0, balanced: true, reasons: vec![] };
    for marker in &rules.hard_markers {
        let hits = text.matches(marker.as_str()).count().min(rules.max_marker_hits);
        if hits > 0 {
            result.hard += hits;
            result.reasons.push(format!("'{marker}' x{hits}"));
        }
    }
    for marker in &rules.soft_markers {
        let hits = text.matches(marker.as_str()).count().min(rules.max_marker_hits);
        if hits > 0 {
            result.soft += hits;
            result.reasons.push(format!("'{marker}' x{hits}"));
        }
    }
    result.balanced = balanced(text);
    if !result.balanced {
        result.reasons.push("unbalanced brackets".into());
        result.value -= rules.unbalanced_penalty;
    }
    result.value -= result.hard as i64 * rules.hard_penalty + result.soft as i64 * rules.soft_penalty;
    Some(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    /// Same golden cases as flashdec_py/tests/test_flashdec.py.
    #[test]
    fn shared_scoring_vectors() {
        let vectors: Value =
            serde_json::from_str(include_str!("../../../../shared/scoring-vectors.json")).unwrap();
        let cases = vectors["cases"].as_array().unwrap();
        assert!(!cases.is_empty());
        for case in cases {
            let text = case["text"].as_str();
            let got = score(text).map(|s| s.value);
            assert_eq!(got, case["score"].as_i64(), "score for {text:?}");
            if let (Some(text), Some(expected)) = (text, case["balanced"].as_bool()) {
                assert_eq!(balanced(text), expected, "balanced for {text:?}");
            }
        }
    }
}
