// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
//! Read access to `references/model-taxonomy.json`, mirroring the accessors
//! of `scripts/model_taxonomy.py` and `scripts/host_model_map.py` that model
//! selection uses. Keys starting with `_` are documentation and are skipped.

use serde_json::{Map, Value};
use std::collections::BTreeSet;
use std::path::Path;

pub struct Taxonomy {
    root: Map<String, Value>,
}

impl Taxonomy {
    pub fn load(path: &Path) -> Result<Taxonomy, String> {
        let text = std::fs::read_to_string(path).map_err(|e| format!("model taxonomy unreadable at {}: {e}", path.display()))?;
        Taxonomy::parse(&text).map_err(|e| format!("{e} at {}", path.display()))
    }

    pub fn parse(text: &str) -> Result<Taxonomy, String> {
        match serde_json::from_str::<Value>(text) {
            Ok(Value::Object(root)) if root.contains_key("tiers") && root.contains_key("segments") => Ok(Taxonomy { root }),
            Ok(_) => Err("model taxonomy malformed".into()),
            Err(e) => Err(format!("model taxonomy is not JSON ({e})")),
        }
    }

    fn object(&self, path: &[&str]) -> Option<&Map<String, Value>> {
        let mut at = &self.root;
        for key in path {
            at = at.get(*key)?.as_object()?;
        }
        Some(at)
    }

    fn strings(v: Option<&Value>) -> Vec<String> {
        v.and_then(Value::as_array).map(|a| a.iter().filter_map(|s| s.as_str().map(str::to_string)).collect()).unwrap_or_default()
    }

    /// `tier_ladder`.
    pub fn ladder(&self) -> Vec<String> {
        Self::strings(self.object(&["tiers"]).and_then(|t| t.get("order")))
    }

    fn legacy_alias(&self, token: &str) -> Option<&str> {
        if token.starts_with('_') {
            return None;
        }
        self.object(&["tiers", "legacy_aliases"])?.get(token)?.as_str()
    }

    pub fn is_legacy_tier(&self, token: &str) -> bool {
        self.legacy_alias(token).is_some()
    }

    /// `normalize_tier`: a legacy token or ladder rung to its rung.
    pub fn normalize_tier(&self, token: &str) -> Result<String, String> {
        if token.is_empty() {
            return Err("tier token is empty".into());
        }
        if let Some(rung) = self.legacy_alias(token) {
            return Ok(rung.to_string());
        }
        if self.ladder().iter().any(|r| r == token) {
            return Ok(token.to_string());
        }
        Err(format!("unknown tier token {token:?}"))
    }

    /// `ladder_fallback()[rung]`: the next rung down, if any.
    pub fn ladder_fallback(&self, rung: &str) -> Option<String> {
        if rung.starts_with('_') {
            return None;
        }
        self.object(&["tiers", "fallback"])?.get(rung)?.as_str().map(str::to_string)
    }

    pub fn has_segment(&self, segment: &str) -> bool {
        self.object(&["segments"]).is_some_and(|s| s.contains_key(segment))
    }

    pub fn segments(&self) -> Vec<String> {
        self.object(&["segments"]).map(|s| s.keys().cloned().collect()).unwrap_or_default()
    }

    /// `preferred(segment, tier)`; `[]` for an absent cell or unknown tier.
    pub fn preferred(&self, segment: &str, tier: &str) -> Vec<String> {
        let Ok(rung) = self.normalize_tier(tier) else { return Vec::new() };
        Self::strings(self.object(&["preferred", segment]).and_then(|cell| cell.get(&rung)))
    }

    /// `legacy_registry(token)`.
    pub fn legacy_registry(&self, token: &str) -> Vec<String> {
        if token.starts_with('_') {
            return Vec::new();
        }
        Self::strings(self.object(&["legacy_registry"]).and_then(|r| r.get(token)))
    }

    /// Every model id (keys of `models`, documentation keys excluded).
    pub fn model_ids(&self) -> impl Iterator<Item = &str> {
        self.object(&["models"]).into_iter().flat_map(|m| m.iter()).filter(|(k, v)| !k.starts_with('_') && v.is_object()).map(|(k, _)| k.as_str())
    }

    /// Metadata by exact id, else by case-insensitive id or alias.
    pub fn meta(&self, id: &str) -> Option<&Map<String, Value>> {
        let models = self.object(&["models"])?;
        let key = id.trim();
        if !key.starts_with('_') {
            if let Some(Value::Object(meta)) = models.get(key) {
                return Some(meta);
            }
        }
        let low = key.to_lowercase();
        models.iter().filter(|(k, _)| !k.starts_with('_')).filter_map(|(k, v)| Some((k, v.as_object()?))).find_map(|(k, meta)| {
            let alias_hit = Self::strings(meta.get("aliases")).iter().any(|a| a.to_lowercase() == low);
            (k.to_lowercase() == low || alias_hit).then_some(meta)
        })
    }

    pub fn aliases(&self, id: &str) -> Vec<String> {
        Self::strings(self.meta(id).and_then(|m| m.get("aliases")))
    }

    /// Ids named by `preferred` or `legacy_registry` that are not keys of
    /// `models`. Selection parity with the Python rests on this being empty.
    pub fn unknown_ids(&self) -> Vec<String> {
        let models = self.object(&["models"]);
        let mut named: BTreeSet<String> = BTreeSet::new();
        for cell in self.object(&["preferred"]).into_iter().flat_map(|p| p.values()).filter_map(Value::as_object) {
            named.extend(cell.values().flat_map(|v| Self::strings(Some(v))));
        }
        named.extend(self.object(&["legacy_registry"]).into_iter().flat_map(|r| r.iter()).filter(|(k, _)| !k.starts_with('_')).flat_map(|(_, v)| Self::strings(Some(v))));
        named.into_iter().filter(|id| !models.is_some_and(|m| m.get(id).is_some_and(Value::is_object))).collect()
    }

    fn host_row(&self, family: &str) -> Option<&Map<String, Value>> {
        if family.starts_with('_') {
            return None;
        }
        self.object(&["hosts", family])
    }

    /// `providers_for_host`: `None` for an unknown host or an empty list.
    pub fn providers_for_host(&self, host: &str) -> Option<BTreeSet<String>> {
        let providers: BTreeSet<String> = Self::strings(self.host_row(&host.trim().to_lowercase())?.get("providers")).iter().map(|p| p.trim().to_lowercase()).filter(|p| !p.is_empty()).collect();
        (!providers.is_empty()).then_some(providers)
    }

    /// `host_family_for_token`: the family a token names, by id or alias.
    fn host_family(&self, token: &str) -> Option<String> {
        if self.host_row(token).is_some() {
            return Some(token.to_string());
        }
        self.object(&["hosts"])?.iter().filter(|(k, v)| !k.starts_with('_') && v.is_object()).find(|(_, row)| Self::strings(row.get("aliases")).iter().any(|a| a.trim().to_lowercase() == token)).map(|(k, _)| k.clone())
    }

    /// `expand_host_tokens`: host names become their providers, anything
    /// else passes through as a provider name.
    pub fn expand_host_tokens(&self, raw: &str) -> BTreeSet<String> {
        let mut out = BTreeSet::new();
        for token in raw.split(',').map(|t| t.trim().to_lowercase()).filter(|t| !t.is_empty()) {
            match self.host_family(&token).and_then(|family| self.providers_for_host(&family)) {
                Some(providers) => out.extend(providers),
                None => {
                    out.insert(token);
                }
            }
        }
        out
    }

    /// Host family names, for enumerating every host the taxonomy defines.
    pub fn hosts(&self) -> Vec<String> {
        self.object(&["hosts"]).map(|h| h.iter().filter(|(k, v)| !k.starts_with('_') && v.is_object()).map(|(k, _)| k.clone()).collect()).unwrap_or_default()
    }
}

/// The keys of a JSON object's top level in file order. `serde_json` sorts
/// object keys; Python keeps file order, and the tier cache is walked in it.
pub(crate) fn top_level_keys(text: &str) -> Vec<String> {
    let bytes = text.as_bytes();
    let mut keys = Vec::new();
    let (mut depth, mut i) = (0usize, 0usize);
    while i < bytes.len() {
        match bytes[i] {
            b'{' | b'[' => depth += 1,
            b'}' | b']' => depth = depth.saturating_sub(1),
            b'"' => {
                let start = i;
                i += 1;
                while i < bytes.len() && bytes[i] != b'"' {
                    i += if bytes[i] == b'\\' { 2 } else { 1 };
                }
                let end = (i + 1).min(bytes.len());
                let is_key = bytes[end..].iter().find(|b| !b.is_ascii_whitespace()) == Some(&b':');
                if depth == 1 && is_key {
                    if let Ok(key) = serde_json::from_slice::<String>(&bytes[start..end]) {
                        keys.push(key);
                    }
                }
            }
            _ => {}
        }
        i += 1;
    }
    keys
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn top_level_keys_keep_file_order_and_skip_nested() {
        let text = r#"{"z": {"a": 1, "k": "x: y"}, "b\"q": [{"n": 2}], "a": "\"z\": 1"}"#;
        assert_eq!(top_level_keys(text), vec!["z", "b\"q", "a"]);
    }

    #[test]
    fn the_shipped_taxonomy_names_only_known_models() {
        let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../references/model-taxonomy.json");
        let tax = Taxonomy::load(&path).unwrap();
        assert_eq!(tax.unknown_ids(), Vec::<String>::new());
        assert_eq!(tax.normalize_tier("frontier").unwrap(), "T1");
        assert!(tax.expand_host_tokens("claude").contains("anthropic"));
    }
}
