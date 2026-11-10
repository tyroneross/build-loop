// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
//! Shared contract for build-loop's native hooks.
//!
//! Every rule here mirrors the Python implementation it replaces, byte for
//! byte where the result is observable (file names, cache keys, argv). The
//! Python source is cited on each item. When an input falls outside what a
//! function can mirror exactly, it returns [`Fallback`] and the caller runs
//! the shell hook instead of guessing.

pub mod cache;
pub mod event;
pub mod identity;
pub mod py;
pub mod stamp;

/// An input the native path cannot mirror exactly. The caller must hand the
/// whole invocation to the shell hook; the string names the reason.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Fallback(pub &'static str);

pub type R<T> = Result<T, Fallback>;

/// Exit status meaning "bl did nothing and did not read stdin; run the shell
/// hook". The launcher in `hooks/pre-edit-rally-point.sh` continues on it.
pub const FALLBACK_EXIT: i32 = 75;

/// `hooks.py:_EVENT_STDIN_MAX_BYTES`.
pub const STDIN_MAX_BYTES: usize = 65_536;

/// `_bounded_hook.sh`: budget in ms, integer 100..=1500, otherwise 1500.
pub const DEADLINE_DEFAULT_MS: u64 = 1500;
pub const DEADLINE_MIN_MS: u64 = 100;

/// Parse `BUILD_LOOP_RALLY_PRE_EDIT_DEADLINE_MS` the way the Perl supervisor
/// does (`/\A[0-9]+\z/ && $ms >= 100 && $ms <= 1500`).
pub fn deadline_ms(raw: Option<&str>) -> u64 {
    let raw = match raw {
        Some(s) if !s.is_empty() => s,
        _ => return DEADLINE_DEFAULT_MS,
    };
    if !raw.bytes().all(|b| b.is_ascii_digit()) {
        return DEADLINE_DEFAULT_MS;
    }
    let digits = raw.trim_start_matches('0');
    if digits.len() > 4 {
        return DEADLINE_DEFAULT_MS;
    }
    let value: u64 = if digits.is_empty() { 0 } else { digits.parse().unwrap_or(u64::MAX) };
    if (DEADLINE_MIN_MS..=DEADLINE_DEFAULT_MS).contains(&value) {
        value
    } else {
        DEADLINE_DEFAULT_MS
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn deadline_budget_rules() {
        assert_eq!(deadline_ms(None), 1500);
        assert_eq!(deadline_ms(Some("")), 1500);
        assert_eq!(deadline_ms(Some("300")), 300);
        assert_eq!(deadline_ms(Some("0100")), 100);
        assert_eq!(deadline_ms(Some("99")), 1500);
        assert_eq!(deadline_ms(Some("1501")), 1500);
        assert_eq!(deadline_ms(Some(" 300")), 1500);
        assert_eq!(deadline_ms(Some("300ms")), 1500);
        assert_eq!(deadline_ms(Some("000000000000000300")), 300);
        assert_eq!(deadline_ms(Some("99999999999999999999")), 1500);
    }
}
