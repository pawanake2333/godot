#!/usr/bin/env python3
"""Compile a real native fixture and reload it through one persistent MCP session."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from e2e_mcp_test import MCPClient
from process_cleanup import cleanup_fixture

ROOT = Path(__file__).resolve().parents[1]


def compile_fixture(compiler, interface, output, generation):
    source = ROOT / "tests/fixtures/reload_extension.cpp"
    if os.name == "nt":
        command = [compiler, "/nologo", "/LD", "/MT", "/EHsc", "/std:c++20", f"/DPROBE_GENERATION={generation}", f"/I{interface.parent}", str(source), f"/Fo{output.with_suffix('.obj')}", "/link", f"/OUT:{output}"]
    else:
        command = [compiler, "-std=c++20", "-fPIC", "-fvisibility=hidden", "-dynamiclib" if sys.platform == "darwin" else "-shared", f"-DPROBE_GENERATION={generation}", "-I", str(interface.parent), str(source), "-o", str(output)]
        if sys.platform.startswith("linux"):
            command.extend(["-static-libstdc++", "-static-libgcc"])
    subprocess.run(command, check=True, capture_output=True, text=True, timeout=60)


def run(args, project):
    addon = project / "addons/godot_mcp"
    shutil.copytree(ROOT, addon, ignore=shutil.ignore_patterns("tests", "__pycache__", ".godot"))
    binary_dir = project / "addons/reload_probe/bin"
    binary_dir.mkdir(parents=True)
    binary = binary_dir / ("probe.dll" if os.name == "nt" else "probe.dylib" if sys.platform == "darwin" else "probe.so")
    declaration = binary_dir.parent / "probe.gdextension"
    original_config = f'[configuration]\nentry_symbol="mcp_reload_probe_init"\ncompatibility_minimum="4.7"\nreloadable=true\n[libraries]\nmacos.debug="res://addons/reload_probe/bin/{binary.name}"\nlinux.debug.x86_64="res://addons/reload_probe/bin/{binary.name}"\nwindows.debug.x86_64="res://addons/reload_probe/bin/{binary.name}"\n'
    declaration.write_text(original_config, encoding="utf-8")
    (project / "project.godot").write_text('config_version=5\n[application]\nconfig/name="MCP native reload fixture"\nrun/main_scene="res://main.tscn"\n[editor_plugins]\nenabled=PackedStringArray("res://addons/godot_mcp/plugin.cfg")\n[rendering]\nrenderer/rendering_method="gl_compatibility"\n', encoding="utf-8")
    (project / "main.tscn").write_text('[gd_scene format=3]\n[node name="ReloadTest" type="Node"]\n', encoding="utf-8")
    with (project / "project.godot").open("a", encoding="utf-8") as settings:
        settings.write('\n[autoload]\nGodotMCPRuntime="*res://addons/godot_mcp/runtime/runtime_helper.gd"\n')
    compile_fixture(args.compiler, args.interface, binary, 1)
    imported = subprocess.run([str(args.godot), "--headless", "--editor", "--path", str(project), "--import"], capture_output=True, text=True, timeout=60)
    assert imported.returncode == 0 and "ERROR:" not in imported.stdout + imported.stderr, (imported.stdout, imported.stderr)
    client = MCPClient(ROOT / "sidecar/godot_mcp_server.py", project, args.godot)

    def state():
        return client.call("godot_editor_status")["state"]

    def finish(job, expected="succeeded", code=None):
        for _ in range(5):
            done = client.call("godot_job_wait", {"job_id": job["job_id"], "timeout_ms": 15000})
            if done["state"] in {"succeeded", "failed", "cancelled", "timed_out"}:
                break
        assert done["state"] == expected, done
        if code:
            assert code in done["error"], done
        return done.get("result", done)

    def reload(extra=None, error=None):
        current = state()
        arguments = {"path": "res://addons/reload_probe/probe.gdextension", "editor_epoch": current["editor_epoch"], "expected_document_revision": current["document_revision"], **(extra or {})}
        return finish(client.call("godot_extension_reload", arguments), "failed" if error else "succeeded", error)

    def methods():
        return {item["name"] for item in client.call("godot_class_describe", {"class_name": "MCPReloadProbe"})["methods"]}

    report = {}
    try:
        started = finish(client.call("godot_editor_start", {"display_mode": "headless_logic", "timeout_ms": 60000}))
        client.call("godot_scene_open", {"path": "res://main.tscn"})
        initial = state()
        assert "generation_one" in methods() and "generation_two" not in methods()
        reload({"editor_epoch": "stale"}, "EDITOR_CONFLICT")
        reload({"expected_document_revision": "stale"}, "DOCUMENT_CONFLICT")
        reload({"path": "res://missing.gdextension"}, "FILE_NOT_FOUND")
        reload({"expected_library_hash": "0" * 64}, "LIBRARY_CONFLICT")
        declaration.write_text(original_config.replace("reloadable=true", "reloadable=false"), encoding="utf-8")
        reload(error="NOT_RELOADABLE")
        declaration.write_text(original_config, encoding="utf-8")
        created = client.call("godot_scene_create", {"root_type": "Node", "root_name": "Dirty"})
        client.call("godot_edit_batch", {"scene_id": created["scene_id"], "expected_revision": created["revision"], "operations": [{"op": "create", "name": "UnsavedChild", "type": "Node"}]})
        reload(error="UNSAVED_CHANGES")
        active = client.call("godot_scene_list")["active"]
        client.call("godot_scene_close", {"scene_id": active["scene_id"], "expected_revision": active["revision"], "unsaved": "discard"})
        owned = finish(client.call("godot_run_start", {"display_mode": "headless_logic"}))
        reload(error="ACTIVE_GAME")
        finish(client.call("godot_run_stop", {"run_id": owned["run_id"]}))
        report["preflight_guards"] = True

        compile_fixture(args.compiler, args.interface, binary, 2)
        expected_hash = hashlib.sha256(binary.read_bytes()).hexdigest()
        changed = reload({"expected_library_hash": expected_hash, "expected_classes": {"MCPReloadProbe": ["generation_two"]}})
        assert changed["verified"] and changed["reloaded"] and not changed["requires_restart"], changed
        assert changed["library_hash"] == expected_hash and changed["editor_pid"] == initial["editor_pid"]
        assert "generation_two" in methods() and "generation_one" not in methods()
        report["new_code_and_removed_method"] = True

        reload({"expected_classes": {"MCPReloadProbe": ["missing_native_method"]}}, "RELOAD_VALIDATION_FAILED")
        restored = reload({"expected_classes": {"MCPReloadProbe": ["generation_two"]}})
        assert restored["verified"] and state()["editor_epoch"] == initial["editor_epoch"]
        compile_fixture(args.compiler, args.interface, binary, 1)
        back = reload({"expected_classes": {"MCPReloadProbe": ["generation_one"]}})
        assert back["verified"] and "generation_two" not in methods()
        assert state()["editor_pid"] == initial["editor_pid"]
        report["multiple_reloads_same_process"] = True
        report["expected_registration_failure_is_reported"] = True
        current = state()
        closed = finish(client.call("godot_editor_close", {"editor_epoch": current["editor_epoch"], "expected_document_revision": current["document_revision"]}))
        if closed["exit_code"] != 0:
            artifact = client.call("godot_artifact_read", {"artifact_id": started["log_artifact"], "length": 1048576})
            print(base64.b64decode(artifact["data"]).decode("utf-8", errors="replace")[-18000:], flush=True)
        assert closed["exit_code"] == 0, closed
        leftovers = [path.name for path in binary_dir.glob("*~")]
        assert not leftovers, f"Temporary native library copies survived editor exit (editor {initial['editor_pid']}): {leftovers}"
        report["temporary_copy_cleanup"] = True
        report["status"] = "passed"
        return report
    finally:
        try:
            status = client.call("godot_editor_status")
            if status.get("connected"):
                current = status["state"]
                finish(client.call("godot_editor_close", {"editor_epoch": current["editor_epoch"], "expected_document_revision": current["document_revision"], "unsaved": "discard"}))
        finally:
            client.close()
            if os.name != "nt":
                cleanup_fixture(project, args.godot)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--godot", required=True, type=Path)
    parser.add_argument("--interface", required=True, type=Path, help="Matching gdextension_interface.gen.h")
    parser.add_argument("--compiler", default=os.environ.get("CXX", "cl" if os.name == "nt" else "c++"))
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    args.godot = args.godot.resolve()
    args.interface = args.interface.resolve()
    with tempfile.TemporaryDirectory(prefix="godot-mcp-native-reload-") as directory:
        report = run(args, Path(directory))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.report:
        args.report.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
