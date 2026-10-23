//! JSONL framing of `rustswf worker` (engines/PROTOCOL.md).
//!
//! Protocol v2 is `{id, op, args}` -> `{id, ok:true, result}` | `{id, ok:false,
//! error:{message, kind}}`. The legacy framing used by the previous desktop host and the
//! benchmark adapters (`{request_id, operation, args}` -> `{request_id, result}`
//! | `{request_id, error:"text"}`) is still answered in kind, so nothing that
//! speaks it has to change.
use serde_json::{Value, json};
use std::fmt;

/// Kinds a worker reports in `error.kind`. The harness adds its own
/// (`timeout`, `crashed`, ...); see engines/protocol.json.
pub mod kind {
    pub const BAD_REQUEST: &str = "bad_request";
    pub const NOT_OPEN: &str = "not_open";
    pub const NOT_FOUND: &str = "not_found";
    pub const UNSUPPORTED: &str = "unsupported";
    pub const FAILED: &str = "failed";
    pub const LIMIT: &str = "limit";
}

/// An error that carries its protocol kind. Plain `anyhow` errors are `failed`.
#[derive(Debug)]
pub struct OpError {
    pub kind: &'static str,
    pub message: String,
}
impl fmt::Display for OpError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}
impl std::error::Error for OpError {}

pub fn op_error(kind: &'static str, message: impl Into<String>) -> anyhow::Error {
    OpError {
        kind,
        message: message.into(),
    }
    .into()
}
pub fn bad_request(message: impl Into<String>) -> anyhow::Error {
    op_error(kind::BAD_REQUEST, message)
}
pub fn not_found(message: impl Into<String>) -> anyhow::Error {
    op_error(kind::NOT_FOUND, message)
}
pub fn not_open() -> anyhow::Error {
    op_error(kind::NOT_OPEN, "No hay SWF abierto")
}
pub fn unsupported(op: &str) -> anyhow::Error {
    op_error(kind::UNSUPPORTED, format!("Operación no soportada: {op}"))
}

pub fn error_kind(error: &anyhow::Error) -> &'static str {
    error
        .downcast_ref::<OpError>()
        .map_or(kind::FAILED, |e| e.kind)
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Framing {
    V2,
    Legacy,
}

#[derive(Debug)]
pub struct Request {
    pub id: Value,
    pub op: String,
    pub args: Value,
    pub framing: Framing,
}

/// A line that is JSON but not a usable request still gets an answer in the
/// framing it appears to use.
pub fn parse(line: &str) -> std::result::Result<Request, (Framing, Value, String)> {
    let value: Value = serde_json::from_str(line)
        .map_err(|e| (Framing::V2, Value::Null, format!("JSON inválido: {e}")))?;
    let Some(object) = value.as_object() else {
        return Err((Framing::V2, Value::Null, "La solicitud debe ser un objeto".into()));
    };
    let framing = if object.contains_key("op") || object.contains_key("id") {
        Framing::V2
    } else if object.contains_key("operation") || object.contains_key("request_id") {
        Framing::Legacy
    } else {
        Framing::V2
    };
    let id = match framing {
        Framing::V2 => object.get("id"),
        Framing::Legacy => object.get("request_id"),
    }
    .cloned()
    .unwrap_or(Value::Null);
    let op = match framing {
        Framing::V2 => object.get("op"),
        Framing::Legacy => object.get("operation"),
    }
    .and_then(Value::as_str);
    let Some(op) = op else {
        return Err((framing, id, "Falta operación".into()));
    };
    let args = object.get("args").cloned().unwrap_or_else(|| json!({}));
    if !args.is_object() {
        return Err((framing, id, "args debe ser un objeto".into()));
    }
    if let Some(version) = object.get("protocol_version").and_then(Value::as_u64)
        && !matches!(version, 1 | 2)
    {
        return Err((framing, id, format!("protocol_version {version} no compatible")));
    }
    Ok(Request {
        id,
        op: op.to_owned(),
        args,
        framing,
    })
}

pub fn reply_ok(request: &Request, result: Value, elapsed_ms: u128) -> Value {
    match request.framing {
        Framing::V2 => json!({"id":request.id,"ok":true,"protocol_version":2,"result":result,"elapsed_ms":elapsed_ms}),
        Framing::Legacy => json!({"request_id":request.id,"result":result,"elapsed_ms":elapsed_ms}),
    }
}

pub fn reply_error(
    framing: Framing,
    id: &Value,
    kind: &str,
    message: &str,
    elapsed_ms: u128,
) -> Value {
    match framing {
        Framing::V2 => json!({"id":id,"ok":false,"protocol_version":2,"error":{"message":message,"kind":kind},"elapsed_ms":elapsed_ms}),
        Framing::Legacy => json!({"request_id":id,"error":message,"elapsed_ms":elapsed_ms}),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn v2_and_legacy_requests_are_recognized_and_answered_in_kind() {
        let v2 = parse(r#"{"id":7,"op":"header","args":{}}"#).unwrap();
        assert_eq!((v2.framing, v2.op.as_str(), v2.id.clone()), (Framing::V2, "header", json!(7)));
        let ok = reply_ok(&v2, json!({"a":1}), 3);
        assert_eq!(ok["ok"], true);
        assert_eq!(ok["id"], 7);
        assert_eq!(ok["result"]["a"], 1);

        let legacy = parse(r#"{"request_id":"r","operation":"info","args":{"x":1}}"#).unwrap();
        assert_eq!(legacy.framing, Framing::Legacy);
        let ok = reply_ok(&legacy, json!(1), 0);
        assert!(ok.get("ok").is_none());
        assert_eq!(ok["request_id"], "r");
        let err = reply_error(Framing::Legacy, &legacy.id, kind::FAILED, "boom", 0);
        assert_eq!(err["error"], "boom");
        let err = reply_error(Framing::V2, &v2.id, kind::NOT_FOUND, "nope", 0);
        assert_eq!(err["ok"], false);
        assert_eq!(err["error"]["kind"], "not_found");
        assert_eq!(err["error"]["message"], "nope");
    }

    #[test]
    fn malformed_requests_are_rejected_with_the_framing_they_look_like() {
        assert!(parse("not json").is_err());
        assert!(parse("[1]").is_err());
        let (framing, id, _) = parse(r#"{"request_id":4,"args":{}}"#).unwrap_err();
        assert_eq!((framing, id), (Framing::Legacy, json!(4)));
        let (framing, _, message) = parse(r#"{"id":1,"op":"x","args":[]}"#).unwrap_err();
        assert_eq!(framing, Framing::V2);
        assert!(message.contains("args"));
        assert!(parse(r#"{"id":1,"op":"x","protocol_version":9}"#).is_err());
        assert!(parse(r#"{"id":1,"op":"x","protocol_version":1}"#).is_ok());
    }

    #[test]
    fn kinds_survive_anyhow_context() {
        let error = not_found("x");
        assert_eq!(error_kind(&error), kind::NOT_FOUND);
        assert_eq!(error_kind(&anyhow::anyhow!("plain")), kind::FAILED);
        assert_eq!(error_kind(&unsupported("zzz")), kind::UNSUPPORTED);
    }
}
