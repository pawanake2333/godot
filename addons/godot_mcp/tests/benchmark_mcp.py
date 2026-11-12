#!/usr/bin/env python3
"""Small reproducible idle/query baseline; measures timer windows, never polls by sleep."""
import argparse
import asyncio
import contextlib
import json
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sidecar"))
from lifecycle import Watch
from package_info import file_hash
from e2e_mcp_test import MCPClient
from process_cleanup import cleanup_fixture


def cpu_seconds(value):
    parts = value.split(":")
    return sum(float(part) * 60 ** index for index, part in enumerate(reversed(parts)))


def usage(pid):
    raw = subprocess.check_output(["ps", "-o", "time=,rss=", "-p", str(pid)], text=True).split()
    return {"cpu_seconds": cpu_seconds(raw[0]), "rss_kib": int(raw[1])}


async def observation_window(seconds):
    # This timer defines the CPU measurement interval, not a readiness delay.
    loop = asyncio.get_running_loop()
    finished = loop.create_future()
    loop.call_later(seconds, finished.set_result, None)
    await finished


def measure_calls(client, name, arguments, count=100):
    samples = []
    for _ in range(count):
        started = time.perf_counter()
        client.call(name, arguments)
        samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    return {"count": count, "p50_ms": statistics.median(samples), "p95_ms": samples[int(len(samples) * .95) - 1], "max_ms": max(samples)}


def measure(godot, enabled):
    with tempfile.TemporaryDirectory(prefix="godot-mcp-benchmark-") as directory:
        project = Path(directory)
        shutil.copytree(ROOT, project / "addons/godot_mcp", ignore=shutil.ignore_patterns("__pycache__", "tests"))
        probe = project / "addons/probe"
        probe.mkdir()
        (probe / "plugin.cfg").write_text('[plugin]\nname="Probe"\ndescription="Benchmark readiness"\nauthor="MCP"\nversion="1"\nscript="probe.gd"\n')
        (probe / "probe.gd").write_text('@tool\nextends EditorPlugin\nfunc _enter_tree() -> void:\n\twhile not EditorInterface.is_editor_ready() or EditorInterface.get_resource_filesystem().is_scanning():\n\t\tawait get_tree().process_frame\n\tvar file := FileAccess.open("res://.godot/benchmark.ready", FileAccess.WRITE)\n\tfile.store_string("ready")\n')
        plugins = ['"res://addons/probe/plugin.cfg"'] + (['"res://addons/godot_mcp/plugin.cfg"'] if enabled else [])
        (project / "project.godot").write_text('config_version=5\n[application]\nconfig/name="MCPBaseline"\n[editor_plugins]\nenabled=PackedStringArray(' + ','.join(plugins) + ')\n')
        (project / "main.tscn").write_text('[gd_scene format=3]\n[node name="Baseline" type="Node"]\n[node name="Child" type="Node" parent="."]\n')
        imported = subprocess.run([str(godot), "--headless", "--editor", "--path", directory, "--quit"], capture_output=True, text=True, timeout=60)
        if imported.returncode or "SCRIPT ERROR" in imported.stderr:
            raise RuntimeError(imported.stdout + imported.stderr)
        ready = project / ".godot/benchmark.ready"
        ready.unlink(missing_ok=True)
        with (project / "benchmark.log").open("w") as output, contextlib.closing(Watch(project / ".godot")) as watch:
            editor = subprocess.Popen([str(godot), "--headless", "--editor", "--path", directory, "res://main.tscn"], stdout=output, stderr=subprocess.STDOUT)
            client = None
            try:
                deadline = time.monotonic() + 30
                while not ready.exists():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or editor.poll() is not None:
                        raise RuntimeError((project / "benchmark.log").read_text())
                    watch.wait(remaining)
                before = usage(editor.pid)
                begin = time.monotonic()
                asyncio.run(observation_window(2))
                after = usage(editor.pid)
                result = {"idle_window_s": time.monotonic() - begin, "idle_cpu_seconds": after["cpu_seconds"] - before["cpu_seconds"], "idle_rss_kib": after["rss_kib"]}
                if enabled:
                    client = MCPClient(ROOT / "sidecar/godot_mcp_server.py", project, godot)
                    scene = client.call("godot_scene_list")["active"]
                    result["scene_list"] = measure_calls(client, "godot_scene_list", {})
                    result["scene_tree"] = measure_calls(client, "godot_scene_tree", {"scene_id": scene["scene_id"]})
                    result["events_read"] = measure_calls(client, "godot_events_read", {})
                    result["rss_after_queries_kib"] = usage(editor.pid)["rss_kib"]
                return result
            finally:
                if client:
                    client.close()
                cleanup_fixture(project, godot)
                editor.wait(timeout=15)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--godot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    executable = args.godot.resolve()
    report = {"method": "One warmed minimal project per mode, 2s idle timer window, 100 sequential calls per operation; development baseline, not a statistical performance guarantee", "engine_sha256": file_hash(executable), "plugin_disabled": measure(executable, False), "plugin_enabled": measure(executable, True)}
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
