from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from context_cache.cli import main
from context_cache.cli_commands import COMMAND_HANDLERS, execute_command
from context_cache.cli_config import create_cache
from context_cache.cli_parser import build_parser


class CLIParserTests(unittest.TestCase):
    def test_commands_and_aliases_resolve_to_canonical_handlers(self) -> None:
        cases = [
            (["quickstart"], "quickstart"),
            (["doctor"], "doctor"),
            (["stats"], "stats"),
            (["purge"], "purge"),
            (["shelve", "text"], "shelve"),
            (["put", "text"], "shelve"),
            (["shelve-document", "-"], "shelve-document"),
            (["ingest", "-"], "shelve-document"),
            (["consult", "subject"], "consult"),
            (["query", "subject"], "consult"),
            (["desk", "subject"], "desk"),
            (["context", "subject"], "desk"),
            (["watch-desk", "subject"], "watch-desk"),
            (["watch", "subject"], "watch-desk"),
            (["discard", "record"], "discard"),
            (["delete", "record"], "discard"),
            (["serve"], "serve"),
        ]
        parser = build_parser()
        for argv, expected in cases:
            with self.subTest(argv=argv):
                args = parser.parse_args(argv)
                self.assertEqual(args.command_handler, expected)

    def test_branded_environment_variables_take_precedence(self) -> None:
        environment = {
            "LIBRARY_OF_CONTEXT_DB": "branded.sqlite",
            "CONTEXT_CACHE_DB": "legacy.sqlite",
            "LIBRARY_OF_CONTEXT_REDIS_URL": "redis://branded:6379/2",
            "CONTEXT_CACHE_REDIS_URL": "redis://legacy:6379/1",
        }
        with patch.dict(os.environ, environment, clear=False):
            args = build_parser().parse_args(["stats"])
        self.assertEqual(args.db, "branded.sqlite")
        self.assertEqual(args.redis_url, "redis://branded:6379/2")


class CLIConfigurationTests(unittest.TestCase):
    def test_cache_configuration_maps_parsed_options(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "library.sqlite"
            args = build_parser().parse_args(
                [
                    "--db",
                    str(database),
                    "--namespace",
                    "project-a",
                    "--ram-mb",
                    "1",
                    "--no-redis",
                    "stats",
                ]
            )
            cache = create_cache(args)
            try:
                self.assertEqual(cache.namespace, "project-a")
                self.assertEqual(cache.ram.max_bytes, 1024 * 1024)
                self.assertIsNone(cache.redis)
                self.assertEqual(type(cache.embedder).__name__, "HashingEmbedder")
            finally:
                cache.close()


class CLICommandTests(unittest.TestCase):
    def test_aliases_keep_existing_json_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "library.sqlite"
            common = ["--db", str(database), "--no-redis"]

            output = io.StringIO()
            with redirect_stdout(output):
                result = main(
                    common
                    + [
                        "put",
                        "Use a canary deployment wave.",
                        "--id",
                        "decision",
                        "--metadata",
                        '{"kind":"decision"}',
                    ]
                )
            self.assertEqual(result, 0)
            record = json.loads(output.getvalue())
            self.assertEqual(record["id"], "decision")
            self.assertEqual(record["metadata"], {"kind": "decision"})

            output = io.StringIO()
            with redirect_stdout(output):
                result = main(common + ["query", "canary deployment", "--json"])
            self.assertEqual(result, 0)
            hits = json.loads(output.getvalue())
            self.assertEqual(hits[0]["record"]["id"], "decision")

            output = io.StringIO()
            with redirect_stdout(output):
                result = main(common + ["delete", "decision"])
            self.assertEqual(result, 0)
            self.assertEqual(json.loads(output.getvalue()), {"deleted": True})

    def test_cache_closes_when_a_handler_fails(self) -> None:
        class FailingCache:
            closed = False

            def close(self) -> None:
                self.closed = True

        cache = FailingCache()
        args = build_parser().parse_args(["stats"])

        def fail(_args: object, _cache: object) -> None:
            raise RuntimeError("handler failed")

        with (
            patch("context_cache.cli_commands.create_cache", return_value=cache),
            patch.dict(COMMAND_HANDLERS, {"stats": fail}),
        ):
            with self.assertRaisesRegex(RuntimeError, "handler failed"):
                execute_command(args)
        self.assertTrue(cache.closed)

    def test_quickstart_does_not_open_the_configured_cache(self) -> None:
        args = build_parser().parse_args(["quickstart"])
        with (
            patch("context_cache.cli_commands.create_cache") as create,
            patch("context_cache.cli_commands.run_quickstart", return_value=7) as run,
        ):
            self.assertEqual(execute_command(args), 7)
        create.assert_not_called()
        run.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
