from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import tomllib
import unittest
from pathlib import Path

from context_cache.mcp_server import SERVER_INSTRUCTIONS, TOOLS
from library_of_context import GovernedTextAgent, LibraryOfContext

ROOT = Path(__file__).resolve().parents[1]


def _source_environment() -> dict[str, str]:
    environment = os.environ.copy()
    current = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        str(ROOT) if not current else str(ROOT) + os.pathsep + current
    )
    return environment


class NormalUserCLITests(unittest.TestCase):
    def test_installed_package_imports_outside_the_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    "import library_of_context; print(library_of_context.__version__)",
                ],
                cwd=directory,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout.strip(), "0.3.0")

    def test_quickstart_is_disposable_and_service_free(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.run(
                [sys.executable, "-m", "library_of_context", "quickstart"],
                cwd=directory,
                env=_source_environment(),
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("quickstart: PASS", completed.stdout)
            self.assertIn("cloud services used: no", completed.stdout)
            self.assertIn("test data retained: no", completed.stdout)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_documented_doctor_command_uses_an_isolated_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "data" / "library.sqlite"
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "library_of_context",
                    "--no-redis",
                    "--db",
                    str(database),
                    "doctor",
                ],
                cwd=directory,
                env=_source_environment(),
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            report = json.loads(completed.stdout)
            self.assertEqual(report["status"]["sqlite"], "ok")
            self.assertEqual(report["status"]["ram"], "ok")
            self.assertEqual(report["status"]["redis"], "disabled")
            self.assertTrue(database.exists())


class ProcessRestartAcceptanceTests(unittest.TestCase):
    def test_governed_thread_recovers_in_a_new_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "library.sqlite"
            record_script = textwrap.dedent(
                """
                import sys
                from library_of_context import LibraryOfContext

                with LibraryOfContext(sys.argv[1], redis_url="") as library:
                    with library.open_context_governor(
                        "restart-thread",
                        token_budget=500,
                        recent_token_budget=120,
                        protected_token_budget=80,
                        start_worker=False,
                    ) as context:
                        context.protect(
                            "Production requires a canary.",
                            event_id="policy",
                        )
                        context.prepare("Remember the policy.", event_id="user-1")
                        context.commit("Policy recorded.", event_id="assistant-1")
                """
            )
            recover_script = textwrap.dedent(
                """
                import json
                import sys
                from library_of_context import LibraryOfContext

                with LibraryOfContext(sys.argv[1], redis_url="") as library:
                    with library.open_context_governor(
                        "restart-thread",
                        token_budget=500,
                        recent_token_budget=120,
                        protected_token_budget=80,
                    ) as context:
                        if not context.flush(timeout=5):
                            raise RuntimeError("pending durable work did not recover")
                        prompt = context.prepare(
                            "Which production policy applies?",
                            event_id="user-2",
                        )
                        if not context.flush(timeout=5):
                            raise RuntimeError("new work did not become searchable")
                        print(json.dumps({
                            "messages": prompt.messages,
                            "token_count": prompt.token_count,
                            "token_budget": prompt.token_budget,
                            "watermarks": context.status()["watermarks"],
                        }))
                """
            )
            for script in (record_script, recover_script):
                completed = subprocess.run(
                    [sys.executable, "-I", "-c", script, str(database)],
                    cwd=directory,
                    capture_output=True,
                    text=True,
                    timeout=20,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertLessEqual(result["token_count"], result["token_budget"])
            self.assertTrue(
                any("canary" in message["content"] for message in result["messages"])
            )
            self.assertEqual(result["watermarks"]["recorded_through"], 4)
            self.assertEqual(result["watermarks"]["indexed_through"], 4)
            self.assertEqual(result["watermarks"]["pending_events"], 0)


class ExistingAgentIntegrationTests(unittest.TestCase):
    def test_text_agent_wraps_every_call_in_a_bounded_envelope(self) -> None:
        calls: list[list[dict[str, str]]] = []

        def fake_model(messages: list[dict[str, str]]) -> str:
            calls.append([dict(message) for message in messages])
            return "The model received only its governed envelope."

        with tempfile.TemporaryDirectory() as directory:
            with LibraryOfContext(
                Path(directory) / "library.sqlite", redis_url=""
            ) as library:
                with library.open_context_governor(
                    "existing-agent",
                    collection="project-a",
                    token_budget=320,
                    recent_token_budget=96,
                    protected_token_budget=48,
                ) as context:
                    agent = GovernedTextAgent(
                        context,
                        fake_model,
                        system_prompt="Use the bounded project context.",
                    )
                    for turn in range(12):
                        response = agent.turn(
                            f"Turn {turn}: " + ("bounded context " * 25),
                            turn_id=f"turn-{turn}",
                        )
                        self.assertIn("governed envelope", response)
                        assert agent.last_prompt is not None
                        self.assertLessEqual(
                            agent.last_prompt.token_count,
                            agent.last_prompt.token_budget,
                        )
                    self.assertTrue(context.flush(timeout=5))
                    self.assertEqual(
                        library.store.count_thread_events(
                            "project-a", "existing-agent"
                        ),
                        24,
                    )
        self.assertEqual(len(calls), 12)
        self.assertTrue(all(calls))

    def test_stable_turn_id_makes_persistence_idempotent(self) -> None:
        def fake_model(_: list[dict[str, str]]) -> str:
            return "same response"

        with tempfile.TemporaryDirectory() as directory:
            with LibraryOfContext(
                Path(directory) / "library.sqlite", redis_url=""
            ) as library:
                with library.open_context_governor("retry") as context:
                    agent = GovernedTextAgent(context, fake_model)
                    agent.turn("same request", turn_id="request-1")
                    agent.turn("same request", turn_id="request-1")
                    self.assertEqual(
                        library.store.count_thread_events("default", "retry"), 2
                    )

    def test_projects_and_agent_threads_remain_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with LibraryOfContext(
                Path(directory) / "library.sqlite", redis_url=""
            ) as library:
                with library.open_context_governor(
                    "thread-1", collection="project-a", start_worker=False
                ) as first:
                    first.record("user", "Project A private fact.")
                with library.open_context_governor(
                    "thread-1", collection="project-b", start_worker=False
                ) as second:
                    second.record("user", "Project B private fact.")
                self.assertEqual(
                    library.store.count_thread_events("project-a", "thread-1"), 1
                )
                self.assertEqual(
                    library.store.count_thread_events("project-b", "thread-1"), 1
                )
                first_event = library.store.list_thread_events("project-a", "thread-1")[
                    0
                ]
                second_event = library.store.list_thread_events(
                    "project-b", "thread-1"
                )[0]
                self.assertNotEqual(first_event.content, second_event.content)


class CodexIntegrationContractTests(unittest.TestCase):
    def test_cooperative_profile_matches_the_server_and_is_isolated(self) -> None:
        path = ROOT / "integrations" / "codex-config.toml.example"
        document = tomllib.loads(path.read_text(encoding="utf-8"))
        server = document["mcp_servers"]["library_of_context"]
        configured = set(server["enabled_tools"])
        available = {tool["name"] for tool in TOOLS}
        cooperative = {
            "library_shelve",
            "library_shelve_document",
            "library_consult",
            "library_desk_refresh",
            "library_desk_get",
            "library_desk_watch",
            "library_desk_stop",
            "library_stats",
        }
        self.assertEqual(configured, cooperative)
        self.assertTrue(configured.issubset(available))
        self.assertIn("--no-redis", server["args"])
        self.assertIn("--namespace", server["args"])
        self.assertNotIn("--ram-mb", server["args"])
        self.assertIn(".venv/Scripts/python.exe", server["command"])
        self.assertNotIn("data/library-of-context.sqlite", server["args"])

    def test_server_instructions_front_load_both_integration_modes(self) -> None:
        decision_prefix = SERVER_INSTRUCTIONS[:512]
        self.assertIn("library_desk_refresh", decision_prefix)
        self.assertIn("library_context_prepare", decision_prefix)
        self.assertIn("library_context_commit", decision_prefix)
        self.assertIn("does not replace the host transcript", decision_prefix)

    def test_agent_template_requires_thread_identity_and_explains_watch(self) -> None:
        instructions = (ROOT / "integrations" / "AGENTS.library.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("unique, stable `session_id`", instructions)
        self.assertIn("does not push new context", instructions)
        self.assertIn("does not intercept", instructions)


if __name__ == "__main__":
    unittest.main()
