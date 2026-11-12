"""Portable package identity; no machine paths are part of the fingerprint."""
import hashlib
import json
from pathlib import Path

VERSION = "0.6.1"
PROTOCOL = 2
ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIRS = {"contract", "editor", "runtime", "sidecar", "tools"}


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1048576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_files(root=ROOT):
    return {path.relative_to(root).as_posix(): file_hash(path) for path in sorted(root.rglob("*"))
            if path.is_file() and not path.is_symlink() and
            (path.name == "plugin.cfg" or (path.relative_to(root).parts[0] in RUNTIME_DIRS and path.suffix in {".gd", ".py"}))}


def source_identity(root=ROOT):
    files = source_files(root)
    fingerprint = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"version": VERSION, "protocol": PROTOCOL, "source_sha256": fingerprint, "files": files}
