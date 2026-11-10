// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
//! Argument parsing shared by `bl route` and `bl run`, and where the
//! checkout around the binary keeps its data files.

use std::collections::BTreeMap;
use std::path::PathBuf;

pub struct Parsed {
    pub values: BTreeMap<String, String>,
    pub switches: Vec<String>,
    pub positional: Vec<String>,
}

impl Parsed {
    pub fn value(&self, flag: &str) -> Option<&str> {
        self.values.get(flag).map(String::as_str)
    }
    pub fn switch(&self, flag: &str) -> bool {
        self.switches.iter().any(|s| s == flag)
    }
}

/// `--flag value`, `--flag=value` and bare switches; `--` ends the flags.
pub fn parse(args: &[String], value_flags: &[&str], switch_flags: &[&str]) -> Result<Parsed, String> {
    let mut parsed = Parsed { values: BTreeMap::new(), switches: Vec::new(), positional: Vec::new() };
    let mut iter = args.iter();
    while let Some(arg) = iter.next() {
        if arg == "--" {
            parsed.positional.extend(iter.cloned());
            break;
        }
        let (name, inline) = match arg.split_once('=') {
            Some((n, v)) if n.starts_with("--") => (n, Some(v.to_string())),
            _ => (arg.as_str(), None),
        };
        if value_flags.contains(&name) {
            let value = match inline {
                Some(v) => v,
                None => iter.next().cloned().ok_or_else(|| format!("{name} needs a value"))?,
            };
            parsed.values.insert(name.to_string(), value);
        } else if switch_flags.contains(&name) && inline.is_none() {
            parsed.switches.push(name.to_string());
        } else if arg.starts_with("--") {
            return Err(format!("unknown option {arg}"));
        } else {
            parsed.positional.push(arg.clone());
        }
    }
    Ok(parsed)
}

/// The build-loop checkout the binary lives in (`bin/native/bl` or
/// `target/<profile>/bl`), or `BL_ROOT`.
pub fn root() -> Option<PathBuf> {
    if let Some(root) = std::env::var_os("BL_ROOT").filter(|r| !r.is_empty()) {
        return Some(PathBuf::from(root));
    }
    crate::plugin_root("references/model-taxonomy.json")
}

pub fn taxonomy_path(explicit: Option<&str>) -> Result<PathBuf, String> {
    match explicit {
        Some(path) => Ok(PathBuf::from(path)),
        None => root().map(|r| r.join("references/model-taxonomy.json")).ok_or_else(|| "no build-loop checkout around the binary; pass --taxonomy or set BL_ROOT".to_string()),
    }
}
