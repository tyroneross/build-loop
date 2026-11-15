// SPDX-License-Identifier: Apache-2.0
//! Trace timestamp and run membership, mirroring build_loop_id.resolve_run_identity
//! and temporal_membership.is_member with bound_hours=0 and explicit sessions.
use bl_contract::py;
use chrono::{DateTime, Datelike, NaiveDate, NaiveDateTime};
use serde_json::{Map, Value};

/// A timezone-bearing RFC3339 event timestamp. Python datetime has microsecond
/// precision; reject leap seconds and truncate finer fractions consistently.
pub(super) fn event_time(text: &str) -> Option<i64> {
    if text.ends_with('z') { return None; } // Python datetime accepts uppercase Z only.
    let dt = DateTime::parse_from_rfc3339(text).ok()?;
    ((1..=9999).contains(&dt.year()) && dt.timestamp_subsec_nanos() < 1_000_000_000)
        .then(|| dt.timestamp_micros())
}

fn state_time(value: &Value) -> Option<i64> {
    let text = py::strip(value.as_str()?);
    if let Some(t) = event_time(text) { return Some(t); }
    let text = text.strip_suffix('Z').unwrap_or(text);
    for fmt in ["%Y%m%dT%H%M%S", "%Y%m%dT%H%M", "%Y-%m-%dT%H:%M:%S%.f", "%Y-%m-%d %H:%M:%S%.f"] {
        if let Ok(t) = NaiveDateTime::parse_from_str(text, fmt) { return Some(t.and_utc().timestamp_micros()); }
    }
    NaiveDate::parse_from_str(text, "%Y-%m-%d").ok()?.and_hms_opt(0, 0, 0).map(|t| t.and_utc().timestamp_micros())
}

/// Keep the existing Python float-to-nanoseconds calculation so persisted
/// timestamps and reader windows do not change during migration.
pub(super) fn nanos(micros: i64) -> i128 {
    let seconds = micros.div_euclid(1_000_000);
    let fraction = micros.rem_euclid(1_000_000);
    ((seconds as f64 + fraction as f64 / 1_000_000.0) * 1_000_000_000.0) as i128
}

fn identifier(value: Option<&Value>) -> Option<&str> {
    value.and_then(Value::as_str).map(py::strip).filter(|s| !s.is_empty())
}

fn aliases(map: &Map<String, Value>) -> Result<Option<&str>, ()> {
    let a = identifier(map.get("build_loop_id"));
    let b = identifier(map.get("run_id"));
    if a.is_some() && b.is_some() && a != b { Err(()) } else { Ok(a.or(b)) }
}

pub(super) fn attached_run<'a>(state: &'a Value, session: &str, time: i64) -> Option<&'a str> {
    let root = state.as_object()?;
    let execution = root.get("execution")?.as_object()?;
    let run = aliases(execution).ok()?.or_else(|| aliases(root).ok().flatten())?;
    let start = state_time(execution.get("started_at")?)?;
    let sessions = [identifier(execution.get("current_session_id")), identifier(execution.get("started_by_session_id"))];
    if session.is_empty() || !sessions.contains(&Some(session)) || time < start { return None; }
    if let Some(end) = execution.get("ended_at").filter(|v| !v.is_null()) {
        let end = state_time(end)?;
        if end < start || time > end { return None; }
    }
    Some(run)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn attribution_requires_run_owner_and_inclusive_time_window() {
        let start = event_time("2026-06-01T00:00:00Z").unwrap();
        let mut state = json!({"execution":{"build_loop_id":"run-a","started_at":"20260601T000000Z",
            "ended_at":"2026-06-01T01:00:00+00:00","current_session_id":"s","started_by_session_id":"original"}});
        assert_eq!(attached_run(&state,"s",start),Some("run-a"));
        assert_eq!(attached_run(&state,"original",start),Some("run-a"));
        assert_eq!(attached_run(&state,"other",start),None);
        assert_eq!(attached_run(&state,"s",start-1),None);
        assert_eq!(attached_run(&state,"s",start+3_600_000_000),Some("run-a"));
        assert_eq!(attached_run(&state,"s",start+3_600_000_001),None);
        state["execution"]["run_id"] = json!("conflict");
        assert_eq!(attached_run(&state,"s",start),None);
        state["execution"].as_object_mut().unwrap().remove("build_loop_id");
        state["execution"].as_object_mut().unwrap().remove("run_id");
        state["run_id"] = json!("legacy");
        assert_eq!(attached_run(&state,"s",start),Some("legacy"));
        state.as_object_mut().unwrap().remove("run_id");
        state["runs"] = json!([{"run_id":"completed"}]);
        assert_eq!(attached_run(&state,"s",start),None);
    }
}
