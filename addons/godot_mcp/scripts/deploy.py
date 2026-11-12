#!/usr/bin/env python3
"""Generate identities and synchronize a plugin copy with local-change protection."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sidecar"))
from package_info import source_identity, file_hash

EXCLUDED = {"__pycache__", ".godot", ".DS_Store"}


def deploy_files(root):
    return {path.relative_to(root).as_posix(): file_hash(path) for path in sorted(root.rglob("*"))
            if path.is_file() and not path.is_symlink() and not (set(path.relative_to(root).parts) & EXCLUDED)
            and path.suffix not in {".pyc", ".uid"} and path.name != "deployment.json"}


def synchronize(source, target, *, baseline=None, check=False):
    """Only replace files matching the last manifest (or an explicitly supplied baseline)."""
    source, target = Path(source).resolve(), Path(target).resolve()
    if source == target or source in target.parents or target in source.parents:
        raise ValueError("Source and target must be separate plugin directories")
    desired = deploy_files(source)
    present = deploy_files(target) if target.exists() else {}
    manifest_path = target / "deployment.json"
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text())["files"]
    elif baseline is not None:
        previous = deploy_files(Path(baseline))
    elif present:
        raise ValueError("Existing copy has no deployment manifest; supply --baseline after reviewing its source")
    else:
        previous = {}
    conflicts = sorted(name for name in set(present) | set(previous)
                       if present.get(name) != previous.get(name) and present.get(name) != desired.get(name))
    if conflicts:
        raise ValueError("Deployment conflicts (nothing changed): " + ", ".join(conflicts))
    changed = sorted(name for name in set(desired) | set(present) if desired.get(name) != present.get(name))
    if check:
        return {"matches": not changed, "changed": changed, "source_sha256": source_identity(source)["source_sha256"]}
    target.mkdir(parents=True, exist_ok=True)
    # Preflight above is complete before any mutation; replace each file atomically.
    for name in changed:
        path = target / name
        if name in desired:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + ".mcp-copy")
            shutil.copyfile(source / name, temporary)
            temporary.replace(path)
        else:
            path.unlink()
    manifest = {**source_identity(source), "files": desired}
    temporary = manifest_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    temporary.replace(manifest_path)
    if deploy_files(target) != desired:
        raise RuntimeError("Post-deployment hash verification failed")
    return {"matches": True, "changed": changed, "source_sha256": manifest["source_sha256"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, help="Target addons/godot_mcp directory")
    parser.add_argument("--baseline", type=Path, help="Reviewed previous plugin copy, for first adoption only")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--manifest", action="store_true", help="Generate package identity and tool catalog from current sources")
    args = parser.parse_args()
    if args.manifest:
        from godot_mcp_server import TOOLS
        catalog = json.dumps(TOOLS, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
        (ROOT / "docs/tool_catalog.json").write_text(catalog)
        (ROOT / "package.json").write_text(json.dumps(source_identity(), indent=2, sort_keys=True) + "\n")
    if args.target:
        result = synchronize(ROOT, args.target, baseline=args.baseline, check=args.check)
        print(json.dumps(result, indent=2))
        return 0 if result["matches"] else 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError) as error:
        sys.exit(str(error))
