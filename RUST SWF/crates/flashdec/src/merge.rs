//! Choose the best engine output for every script path (mirror of merge.py).
use std::collections::BTreeMap;

use serde_json::{Value, json};

use crate::native::NativeScript;
use crate::scoring::{Score, score};

pub struct Choice {
    pub path: String,
    pub engine: &'static str,
    pub text: String,
    pub scores: BTreeMap<&'static str, Option<Score>>,
    pub native_path: Option<String>,
    pub only_native: bool,
    pub units: Vec<Value>,
}

impl Choice {
    pub fn to_json(&self) -> Value {
        let scores: serde_json::Map<String, Value> = self
            .scores
            .iter()
            .map(|(k, v)| (k.to_string(), v.as_ref().map_or(Value::Null, Score::to_json)))
            .collect();
        json!({"path": self.path, "engine": self.engine, "native_path": self.native_path,
               "only_native": self.only_native, "units": self.units, "scores": scores})
    }
    pub fn chosen_value(&self) -> Option<i64> {
        self.scores.get(self.engine)?.as_ref().map(|s| s.value)
    }
}

fn parent(path: &str) -> &str {
    path.rsplit_once('/').map_or("", |(dir, _)| dir)
}

pub fn merge(native: &[NativeScript], jpexs: &BTreeMap<String, String>, prefer: &[&'static str]) -> Vec<Choice> {
    let native_paths: std::collections::HashSet<&str> = native.iter().map(|s| s.path.as_str()).collect();
    let mut jpexs_left: Vec<&String> = jpexs.keys().filter(|p| !native_paths.contains(p.as_str())).collect();
    let mut pairs: Vec<(String, Option<&NativeScript>, Option<&String>)> = vec![];
    for script in native {
        if let Some((key, _)) = jpexs.get_key_value(&script.path) {
            pairs.push((script.path.clone(), Some(script), Some(key)));
            continue;
        }
        // Event handlers: JPEXS names them from flags we do not reproduce. Pair
        // the leftovers that live in the same container directory, in tag order.
        let mut matched = None;
        if script.pairable {
            let folder = parent(&script.path);
            matched = jpexs_left.iter().position(|candidate| {
                let dir = parent(candidate);
                dir == folder || dir.starts_with(&format!("{folder}_")) || parent(dir) == folder
            });
        }
        match matched {
            Some(index) => {
                let key = jpexs_left.remove(index);
                pairs.push((key.clone(), Some(script), Some(key)));
            }
            None => pairs.push((script.path.clone(), Some(script), None)),
        }
    }
    for key in jpexs_left {
        pairs.push((key.clone(), None, Some(key)));
    }
    pairs.sort_by_key(|(path, _, _)| path.to_lowercase());

    pairs
        .into_iter()
        .map(|(path, native_script, jpexs_key)| {
            let mut candidates: Vec<(&'static str, &str)> = vec![];
            if let Some(script) = native_script {
                candidates.push(("native", script.source.as_str()));
            }
            if let Some(key) = jpexs_key {
                candidates.push(("jpexs", jpexs[key].as_str()));
            }
            let scores: BTreeMap<&'static str, Option<Score>> =
                candidates.iter().map(|(engine, text)| (*engine, score(Some(text)))).collect();
            let rank = |engine: &str| prefer.iter().position(|p| *p == engine).unwrap_or(99);
            let best = candidates
                .iter()
                .filter(|(engine, _)| scores[engine].is_some())
                .min_by_key(|(engine, _)| (-scores[engine].as_ref().unwrap().value, rank(engine)))
                .or_else(|| candidates.iter().min_by_key(|(engine, _)| rank(engine)))
                .copied()
                .expect("every pair has at least one engine");
            Choice {
                path,
                engine: best.0,
                text: best.1.to_owned(),
                scores,
                native_path: native_script.map(|s| s.path.clone()),
                only_native: native_script.is_some() && jpexs_key.is_none(),
                units: native_script.map(|s| s.units.clone()).unwrap_or_default(),
            }
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    fn script(path: &str, source: &str, pairable: bool) -> NativeScript {
        NativeScript { path: path.into(), source: source.into(), pcode: String::new(), pairable, units: vec![] }
    }
    #[test]
    fn better_score_then_preference() {
        let native = vec![script("Main.as", "class Main {}", false), script("A.as", "class A {}", false)];
        let jpexs: BTreeMap<String, String> = [
            ("Main.as", "class Main { §§goto(1); }"),
            ("A.as", "class A {}"),
            ("B.as", "class B {}"),
        ]
        .into_iter()
        .map(|(a, b)| (a.to_owned(), b.to_owned()))
        .collect();
        let choices = merge(&native, &jpexs, &["jpexs", "native"]);
        let get = |p: &str| choices.iter().find(|c| c.path == p).unwrap();
        assert_eq!(get("Main.as").engine, "native");
        assert_eq!(get("A.as").engine, "jpexs");
        assert!(get("B.as").engine == "jpexs" && !get("B.as").only_native);
    }
    #[test]
    fn pairs_event_handlers_by_container() {
        let native = vec![script("DefineButton2_3/BUTTONCONDACTION_0.as", "on(release){}", true)];
        let jpexs: BTreeMap<String, String> =
            [("DefineButton2_3/on(release).as".to_owned(), "on(release){\n}".to_owned())].into();
        let choices = merge(&native, &jpexs, &["jpexs", "native"]);
        assert_eq!(choices.len(), 1);
        assert_eq!(choices[0].path, "DefineButton2_3/on(release).as");
    }
}
