from __future__ import annotations

import base64
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from scripts.phase3 import realtime_recommendation as bridge


POWERSHELL = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
RUNNER_PATH = WORKSPACE_ROOT / "scripts" / "run_recommendation.ps1"


def invoke_powershell_runner(
    parameters: dict[str, str | int | bool],
) -> subprocess.CompletedProcess[str]:
    if POWERSHELL is None:
        raise RuntimeError("PowerShell is required for the runner contract tests")

    runner = str(RUNNER_PATH).replace("'", "''")
    parameter_json = json.dumps(parameters, ensure_ascii=True).replace("'", "''")
    command = f"""
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$ErrorActionPreference = 'Stop'
function global:Get-Command {{
    [CmdletBinding()]
    param([Parameter(Position = 0)][string]$Name)
    if ($Name -ne 'py') {{
        throw "Unexpected command lookup: $Name"
    }}
    $capture = {{
        $global:LASTEXITCODE = 0
        $captured = ConvertTo-Json -Compress -InputObject ([string[]]$args)
        [Console]::Out.WriteLine("RUNNER_ARGV=$captured")
    }}
    [pscustomobject]@{{ Source = $capture }}
}}
$runnerParameters = @{{}}
(ConvertFrom-Json -InputObject '{parameter_json}').PSObject.Properties |
    ForEach-Object {{ $runnerParameters[$_.Name] = $_.Value }}
try {{
    & '{runner}' @runnerParameters
}}
catch {{
    [Console]::Error.WriteLine("RUNNER_BINDING_ERROR=$($_.FullyQualifiedErrorId)")
    exit 23
}}
"""
    encoded_command = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded_command],
        cwd=WORKSPACE_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def captured_runner_argv(result: subprocess.CompletedProcess[str]) -> list[str]:
    marker = "RUNNER_ARGV="
    lines = [line for line in result.stdout.splitlines() if line.startswith(marker)]
    if len(lines) != 1:
        raise AssertionError(
            f"expected one captured argv line, got {lines!r}; "
            f"stdout={result.stdout!r}; stderr={result.stderr!r}"
        )
    value = json.loads(lines[0][len(marker) :])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise AssertionError(f"captured argv is not a string list: {value!r}")
    return value


class FakeStdin:
    def __init__(self, *, block: bool = False, broken: bool = False) -> None:
        self.block = block
        self.broken = broken
        self.closed = False
        self.writes: list[str] = []
        self.write_started = threading.Event()
        self.release_write = threading.Event()
        if not block:
            self.release_write.set()

    def write(self, value: str) -> int:
        if self.closed:
            raise ValueError("stdin is closed")
        self.write_started.set()
        if not self.release_write.wait(timeout=2.0):
            raise OSError("injected blocked stdin timeout")
        if self.broken:
            raise BrokenPipeError("injected broken pipe")
        self.writes.append(value)
        return len(value)

    def flush(self) -> None:
        if self.closed:
            raise ValueError("stdin is closed")

    def close(self) -> None:
        self.closed = True


class FakeProcess:
    def __init__(self, stdin: FakeStdin) -> None:
        self.stdin = stdin
        self.stdout = None
        self.stderr = io.StringIO("")
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        if self.returncode is None:
            self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            raise bridge.subprocess.TimeoutExpired("fake-sidecar", timeout)
        return self.returncode


def make_client(
    stdin: FakeStdin,
    diagnostics: list[str],
    *,
    queue_capacity: int = bridge.SIDECAR_QUEUE_CAPACITY,
) -> tuple[bridge.SidecarClient, FakeProcess]:
    process = FakeProcess(stdin)
    with mock.patch.object(bridge.subprocess, "Popen", return_value=process):
        client = bridge.SidecarClient(
            python=Path(sys.executable),
            script=bridge.DEFAULT_SIDECAR_SCRIPT,
            on_unavailable=diagnostics.append,
            queue_capacity=queue_capacity,
        )
    return client, process


class SidecarClientContractTests(unittest.TestCase):
    def test_event_forwarding_is_utf8_jsonl_on_daemon_writer(self) -> None:
        stdin = FakeStdin()
        diagnostics: list[str] = []
        client, _ = make_client(stdin, diagnostics)
        line = '{"type":"recommendation","hero":"阿狸"}'
        client.offer(line)
        self.assertTrue(stdin.write_started.wait(timeout=1.0))
        deadline = time.monotonic() + 1.0
        while not stdin.writes and time.monotonic() < deadline:
            time.sleep(0.001)
        client.close(timeout_seconds=0.1)
        self.assertEqual(stdin.writes, [line + "\n"])
        self.assertEqual(diagnostics, [])

    def test_queue_full_never_blocks_and_disables_once(self) -> None:
        stdin = FakeStdin(block=True)
        diagnostics: list[str] = []
        client, process = make_client(stdin, diagnostics, queue_capacity=1)
        client.offer('{"seq":1}')
        self.assertTrue(stdin.write_started.wait(timeout=1.0))
        client.offer('{"seq":2}')
        started = time.monotonic()
        client.offer('{"seq":3}')
        elapsed = time.monotonic() - started
        client.offer('{"seq":4}')
        stdin.release_write.set()
        client.close(timeout_seconds=0.1)
        self.assertLess(elapsed, 0.1)
        self.assertFalse(client.enabled)
        self.assertEqual(diagnostics, ["event queue is full"])
        self.assertTrue(process.terminated)

    def test_child_exit_disables_once_without_raising(self) -> None:
        stdin = FakeStdin()
        diagnostics: list[str] = []
        client, process = make_client(stdin, diagnostics)
        process.returncode = 7
        client.offer('{"seq":1}')
        client.offer('{"seq":2}')
        client.close(timeout_seconds=0.1)
        self.assertEqual(diagnostics, ["process exited with code 7"])
        self.assertFalse(client.enabled)

    def test_broken_pipe_isolated_from_bridge_thread(self) -> None:
        stdin = FakeStdin(broken=True)
        diagnostics: list[str] = []
        client, process = make_client(stdin, diagnostics)
        client.offer('{"seq":1}')
        deadline = time.monotonic() + 1.0
        while not diagnostics and time.monotonic() < deadline:
            time.sleep(0.001)
        client.offer('{"seq":2}')
        client.close(timeout_seconds=0.1)
        self.assertEqual(len(diagnostics), 1)
        self.assertIn("stdin unavailable", diagnostics[0])
        self.assertTrue(process.terminated)


class IntegrationSurfaceTests(unittest.TestCase):
    def test_bridge_cli_defaults_to_sidecar_enabled(self) -> None:
        args = bridge._parser().parse_args(["--hero", "阿狸"])
        self.assertFalse(args.no_sidecar)
        self.assertEqual(args.sidecar_script, bridge.DEFAULT_SIDECAR_SCRIPT)
        self.assertFalse(args.collect_samples)
        self.assertFalse(args.no_force_recognition_hotkey)
        self.assertIsNone(args.capture_backend)
        self.assertIsNone(args.lcu_context)

    def test_powershell_exposes_independent_switches(self) -> None:
        source = RUNNER_PATH.read_text(encoding="utf-8")
        expected = {
            "$CollectSamples": "--collect-samples",
            "$NoSidecar": "--no-sidecar",
            "$NoForceRecognitionHotkey": "--no-force-recognition-hotkey",
            "$NoHotkeys": "--no-hotkeys",
        }
        for parameter, cli_switch in expected.items():
            with self.subTest(parameter=parameter):
                self.assertIn(f"[switch]{parameter}", source)
                match = re.search(
                    rf"if \({re.escape(parameter)}\) \{{(?P<body>.*?)\}}",
                    source,
                    flags=re.DOTALL,
                )
                self.assertIsNotNone(match)
                assert match is not None
                self.assertIn(cli_switch, match.group("body"))
        no_hotkeys = re.search(
            r"if \(\$NoHotkeys\) \{(?P<body>.*?)\}", source, flags=re.DOTALL
        )
        assert no_hotkeys is not None
        self.assertNotIn("--collect-samples", no_hotkeys.group("body"))
        self.assertNotIn("--no-sidecar", no_hotkeys.group("body"))
        self.assertNotIn(
            "--no-force-recognition-hotkey", no_hotkeys.group("body")
        )

    def test_powershell_live_parameter_sets_forward_live_only_switches(self) -> None:
        bridge_path = str(
            WORKSPACE_ROOT / "scripts" / "phase3" / "realtime_recommendation.py"
        )
        contract_only_title = "Phase4 contract-only synthetic window"
        cases = {
            "WindowTitle": (
                {"WindowTitle": contract_only_title},
                ["--window-title", contract_only_title],
            ),
            "Hwnd": ({"Hwnd": 4242}, ["--hwnd", "4242"]),
        }
        for name, (mode_parameters, expected_mode_argv) in cases.items():
            with self.subTest(parameter_set=name):
                result = invoke_powershell_runner(
                    {
                        "Hero": "Ahri",
                        "MaxSeconds": 60,
                        "CaptureBackend": "desktop",
                        "CollectSamples": True,
                        "NoForceRecognitionHotkey": True,
                        **mode_parameters,
                    }
                )
                self.assertEqual(result.returncode, 0, msg=result.stderr)
                self.assertEqual(
                    captured_runner_argv(result),
                    [
                        "-3.11",
                        "-B",
                        bridge_path,
                        "--hero",
                        "Ahri",
                        "--max-seconds",
                        "60",
                        *expected_mode_argv,
                        "--capture-backend",
                        "desktop",
                        "--lcu-context",
                        "auto",
                        "--collect-samples",
                        "--no-force-recognition-hotkey",
                    ],
                )

    def test_powershell_replay_default_omits_live_only_switches(self) -> None:
        replay_path = str(Path(__file__).resolve())
        result = invoke_powershell_runner(
            {"Hero": "Ahri", "MaxSeconds": 60, "Replay": replay_path}
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        argv = captured_runner_argv(result)
        self.assertIn("--replay", argv)
        self.assertNotIn("--collect-samples", argv)
        self.assertNotIn("--no-force-recognition-hotkey", argv)
        self.assertNotIn("--lcu-context", argv)

    def test_powershell_replay_rejects_live_only_switches_during_binding(self) -> None:
        replay_path = str(Path(__file__).resolve())
        for parameter, value in (
            ("CollectSamples", True),
            ("NoForceRecognitionHotkey", True),
            ("CaptureBackend", "desktop"),
            ("LcuContext", "off"),
            ("LeagueRoot", str(WORKSPACE_ROOT)),
        ):
            with self.subTest(parameter=parameter):
                result = invoke_powershell_runner(
                    {
                        "Hero": "Ahri",
                        "Replay": replay_path,
                        parameter: value,
                    }
                )
                self.assertEqual(result.returncode, 23)
                self.assertNotIn("RUNNER_ARGV=", result.stdout)
                self.assertRegex(
                    result.stderr,
                    r"RUNNER_BINDING_ERROR=.*(?:AmbiguousParameterSet|ParameterSet)",
                )

    def test_runner_uses_literal_backend_argv_not_process_environment(self) -> None:
        source = RUNNER_PATH.read_text(encoding="utf-8")
        self.assertIn("@('--capture-backend', $CaptureBackend)", source)
        self.assertIn("@('--lcu-context', $LcuContext)", source)
        self.assertNotIn("LOL_ASSISTANT_CAPTURE_BACKEND", source)
        result = invoke_powershell_runner(
            {
                "Hero": "Ahri",
                "MaxSeconds": 60,
                "Hwnd": 4242,
                "CaptureBackend": "wgc",
            }
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        argv = captured_runner_argv(result)
        index = argv.index("--capture-backend")
        self.assertEqual(argv[index + 1], "wgc")

    def test_cmake_registers_phase4_contracts_in_generated_registry(self) -> None:
        cmake = shutil.which("cmake")
        self.assertIsNotNone(cmake, "cmake is required for the registry contract test")
        with tempfile.TemporaryDirectory(prefix="phase4-cmake-registry-") as build_dir:
            result = subprocess.run(
                [
                    cmake,
                    "-S",
                    str(WORKSPACE_ROOT),
                    "-B",
                    build_dir,
                    "-G",
                    "Visual Studio 17 2022",
                    "-A",
                    "x64",
                    "-DBUILD_TESTING=ON",
                ],
                cwd=WORKSPACE_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(
                result.returncode,
                0,
                msg=f"stdout={result.stdout}\nstderr={result.stderr}",
            )
            registry = Path(build_dir) / "CTestTestfile.cmake"
            self.assertTrue(registry.is_file(), f"missing generated registry: {registry}")
            registered = set(
                re.findall(
                    r"add_test\(\[=\[(phase4_[A-Za-z0-9_]+)\]=\]",
                    registry.read_text(encoding="utf-8"),
                )
            )
            self.assertEqual(
                registered,
                {"phase4_sidecar_contract", "phase4_integration_contract"},
            )

    def test_bridge_and_sidecar_paths_have_no_input_or_activation_api(self) -> None:
        paths = [
            WORKSPACE_ROOT / "scripts" / "phase3" / "realtime_recommendation.py",
            WORKSPACE_ROOT / "scripts" / "phase4" / "sidecar_window.py",
            WORKSPACE_ROOT / "scripts" / "run_recommendation.ps1",
        ]
        forbidden = (
            "SetForegroundWindow",
            "SetActiveWindow",
            "SetFocus",
            "BringWindowToTop",
            "AttachThreadInput",
            "SendInput",
            "keybd_event",
            "mouse_event",
            "RegisterHotKey",
        )
        for path in paths:
            source = path.read_text(encoding="utf-8")
            for api in forbidden:
                with self.subTest(path=path.name, api=api):
                    self.assertNotIn(api, source)


if __name__ == "__main__":
    unittest.main()
