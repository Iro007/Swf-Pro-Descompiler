//! Class-level unit ids (`tag:<path>/class:<n>`) for AS3.
//!
//! `scripts` lists method bodies; the class tree of `tag_tree` points at whole
//! classes, so `decompile` and `disassemble` also accept a class id: the source
//! is the class document, the P-code is every method the class declares.
use swf_avm::Abc;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ClassRef {
    pub tag_path: Vec<usize>,
    pub index: usize,
}

pub fn class_id(tag_path: &[usize], index: usize) -> String {
    format!(
        "tag:{}/class:{index}",
        tag_path
            .iter()
            .map(usize::to_string)
            .collect::<Vec<_>>()
            .join("/")
    )
}

pub fn parse(id: &str) -> Option<ClassRef> {
    let (path, index) = id.strip_prefix("tag:")?.rsplit_once("/class:")?;
    let tag_path = path
        .split('/')
        .map(str::parse::<usize>)
        .collect::<Result<Vec<_>, _>>()
        .ok()?;
    if tag_path.is_empty() {
        return None;
    }
    Some(ClassRef {
        tag_path,
        index: index.parse().ok()?,
    })
}

/// Methods a class declares, in reading order: static initializer, constructor,
/// instance methods/accessors, static methods/accessors. Duplicates removed.
pub fn method_indices(abc: &Abc, class: usize) -> Vec<usize> {
    let mut methods = vec![];
    let mut push = |m: usize| {
        if !methods.contains(&m) {
            methods.push(m);
        }
    };
    if let Some(c) = abc.classes.get(class) {
        push(c.init as usize);
    }
    if let Some(i) = abc.instances.get(class) {
        push(i.init as usize);
        for t in &i.traits {
            if let Some(m) = t.method {
                push(m as usize);
            }
        }
    }
    if let Some(c) = abc.classes.get(class) {
        for t in &c.traits {
            if let Some(m) = t.method {
                push(m as usize);
            }
        }
    }
    methods
}

/// File-private classes live in compiler-made namespaces named
/// `<file>.as$<n>`; the tree shows them under the file, not under a package
/// called `as$182`.
pub fn clean_package(raw: &str) -> String {
    if let Some(at) = raw.rfind("as$")
        && (at == 0 || raw.as_bytes()[at - 1] == b'.')
        && !raw[at + 3..].is_empty()
        && raw[at + 3..].bytes().all(|b| b.is_ascii_digit())
    {
        return raw[..at].trim_end_matches('.').to_owned();
    }
    raw.to_owned()
}

/// Package and class name of an instance/class multiname.
pub fn split_name(abc: &Abc, multiname: u32) -> (String, String) {
    if let Some(m) = abc.multinames.get(multiname as usize)
        && matches!(m.kind, 0x07 | 0x0d)
    {
        let package = abc
            .namespaces
            .get(m.namespace as usize)
            .and_then(|n| abc.string(n.name))
            .unwrap_or("");
        return (
            clean_package(package),
            abc.string(m.name).unwrap_or("?").to_owned(),
        );
    }
    let shown = abc.multiname_display(multiname);
    match shown.rsplit_once("::") {
        Some((package, name)) => (clean_package(package), name.to_owned()),
        None => (String::new(), shown),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn file_private_namespaces_collapse_to_the_file() {
        assert_eq!(clean_package("TestChainedAssignments2.as$182"), "TestChainedAssignments2");
        assert_eq!(clean_package("com.example.Foo.as$7"), "com.example.Foo");
        assert_eq!(clean_package("as$12"), "");
        assert_eq!(clean_package("com.example"), "com.example");
        assert_eq!(clean_package("alias$12"), "alias$12");
        assert_eq!(clean_package("pkg.as$"), "pkg.as$");
        assert_eq!(clean_package(""), "");
    }

    #[test]
    fn class_ids_round_trip() {
        assert_eq!(class_id(&[6], 0), "tag:6/class:0");
        assert_eq!(class_id(&[2, 5], 3), "tag:2/5/class:3");
        let parsed = parse("tag:2/5/class:3").unwrap();
        assert_eq!((parsed.tag_path, parsed.index), (vec![2, 5], 3));
        assert!(parse("tag:6/method:3").is_none());
        assert!(parse("tag:x/class:1").is_none());
        assert!(parse("tag:6/class:y").is_none());
        assert!(parse("class:1").is_none());
    }
}
