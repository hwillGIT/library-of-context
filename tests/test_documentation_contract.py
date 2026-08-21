import importlib
import json
import os
import subprocess
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DocumentationContractTests(unittest.TestCase):
    def test_redis_installer_owns_a_dedicated_authenticated_service(self) -> None:
        installer = ROOT / "scripts" / "install-local-redis.ps1"
        text = installer.read_text(encoding="utf-8")
        self.assertIn('$ServiceName = "library-of-context-redis"', text)
        self.assertIn("[int]$Port = 6380", text)
        self.assertIn("requirepass $Password", text)
        self.assertIn('$ConfigDirectory = "/etc/library-of-context"', text)
        self.assertIn('$SecretPath = "$ConfigDirectory/redis.secret"', text)
        self.assertNotIn("CONFIG SET", text)
        self.assertNotIn("CONFIG REWRITE", text)

        if os.name != "nt":
            return
        installer_argument = str(installer).replace("'", "''")
        parsed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "$errors=$null; "
                "[System.Management.Automation.Language.Parser]::ParseFile("
                f"'{installer_argument}', [ref]$null, [ref]$errors) | Out-Null; "
                "if ($errors.Count) { $errors | ForEach-Object { "
                "[Console]::Error.WriteLine($_) }; exit 1 }",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(parsed.returncode, 0, parsed.stderr)

    def test_shared_runtime_evidence_manifest_resolves_every_contract(self) -> None:
        manifest_path = ROOT / "docs" / "evidence" / "shared-runtime-contracts.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        contracts = manifest["contracts"]
        self.assertEqual(
            [contract["invariant"] for contract in contracts],
            list(range(1, 15)),
        )
        self.assertIn(
            "Shared-runtime contract evidence: evidence/README.md",
            (ROOT / "mkdocs.yml").read_text(encoding="utf-8"),
        )

        for contract in contracts:
            self.assertTrue(contract["boundary"])
            self.assertTrue(contract["tests"])
            for test_name in contract["tests"]:
                with self.subTest(invariant=contract["invariant"], test=test_name):
                    module_name, class_name, method_name = test_name.rsplit(".", 2)
                    module = importlib.import_module(module_name)
                    test_class = getattr(module, class_name)
                    self.assertTrue(callable(getattr(test_class, method_name)))

    def test_timeless_prose_rule_and_skill_are_discoverable(self) -> None:
        skill = (
            ROOT / ".agents" / "skills" / "write-timeless-technical-prose" / "SKILL.md"
        )
        metadata = skill.parent / "agents" / "openai.yaml"

        self.assertTrue(skill.is_file())
        self.assertTrue(metadata.is_file())
        skill_text = skill.read_text(encoding="utf-8")
        self.assertIn("name: write-timeless-technical-prose", skill_text)
        self.assertIn("one coherent editorial present", skill_text)
        self.assertIn("Remove temporal provenance", skill_text)
        self.assertNotIn("TODO", skill_text)

        for source in ("AGENTS.md", "CONTRIBUTING.md", "docs/contributing.md"):
            with self.subTest(source=source):
                text = (ROOT / source).read_text(encoding="utf-8")
                self.assertIn("write-timeless-technical-prose/SKILL.md", text)
                self.assertRegex(
                    " ".join(text.split()),
                    r"(?:relative to (?:another|other content)|edit history)",
                )

    def test_related_work_is_in_site_navigation(self) -> None:
        related_work = ROOT / "docs" / "RELATED_WORK.md"

        self.assertTrue(related_work.is_file())
        self.assertIn(
            "- Related work and landscape: RELATED_WORK.md",
            (ROOT / "mkdocs.yml").read_text(encoding="utf-8"),
        )

    def test_related_work_has_required_local_links(self) -> None:
        text = (ROOT / "docs" / "RELATED_WORK.md").read_text(encoding="utf-8")

        for target in (
            "STATUS.md",
            "architecture.md",
            "WHY_THE_ROADMAP.md",
            "roadmap.md",
            "DECISION_BRIEF_TEMPLATE.md",
        ):
            with self.subTest(target=target):
                self.assertIn(f"]({target})", text)
                self.assertTrue((ROOT / "docs" / target).is_file())

        for image in (
            "context-management-landscape.png",
            "research-synthesis.png",
        ):
            with self.subTest(image=image):
                self.assertIn(f"]({image})", text)
                self.assertTrue((ROOT / "docs" / image).is_file())

    def test_entry_documents_link_to_related_work(self) -> None:
        expected_links = {
            "README.md": "docs/RELATED_WORK.md",
            "ARCHITECTURE.md": "docs/RELATED_WORK.md",
            "ROADMAP.md": "docs/RELATED_WORK.md",
            "docs/index.md": "RELATED_WORK.md",
            "docs/architecture.md": "RELATED_WORK.md",
            "docs/roadmap.md": "RELATED_WORK.md",
        }

        for source, target in expected_links.items():
            with self.subTest(source=source):
                text = (ROOT / source).read_text(encoding="utf-8")
                self.assertIn(f"]({target})", text)

    def test_codex_profiles_define_one_direct_or_daemon_runtime_owner(self) -> None:
        direct_path = ROOT / "integrations" / "codex-config.toml.example"
        daemon_path = ROOT / "integrations" / "codex-daemon-config.toml.example"
        self.assertTrue(direct_path.is_file())
        self.assertTrue(daemon_path.is_file())

        direct = tomllib.loads(direct_path.read_text(encoding="utf-8"))
        daemon = tomllib.loads(daemon_path.read_text(encoding="utf-8"))
        direct_server = direct["mcp_servers"]["library_of_context"]
        daemon_server = daemon["mcp_servers"]["library_of_context"]

        self.assertIn("--db", direct_server["args"])
        self.assertNotIn("--daemon-url", direct_server["args"])
        self.assertIn("--daemon-url", daemon_server["args"])
        self.assertIn("--daemon-token-file", daemon_server["args"])
        self.assertIn("http://127.0.0.1:8765", daemon_server["args"])
        self.assertIn("--namespace", daemon_server["args"])
        self.assertIn("replace-with-your-project-slug", daemon_server["args"])
        self.assertIn("--daemon-timeout-seconds", daemon_server["args"])
        timeout_index = daemon_server["args"].index("--daemon-timeout-seconds")
        self.assertEqual(
            float(daemon_server["args"][timeout_index + 1]),
            float(daemon_server["tool_timeout_sec"]),
        )
        for local_option in ("--db", "--redis-url", "--no-redis"):
            self.assertNotIn(local_option, daemon_server["args"])
        self.assertEqual(
            set(direct_server["enabled_tools"]),
            set(daemon_server["enabled_tools"]),
        )

        integration_text = (ROOT / "integrations" / "README.md").read_text(
            encoding="utf-8"
        )
        agent_text = (ROOT / "docs" / "ADD_TO_YOUR_AGENT.md").read_text(
            encoding="utf-8"
        )
        for text in (integration_text, agent_text):
            normalized = " ".join(text.split())
            self.assertIn("codex-config.toml.example", normalized)
            self.assertIn("codex-daemon-config.toml.example", normalized)
            self.assertIn("ThreadKey(collection, session_id)", normalized)
            self.assertRegex(
                normalized, r"no implicit (?:default session|session default)"
            )
            self.assertRegex(
                normalized,
                r"Run exactly one daemon owner for (?:each|a) database\.",
            )
            self.assertRegex(normalized, r"10 (?:MiB|mebibytes)")
            self.assertRegex(normalized, r"(?:indeterminate|unknown) result")
            self.assertIn("bearer token", normalized.casefold())

        self.assertRegex(
            integration_text,
            r"The Codex MCP profile cannot (?:obtain or persist|read or store) the "
            r"internal Codex thread identifier\.",
        )
        self.assertRegex(
            integration_text,
            r"(?:The shelving tools|Storage tools) create project[- ](?:scoped|visible) "
            r"books by default\.",
        )
        instructions = (ROOT / "integrations" / "AGENTS.library.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("visible to every thread", instructions)
        self.assertIn("<project-slug>:<random-uuid>", instructions)

    def test_chat_identity_and_shared_runtime_are_explained_consistently(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        architecture = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")
        status = (ROOT / "docs" / "STATUS.md").read_text(encoding="utf-8")
        navigation = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")

        for term in ("context event", "context record", "book"):
            with self.subTest(term=term):
                self.assertIn(f"A **{term}**", readme)
        normalized_readme = " ".join(readme.split())
        self.assertRegex(
            normalized_readme,
            r"(?:does not store a second book (?:entity|item)|not a separate stored item)",
        )
        self.assertIn("ThreadKey(collection, session_id)", readme)
        self.assertRegex(
            normalized_readme,
            r"A chat does not receive a (?:copy of|separate) SQLite database",
        )
        self.assertRegex(
            normalized_readme,
            r"One `LibraryRuntime` owns (?:those components|these process resources)",
        )

        for component in (
            "`LibraryRuntime`",
            "`OutboxIndexer`",
            "ThreadKey(collection, session_id)",
            "`--daemon-url`",
        ):
            with self.subTest(component=component):
                self.assertIn(component, architecture)

        self.assertIn("Shared `LibraryRuntime`", status)
        self.assertIn("Contributor QA workflow: DEVELOPMENT_WORKFLOW.md", navigation)
        self.assertIn(
            "ADR 0001 — thread scope and shared runtime: "
            "adr/0001-thread-scope-and-shared-runtime.md",
            navigation,
        )

    def test_runtime_ownership_and_disposable_tiers_match_the_contract(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        architecture = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")
        getting_started = (ROOT / "docs" / "GETTING_STARTED.md").read_text(
            encoding="utf-8"
        )
        normalized_readme = " ".join(readme.split())
        normalized_architecture = " ".join(architecture.split())
        normalized_getting_started = " ".join(getting_started.split())

        self.assertRegex(
            normalized_readme,
            r"Every Library runtime (?:acquires|takes) the database owner lock",
        )
        self.assertRegex(
            normalized_architecture,
            r"(?:Only one runtime may own|permits one process owner for) (?:a|each) "
            r"database",
        )
        self.assertRegex(
            normalized_getting_started,
            r"Two embedded processes cannot share one database",
        )

        self.assertRegex(
            normalized_readme,
            r"marked, (?:token-capped|shortened) RAM (?:projection|copy)",
        )
        self.assertRegex(
            normalized_readme,
            r"(?:SQLite retains|Keep) the complete event (?:in SQLite)?",
        )
        self.assertNotIn("One oversized event may remain resident", readme)
        self.assertNotIn("one oversized event may remain", architecture)

        for text in (readme, architecture, getting_started):
            with self.subTest(document=text[:40]):
                self.assertIn("runtime", text.casefold())
                self.assertNotIn("cross-process hot caching", text)
        self.assertRegex(
            normalized_readme,
            r"(?:starts cold after process restart|restarted process starts with an empty cache)",
        )
        self.assertRegex(
            normalized_architecture,
            r"(?:old payloads are ignored|Old values expire and remain unused)",
        )


if __name__ == "__main__":
    unittest.main()
