// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
//! Rally discovery disk cache (`discovery_bridge.py` `_disk_cache_*`).
//!
//! A record is reused only when it names a healthy native Rally envelope for
//! the same workdir, binary (path, mtime, size), uid and identity
//! environment. The key is SHA-256 over Python's
//! `json.dumps(material, sort_keys=True)`, reproduced here byte for byte.

use crate::identity::hex;
use crate::py;
use crate::{Fallback, R};
use serde_json::Value;
use sha2::{Digest, Sha256};

pub const SCHEMA: &str = "build-loop.rally-discovery-cache.v1";
/// Fresh window (`CACHE_TTL_SECONDS`).
pub const TTL_SECONDS: f64 = 60.0;
/// Stale records up to this age are used while a refresh runs.
pub const STALE_MAX_SECONDS: f64 = 24.0 * 60.0 * 60.0;
/// A refresh lock younger than this means a refresher is already running.
pub const REFRESH_LOCK_SECONDS: f64 = 30.0;
/// Environment that scopes a record to one identity (`_DISK_CACHE_IDENTITY_ENV`).
pub const IDENTITY_ENV: [&str; 9] = [
    "RALLY_SESSION_ID",
    "RALLY_AGENT_ID",
    "RALLY_TOOL_ID",
    "RALLY_OBSERVER_PID",
    "RALLY_HOME",
    "BUILD_LOOP_RALLY_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CLAUDE_CODE_SESSION_ID",
    "CODEX_SESSION_ID",
];
/// Usage fragments a `rally` binary must print (`REQUIRED_RALLY_HELP_FRAGMENTS`).
pub const RALLY_HELP_FRAGMENTS: [&str; 3] = ["rally enter --tool", "rally say <kind>", "rally whoami"];

pub struct KeyInput<'a> {
    pub workdir: &'a str,
    pub binary: &'a str,
    pub binary_mtime_ns: i128,
    pub binary_size: u64,
    pub uid: u32,
    /// Values for every name in [`IDENTITY_ENV`], `None` when unset.
    pub env: Vec<(&'a str, Option<String>)>,
}

/// `_disk_cache_key`.
pub fn key(input: &KeyInput) -> String {
    let mut env: Vec<&(&str, Option<String>)> = input.env.iter().collect();
    env.sort_by(|a, b| a.0.cmp(b.0));
    let env_json: Vec<String> = env
        .iter()
        .map(|(k, v)| {
            let value = v.as_deref().map(py::json_string).unwrap_or_else(|| "null".to_string());
            format!("{}: {}", py::json_string(k), value)
        })
        .collect();
    let material = format!(
        "{{\"binary\": {}, \"binary_mtime_ns\": {}, \"binary_size\": {}, \"env\": {{{}}}, \"schema\": {}, \"uid\": {}, \"workdir\": {}}}",
        py::json_string(input.binary),
        input.binary_mtime_ns,
        input.binary_size,
        env_json.join(", "),
        py::json_string(SCHEMA),
        input.uid,
        py::json_string(input.workdir),
    );
    hex(&Sha256::digest(material.as_bytes()))
}

/// A usable cached native envelope.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Envelope {
    /// `app_slug == "_unscoped"`: the join is a no-op for this room.
    pub unscoped: bool,
}

/// `_disk_cache_get(..., max_age=limit)` on the raw file bytes. `Ok(None)`
/// is a miss; inputs whose Python reading this cannot mirror fall back.
pub fn evaluate(bytes: &[u8], key: &str, binary: &str, now: f64, limit: f64) -> R<Option<Envelope>> {
    let Ok(text) = std::str::from_utf8(bytes) else {
        return Ok(None);
    };
    if text.starts_with('\u{feff}') {
        return Ok(None);
    }
    let data: Value = serde_json::from_str(text).map_err(|_| Fallback("cache json"))?;
    let Value::Object(data) = data else {
        return Ok(None);
    };
    let cached_at = match data.get("cached_at") {
        Some(Value::Number(n)) => n.as_f64().ok_or(Fallback("cache number"))?,
        Some(Value::Bool(b)) => f64::from(u8::from(*b)),
        Some(Value::String(_)) => return Err(Fallback("cached_at string")),
        _ => return Ok(None),
    };
    if data.get("key").and_then(Value::as_str) != Some(key) || data.get("schema").and_then(Value::as_str) != Some(SCHEMA) {
        return Ok(None);
    }
    let age = now - cached_at;
    if !(0.0..=limit).contains(&age) {
        return Ok(None);
    }
    let raw = match data.get("raw") {
        Some(Value::Object(o)) => o,
        Some(Value::Array(a)) if !a.is_empty() => return Err(Fallback("raw pairs")),
        _ => return Ok(None),
    };
    let Some(resolved_via) = data.get("resolved_via") else {
        return Ok(None);
    };
    let null = Value::Null;
    let channel_dir = raw.get("channel_dir").unwrap_or(&null);
    let app_slug = raw.get("app_slug").unwrap_or(&null);
    if !py::truthy(channel_dir) || !py::truthy(app_slug) {
        return Ok(None);
    }
    let protocol = match raw.get("protocol_version") {
        Some(v) if py::truthy(v) => match v {
            Value::String(s) => s.as_str(),
            _ => return Err(Fallback("protocol type")),
        },
        _ => "1.0",
    };
    if !protocol_in_range(protocol)? {
        return Ok(None);
    }
    if raw.get("coordination_unavailable").map(py::truthy).unwrap_or(false) {
        return Ok(None);
    }
    let native = matches!(resolved_via.as_str(), Some("repo-local-rally-cli" | "fetched-binary"));
    if !native || raw.get("rally_binary").and_then(Value::as_str) != Some(binary) {
        return Ok(None);
    }
    Ok(Some(Envelope { unscoped: app_slug.as_str() == Some("_unscoped") }))
}

/// `_protocol_in_range`: `(1, 0) <= (major, minor) < (3, 0)`.
fn protocol_in_range(version: &str) -> R<bool> {
    let mut parts = version.split('.');
    let major = py::int(parts.next().unwrap_or(""))?;
    let minor = match parts.next() {
        Some(p) => py::int(p)?,
        None => Some(0),
    };
    Ok(match (major, minor) {
        (Some(ma), Some(mi)) => (1, 0) <= (ma, mi) && (ma, mi) < (3, 0),
        _ => false,
    })
}

/// `hook_budget.inner_timeout_seconds(MARGIN_CHILD)` from `RALLY_HOOK_BUDGET_MS`.
pub fn probe_timeout_seconds(raw: Option<&str>) -> R<f64> {
    let ms = match raw {
        None => 3000,
        Some(r) => match py::int(r)? {
            Some(v) if v > 0 => v.min(u64::MAX as u128) as u64,
            _ => 3000,
        },
    };
    let outer = ms as f64 / 1000.0;
    let margin = 1.2_f64;
    let reserve = margin.min(outer * (margin / (margin + 1.2)));
    let below = f64::from_bits(outer.to_bits() - 1);
    Ok((outer - reserve).min(below).max(f64::MIN_POSITIVE))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn record(extra: &str) -> String {
        format!(
            r#"{{"schema": "{SCHEMA}", "key": "k", "cached_at": 100.0, "resolved_via": "repo-local-rally-cli", "raw": {{"channel_dir": "/r/.rally", "app_slug": "s", "rally_binary": "/b/rally"{extra}}}}}"#
        )
    }

    #[test]
    fn record_validity() {
        let ok = record("");
        assert_eq!(evaluate(ok.as_bytes(), "k", "/b/rally", 110.0, 60.0).unwrap(), Some(Envelope { unscoped: false }));
        assert_eq!(evaluate(ok.as_bytes(), "k", "/b/rally", 170.0, 60.0).unwrap(), None);
        assert_eq!(evaluate(ok.as_bytes(), "k", "/b/rally", 99.0, 60.0).unwrap(), None);
        assert_eq!(evaluate(ok.as_bytes(), "x", "/b/rally", 110.0, 60.0).unwrap(), None);
        assert_eq!(evaluate(ok.as_bytes(), "k", "/c/rally", 110.0, 60.0).unwrap(), None);
        let bad_proto = record(r#", "protocol_version": "3.0""#);
        assert_eq!(evaluate(bad_proto.as_bytes(), "k", "/b/rally", 110.0, 60.0).unwrap(), None);
        let degraded = record(r#", "coordination_unavailable": "x""#);
        assert_eq!(evaluate(degraded.as_bytes(), "k", "/b/rally", 110.0, 60.0).unwrap(), None);
        assert_eq!(evaluate(b"[1]", "k", "/b/rally", 110.0, 60.0).unwrap(), None);
        assert!(evaluate(b"{", "k", "/b/rally", 110.0, 60.0).is_err());
    }

    #[test]
    fn default_probe_timeout() {
        assert!((probe_timeout_seconds(None).unwrap() - 1.8).abs() < 1e-9);
    }
}
