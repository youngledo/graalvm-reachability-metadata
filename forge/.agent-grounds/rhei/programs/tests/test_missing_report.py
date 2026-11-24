# Copyright and related rights waived via CC0
#
# You should have received a copy of the CC0 legalcode along with this
# work. If not, see <http://creativecommons.org/publicdomain/zero/1.0/>.

"""A report build that fails inside `generateLibraryStats` is judged as that build, on every route.

§AR-forge-missing-report, §FS-local-ci-equivalent-verification.1.1

The task passes and records `N/A`, which is neither zero coverage nor a pass.
A report without calls, and a previous version without a report, still are what
they were.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import unittest
from typing import Any

from support import COORDINATE, play_fix
from test_audit import FinalizedRun

import forge_run  # noqa: E402
import gates  # noqa: E402
from dispatcher.run_launch import intervention_comment  # noqa: E402
from exit_codes import AGENT_WORK, INFRASTRUCTURE, OK, RETURN_FINALIZE, STILL_FAILING  # noqa: E402
from run_state import Chunk, RunState, read_json, runtime_path, save  # noqa: E402

REPORT: str = f"./gradlew generateDynamicAccessReport -Pcoordinates={COORDINATE}"
OUT_OF_MEMORY: str = "generateDynamicAccessReport failed: the Native Image build ran out of memory"
PREVIOUS_STATS: str = "stats/org.example/lib/1.5.0/stats.json"
CHUNK: str = "chunk"
ROUTES: tuple[str, ...] = ("library-new-request", "library-update-request", "fails-java-run", CHUNK)


def report(covered: int, total: int) -> dict[str, Any]:
    return {"coveredCalls": covered, "totalCalls": total, "coverageRatio": round(covered / max(total, 1), 6),
            "breakdown": {}}


class MissingReportTest(FinalizedRun):

    def ready(self, route: str) -> None:
        """Bring a fresh fake run of the route up to `finalize`."""
        self.fake.close()
        self.setUp()
        self.repaired("library-new-request" if route == CHUNK else route)
        if route == CHUNK:
            state: RunState = self.state
            state.chunk = Chunk(active=True, budget=15)
            save(self.fake.workspace, state)

    def recorded_outcome(self) -> dict[str, Any]:
        return read_json(runtime_path(self.fake.workspace, forge_run.OUTCOME))

    def assert_out_of_memory(self, sender: str) -> None:
        outcome: dict[str, Any] = self.recorded_outcome()
        self.assertEqual(("human-intervention", OUT_OF_MEMORY, sender),
                         (outcome["outcome"], outcome["reason"], outcome["sender"]))
        self.assertEqual([REPORT], [failure["command"] for failure in outcome["failures"]])
        comment: str = intervention_comment(outcome)
        self.assertIn(f"Forge could not finish this issue: {OUT_OF_MEMORY}.", comment)
        self.assertIn("Terminating due to java.lang.OutOfMemoryError: GC overhead limit exceeded", comment)
        self.assertNotIn("covers no dynamic-access call", comment)

    def test_a_failed_report_build_is_the_failing_check_on_every_route(self) -> None:
        for route in ROUTES:
            with self.subTest(route):
                self.ready(route)
                self.fake.control({"reportFails": "build"})
                self.assertEqual(AGENT_WORK, self.step("finalize"))
                request: str = self.read("fix-request.md")
                self.assertIn(f"## Failing commands\n- {REPORT}\n", request)
                self.assertIn("Unresolved type found: org.example.missing.Location", request)
                self.assertNotIn(gates.COVERAGE_GATE, request)
                # The fix made the report build pass, so the gates judge its numbers.
                self.fake.control({"reportFails": "", "stats": report(6, 6)})
                play_fix(self.fake.workspace)
                self.assertEqual(RETURN_FINALIZE, self.step("fix-route"))
                self.assertEqual(OK, self.step("finalize"))

    def test_an_out_of_memory_report_build_needs_a_human_on_every_route(self) -> None:
        for route in ROUTES:
            with self.subTest(route):
                self.ready(route)
                self.fake.control({"reportFails": "memory"})
                self.assertEqual(STILL_FAILING, self.step("finalize"))
                self.assertEqual((0, 1), (self.state.finalize_requests, self.gradle_calls("generateLibraryStats")))
                self.assert_out_of_memory("finalize")

    def test_an_out_of_memory_report_build_after_a_fix_names_the_build(self) -> None:
        self.repaired()
        self.fake.control({"styleFails": "checkstyle"})
        self.assertEqual(AGENT_WORK, self.step("finalize"))
        play_fix(self.fake.workspace)
        self.assertEqual(RETURN_FINALIZE, self.step("fix-route"))
        self.fake.control({"styleFails": "", "reportFails": "memory"})
        self.assertEqual(STILL_FAILING, self.step("finalize"))
        self.assert_out_of_memory("finalize")

    def test_an_out_of_memory_report_build_over_the_audits_edit_names_the_build(self) -> None:
        self.repaired()
        self.audited()
        self.write(os.path.join(self.state.test_dir, "src", "test", "java", "org_example", "CodecTest.java"),
                   "package org_example;\npublic class CodecTest {}\n")
        self.fake.control({"reportFails": "memory"})
        self.assertEqual(STILL_FAILING, self.step("audit"))
        self.assertFalse(self.state.audit_passed)
        self.assert_out_of_memory("audit")

    def test_a_report_build_that_keeps_failing_hands_off_its_output(self) -> None:
        self.repaired()
        self.fake.control({"reportFails": "build"})
        for _ in range(self.state.budgets.finalize_fixes):
            self.assertEqual(AGENT_WORK, self.step("finalize"))
            play_fix(self.fake.workspace)
            self.assertEqual(RETURN_FINALIZE, self.step("fix-route"))
        self.assertEqual(STILL_FAILING, self.step("finalize"))
        outcome: dict[str, Any] = self.recorded_outcome()
        self.assertEqual(("finalize still fails after its fix", REPORT),
                         (outcome["reason"], outcome["failures"][0]["command"]))
        self.assertIn("Unresolved type found", outcome["failures"][0]["output"])

    def test_a_report_build_that_cannot_reach_a_repository_is_retried_on_every_route(self) -> None:
        for route in ROUTES:
            with self.subTest(route):
                self.ready(route)
                self.fake.control({"reportFails": "unreachable"})
                self.assertEqual(INFRASTRUCTURE, self.step("finalize"))
                self.assertEqual(("infrastructure", 0),
                                 (self.recorded_outcome()["kind"], self.state.finalize_requests))

    def test_a_report_build_a_stale_daemon_fails_reruns_once(self) -> None:
        self.repaired()
        self.fake.control({"reportFails": "daemon"})
        self.assertEqual(AGENT_WORK, self.step("finalize"))
        self.assertEqual(2, self.gradle_calls("generateLibraryStats"))

    def test_the_gate_command_never_passes_a_version_without_a_report(self) -> None:
        for route in ROUTES:
            with self.subTest(route):
                self.ready(route)
                self.write("stats/org.example/lib/2.0.0/stats.json",
                           json.dumps({"versions": [{"version": "2.0.0", "dynamicAccess": "N/A"}]}))
                printed: io.StringIO = io.StringIO()
                with contextlib.redirect_stdout(printed):
                    self.assertEqual(1, gates.main([self.fake.workspace]))
                self.assertEqual(f"FAILED {gates.COVERAGE_GATE}: not judged: {COORDINATE} has no dynamic-access "
                                 "report\n", printed.getvalue())

    def test_a_report_without_calls_still_passes(self) -> None:
        """As for a library without dynamic access, or a JAR without bytecode."""
        for route in ("library-new-request", "library-update-request", "fails-java-run"):
            with self.subTest(route):
                self.ready(route)
                self.fake.control({"stats": report(0, 0)})
                self.assertEqual(OK, self.step("finalize"))
                self.assertEqual(AGENT_WORK, self.step("audit"))
                self.assertIn("reports no dynamic-access calls", self.read("fix-request.md"))

    def test_a_chunk_whose_report_covers_nothing_still_fails_the_gate(self) -> None:
        self.ready(CHUNK)
        self.fake.control({"stats": report(0, 6)})
        self.assertEqual(AGENT_WORK, self.step("finalize"))
        self.assertIn(f"{gates.COVERAGE_GATE}: the chunk covers no dynamic-access call",
                      self.read("fix-request.md"))

    def test_a_previous_version_without_a_report_is_no_baseline(self) -> None:
        for route in ("library-update-request", "fails-java-run"):
            with self.subTest(route):
                self.ready(route)
                path: str = self.state.path(PREVIOUS_STATS)
                with open(path, encoding="utf-8") as handle:
                    stats: dict[str, Any] = json.load(handle)
                stats["versions"][0]["dynamicAccess"] = "N/A"
                self.write(PREVIOUS_STATS, json.dumps(stats))
                state: RunState = self.state
                self.assertIsNone(gates.dynamic_access(state, "1.5.0"))
                self.write("stats/org.example/lib/2.0.0/stats.json",
                           json.dumps({"versions": [{"version": "2.0.0", "dynamicAccess": report(1, 9)}]}))
                self.assertIsNone(gates.coverage_breach(state))
                self.write("stats/org.example/lib/2.0.0/stats.json",
                           json.dumps({"versions": [{"version": "2.0.0", "dynamicAccess": report(0, 9)}]}))
                # The repair comparison still needs one covered call; an update has nothing to drop from.
                self.assertEqual(route == "fails-java-run", gates.coverage_breach(state) is not None)


if __name__ == "__main__":
    unittest.main()
