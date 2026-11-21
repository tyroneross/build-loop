"""Index Easy Terminal retention receipts together as one searchable project reference."""
import argparse
import json
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipts", nargs="+", type=Path, help="JSON output of worktrees retain --json")
    parser.add_argument("--name", required=True, help="searchable measurement or product name")
    parser.add_argument("--project", required=True)
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--host", choices=["claude_code", "codex", "gemini", "other"], required=True)
    args = parser.parse_args()
    lines = [f"# {args.name}", ""]
    for source in args.receipts:
        receipt = json.loads(source.read_text())
        if receipt.get("event") != "artifact-retained":
            parser.error(f"{source} is not an artifact retention receipt")
        proof = receipt.get("verification") or {}
        lines.extend([
            f"## {receipt['relative_path']}", "",
            receipt.get("purpose") or receipt["consumer"],
            f"Receipt {receipt['id']}. SHA-256 {receipt['sha256']}.",
            f"Recovery copy: {receipt['retained_path']}",
            f"Producer revision: {receipt.get('build_revision') or 'unknown'}. "
            f"Checkout revision: {receipt.get('head_at_capture') or receipt.get('staged_head') or 'unknown'}.",
            f"Verification: {proof.get('status', 'unchecked')} · {proof.get('tool', '')} · {proof.get('result', '')}", "",
        ])
    writer = Path(__file__).with_name("memory_writer.py")
    command = [sys.executable, str(writer), "--scope", "project", "--project", args.project,
               "write", "--type", "reference", "--name", args.name, "--description", args.name,
               "--host", args.host, "--run-id", args.run_id, "--workdir", args.workdir, "--json"]
    subprocess.run(command, input="\n".join(lines), text=True, check=True)


if __name__ == "__main__":
    main()
