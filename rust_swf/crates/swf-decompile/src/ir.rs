//! Side-effect preserving IR and structured AST. Values refer to eager temporaries.
use crate::{Result, fail};

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct PropertyName {
    pub expression: String,
    pub qualified: bool,
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) enum Value {
    Expr(String),
    Literal(String),
    Int32(String),
    LexicalReceiver(PropertyName),
    BoundReceiver {
        receiver: Option<String>,
        property: PropertyName,
    },
    Activation {
        object: String,
        body_index: usize,
    },
    CatchScope {
        variable: String,
        exception_index: usize,
    },
    PublicNamespace(String),
}
impl Value {
    pub fn text(&self) -> Result<&str> {
        match self {
            Self::Expr(s) | Self::Literal(s) | Self::Int32(s) => Ok(s),
            Self::Activation { .. } => Err(fail(
                "UNSUPPORTED_ACTIVATION_ESCAPE",
                "Activation object is only valid for proven slots, scopes and closure capture",
                0,
            )),
            Self::CatchScope { .. } => Err(fail(
                "UNSUPPORTED_CATCH_ESCAPE",
                "Catch scope object is only valid for its proven exception slot",
                0,
            )),
            Self::LexicalReceiver(_) | Self::BoundReceiver { .. } => Err(fail(
                "UNPROVEN_RECEIVER",
                "A lexical lookup receiver escaped its matching property operation",
                0,
            )),
            Self::PublicNamespace(_) => Err(fail(
                "UNPROVEN_NAMESPACE_VALUE",
                "A namespace constant escaped its qualified property lookup",
                0,
            )),
        }
    }
    pub fn literal_int(&self) -> Option<usize> {
        match self {
            Self::Literal(s) | Self::Int32(s) => s.parse().ok(),
            _ => None,
        }
    }
    pub fn literal_string(&self) -> Option<String> {
        match self {
            Self::Literal(s) => serde_json::from_str::<String>(s).ok(),
            _ => None,
        }
    }
}

#[derive(Clone, Debug)]
pub(crate) enum Node {
    Line(String),
    If {
        condition: String,
        body: Vec<Node>,
        other: Vec<Node>,
    },
    While {
        condition: String,
        body: Vec<Node>,
    },
    ForIn {
        key: String,
        object: String,
        body: Vec<Node>,
    },
    ForEach {
        value: String,
        object: String,
        body: Vec<Node>,
    },
    Switch {
        expression: String,
        arms: Vec<(Vec<Option<usize>>, Vec<Node>)>,
    },
    Function {
        name: String,
        parameters: Vec<String>,
        body: Vec<Node>,
    },
    TypedFunction {
        name: String,
        parameters: Vec<String>,
        return_type: String,
        body: Vec<Node>,
    },
    TryCatch {
        body: Vec<Node>,
        variable: String,
        exception_type: String,
        handler: Vec<Node>,
    },
    /// A function whose calling-convention bindings must run before its body.
    /// AVM1 Function2 uses this for register-backed parameters and preloaded
    /// values; keeping the prologue distinct prevents ordinary body lifting
    /// from moving those assignments across user actions.
    FunctionWithPrologue {
        name: String,
        parameters: Vec<String>,
        prologue: Vec<Node>,
        body: Vec<Node>,
    },
}

pub(crate) fn emit(nodes: &[Node], depth: usize, output: &mut String) {
    let indent = "    ".repeat(depth);
    for node in nodes {
        match node {
            Node::Line(line) => {
                output.push_str(&indent);
                output.push_str(line);
                output.push('\n');
            }
            Node::If {
                condition,
                body,
                other,
            } => {
                output.push_str(&format!("{indent}if ({condition}) {{\n"));
                emit(body, depth + 1, output);
                output.push_str(&format!("{indent}}}"));
                if !other.is_empty() {
                    output.push_str(" else {\n");
                    emit(other, depth + 1, output);
                    output.push_str(&format!("{indent}}}"));
                }
                output.push('\n');
            }
            Node::While { condition, body } => {
                output.push_str(&format!("{indent}while ({condition}) {{\n"));
                emit(body, depth + 1, output);
                output.push_str(&format!("{indent}}}\n"));
            }
            Node::ForIn { key, object, body } => {
                output.push_str(&format!("{indent}for (var {key} in {object}) {{\n"));
                emit(body, depth + 1, output);
                output.push_str(&format!("{indent}}}\n"));
            }
            Node::ForEach {
                value,
                object,
                body,
            } => {
                output.push_str(&format!("{indent}for each (var {value} in {object}) {{\n"));
                emit(body, depth + 1, output);
                output.push_str(&format!("{indent}}}\n"));
            }
            Node::Switch { expression, arms } => {
                output.push_str(&format!("{indent}switch ({expression}) {{\n"));
                for (labels, body) in arms {
                    for label in labels {
                        match label {
                            Some(index) => output.push_str(&format!("{indent}    case {index}:\n")),
                            None => output.push_str(&format!("{indent}    default:\n")),
                        }
                    }
                    emit(body, depth + 2, output);
                }
                output.push_str(&format!("{indent}}}\n"));
            }
            Node::Function {
                name,
                parameters,
                body,
            } => emit_function(name, parameters, &[], body, depth, output),
            Node::TypedFunction {
                name,
                parameters,
                return_type,
                body,
            } => {
                output.push_str(&format!(
                    "{indent}function {name}({}):{return_type} {{\n",
                    parameters.join(", ")
                ));
                emit(body, depth + 1, output);
                output.push_str(&format!("{indent}}}\n"));
            }
            Node::TryCatch {
                body,
                variable,
                exception_type,
                handler,
            } => {
                output.push_str(&format!("{indent}try {{\n"));
                emit(body, depth + 1, output);
                output.push_str(&format!(
                    "{indent}}} catch ({variable}:{exception_type}) {{\n"
                ));
                emit(handler, depth + 1, output);
                output.push_str(&format!("{indent}}}\n"));
            }
            Node::FunctionWithPrologue {
                name,
                parameters,
                prologue,
                body,
            } => emit_function(name, parameters, prologue, body, depth, output),
        }
    }
}

fn emit_function(
    name: &str,
    parameters: &[String],
    prologue: &[Node],
    body: &[Node],
    depth: usize,
    output: &mut String,
) {
    let indent = "    ".repeat(depth);
    output.push_str(&format!(
        "{indent}function {name}({}) {{\n",
        parameters.join(", ")
    ));
    emit(prologue, depth + 1, output);
    emit(body, depth + 1, output);
    output.push_str(&format!("{indent}}}\n"));
}

pub(crate) fn identifier(s: &str) -> bool {
    let mut chars = s.chars();
    let Some(first) = chars.next() else {
        return false;
    };
    if !(first.is_ascii_alphabetic() || first == '_' || first == '$') {
        return false;
    }
    if !chars.all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '$') {
        return false;
    }
    !matches!(
        s,
        "this"
            | "super"
            | "class"
            | "function"
            | "var"
            | "const"
            | "return"
            | "new"
            | "if"
            | "else"
            | "while"
            | "for"
            | "true"
            | "false"
            | "null"
            | "undefined"
            | "throw"
            | "try"
            | "catch"
            | "finally"
            | "switch"
            | "case"
            | "default"
            | "break"
            | "continue"
            | "with"
            | "delete"
            | "typeof"
            | "in"
            | "instanceof"
            | "is"
            | "as"
            | "void"
            | "package"
            | "import"
            | "public"
            | "private"
            | "protected"
            | "internal"
            | "namespace"
            | "use"
            | "extends"
            | "implements"
            | "interface"
    )
}

pub(crate) fn quoted(text: &str) -> String {
    serde_json::to_string(text)
        .expect("String serialization is infallible")
        .replace('\u{2028}', "\\u2028")
        .replace('\u{2029}', "\\u2029")
}

pub(crate) fn member(receiver: &str, property: &str) -> String {
    if identifier(property) {
        format!("({receiver}).{property}")
    } else {
        format!("({receiver})[{}]", quoted(property))
    }
}
