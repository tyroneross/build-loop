// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
//! `bl route`: the native model resolver on the command line.
//!
//! - `bl route resolve --tier T [--segment S] [--host H] [--workdir W]`
//!   selects what `python3 scripts/model_index.py resolve` selects;
//! - `bl route agent NAME [--host-providers H] [--agents-dir D] [--workdir W]`
//!   selects what `python3 scripts/resolve_agent_model.py NAME` selects.
//! - `bl route batch` answers one JSON request per stdin line
//!   (`{"op":"resolve","tier",..}` or `{"op":"agent","agent",..}`, each with
//!   optional `segment`, `host`, `workdir`, `agents_dir`) with one JSON line,
//!   so a caller resolving many roles starts one process.
//!
//! Prints the model id, or with `--json` `{"model","source","resolved"}`.
//! Exit 0 resolved, 1 not resolved or not found, 2 usage or taxonomy error,
//! as `model_index.py` does.

use crate::cli;
use bl_route::{EnvView, Resolver, Taxonomy};
use std::path::PathBuf;

pub fn now() -> f64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

pub fn main(args: &[String]) -> i32 {
    match run(args) {
        Ok(code) => code,
        Err(message) => {
            eprintln!("bl route: {message}");
            2
        }
    }
}

fn run(args: &[String]) -> Result<i32, String> {
    let p = cli::parse(args, &["--tier", "--segment", "--host", "--host-providers", "--workdir", "--taxonomy", "--agents-dir"], &["--json"])?;
    let taxonomy = Taxonomy::load(&cli::taxonomy_path(p.value("--taxonomy"))?)?;
    if p.positional == ["batch"] {
        return batch(&taxonomy);
    }
    let workdir = PathBuf::from(p.value("--workdir").unwrap_or("."));
    let resolver = Resolver::new(&taxonomy, &workdir, EnvView::from_process(), now());
    let result = match p.positional.iter().map(String::as_str).collect::<Vec<_>>()[..] {
        ["resolve"] => {
            let tier = p.value("--tier").ok_or("resolve needs --tier")?;
            resolver.resolve(tier, p.value("--segment").filter(|s| !s.is_empty()), &resolver.parse_host(p.value("--host")))
        }
        ["agent", name] => {
            let agents_dir = match p.value("--agents-dir") {
                Some(dir) => PathBuf::from(dir),
                None => cli::root().ok_or("no build-loop checkout around the binary; pass --agents-dir")?.join("agents"),
            };
            resolver.resolve_agent(name, &agents_dir, &resolver.parse_host_providers(p.value("--host-providers")))
        }
        _ => return Err("usage: bl route resolve --tier T [--segment S] [--host H] | bl route agent NAME".into()),
    };
    let resolution = match result {
        Ok(r) => r,
        Err(message) => {
            eprintln!("{}", serde_json::json!({"error": message, "kind": "not-found"}));
            return Ok(1);
        }
    };
    if p.switch("--json") {
        println!("{}", serde_json::json!({"model": resolution.model, "source": resolution.source, "resolved": resolution.resolved}));
    } else {
        println!("{}", resolution.model.as_deref().unwrap_or(""));
    }
    Ok(if resolution.resolved { 0 } else { 1 })
}

fn batch(taxonomy: &Taxonomy) -> Result<i32, String> {
    use std::io::{BufRead, Write};
    let env = EnvView::from_process();
    let mut out = std::io::stdout().lock();
    for line in std::io::stdin().lock().lines() {
        let line = line.map_err(|e| e.to_string())?;
        if line.trim().is_empty() {
            continue;
        }
        let request: serde_json::Value = serde_json::from_str(&line).map_err(|e| format!("bad request line: {e}"))?;
        let text = |key: &str| request.get(key).and_then(serde_json::Value::as_str);
        let resolver = Resolver::new(taxonomy, &PathBuf::from(text("workdir").unwrap_or(".")), env.clone(), now());
        let result = match text("op") {
            Some("agent") => {
                let agents_dir = match text("agents_dir") {
                    Some(dir) => PathBuf::from(dir),
                    None => cli::root().ok_or("no build-loop checkout around the binary")?.join("agents"),
                };
                resolver.resolve_agent(text("agent").unwrap_or(""), &agents_dir, &resolver.parse_host_providers(text("host")))
            }
            _ => resolver.resolve(text("tier").unwrap_or(""), text("segment").filter(|s| !s.is_empty()), &resolver.parse_host(text("host"))),
        };
        let reply = match result {
            Ok(r) => serde_json::json!({"model": r.model, "source": r.source, "resolved": r.resolved}),
            Err(message) => serde_json::json!({"error": message}),
        };
        writeln!(out, "{reply}").map_err(|e| e.to_string())?;
    }
    Ok(0)
}
