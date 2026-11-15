//! Test-only semantic entrypoint: exact production modules, no outer watchdog.
//! The parity harness imposes an external 30-second bound.
#[path = "../src/pre_edit.rs"]
mod pre_edit;
#[path = "../src/sys.rs"]
mod sys;

fn main() {
    let root = std::env::var("CLAUDE_PLUGIN_ROOT").expect("explicit fixture plugin root");
    match pre_edit::run(&root) {
        pre_edit::Outcome::Done => (),
        pre_edit::Outcome::Fallback { .. } => {
            eprintln!("pre-edit semantic runner: unexpected fallback");
            std::process::exit(75);
        }
    }
}
