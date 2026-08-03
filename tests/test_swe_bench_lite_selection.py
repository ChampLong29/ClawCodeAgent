import json
import tempfile
import unittest
from pathlib import Path

from tools.select_swe_bench_lite_pilot import PILOT, build_catalog, generate


ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_ROOT = ROOT / "benchmarks" / "swe_bench_lite"


class SweBenchLiteSelectionTests(unittest.TestCase):
    def test_snapshot_contains_exact_dev_split(self):
        snapshot = json.loads(
            (BENCHMARK_ROOT / "snapshot.json").read_text(encoding="utf-8")
        )
        self.assertEqual(snapshot["split"], "dev")
        self.assertEqual(snapshot["row_count"], 23)
        self.assertEqual(len(snapshot["dataset_revision"]), 40)
        self.assertEqual(len(snapshot["rows_sha256"]), 64)
        self.assertEqual(len(snapshot["parquet_sha256"]), 64)

    def test_catalog_excludes_gold_and_test_patch_content(self):
        raw = json.loads(
            (BENCHMARK_ROOT / "raw" / "dev.rows.json").read_text(
                encoding="utf-8"
            )
        )
        catalog = build_catalog(entry["row"] for entry in raw["rows"])
        self.assertEqual(len(catalog), 23)
        self.assertEqual(len({item["instance_id"] for item in catalog}), 23)
        for item in catalog:
            self.assertNotIn("patch", item)
            self.assertNotIn("test_patch", item)
            self.assertNotIn("problem_statement", item)

    def test_selection_is_diverse_and_bounded(self):
        selection = json.loads(
            (BENCHMARK_ROOT / "pilot-selection.json").read_text(
                encoding="utf-8"
            )
        )
        selected = selection["selected"]
        self.assertEqual(
            [item["instance_id"] for item in selected], list(PILOT)
        )
        self.assertEqual(len(selected), 3)
        self.assertEqual(len({item["repo"] for item in selected}), 3)
        self.assertTrue(all(1 <= item["fail_to_pass_count"] <= 3 for item in selected))

    def test_agent_inputs_exclude_evaluation_only_fields(self):
        payload = json.loads(
            (BENCHMARK_ROOT / "pilot-agent-inputs.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(len(payload["instances"]), 3)
        allowed = {
            "instance_id",
            "repo",
            "base_commit",
            "version",
            "problem_statement",
        }
        for instance in payload["instances"]:
            self.assertEqual(set(instance), allowed)

    def test_repo_snapshot_evidence_matches_selection(self):
        selection = json.loads(
            (BENCHMARK_ROOT / "pilot-selection.json").read_text(
                encoding="utf-8"
            )
        )
        snapshots = json.loads(
            (BENCHMARK_ROOT / "repo-snapshots.json").read_text(
                encoding="utf-8"
            )
        )
        expected = {
            item["instance_id"]: item["base_commit"]
            for item in selection["selected"]
        }
        observed = {
            item["instance_id"]: item["head"]
            for item in snapshots["instances"]
        }
        self.assertEqual(observed, expected)
        self.assertTrue(all(item["clean"] for item in snapshots["instances"]))
        self.assertTrue(
            all(len(item["license_sha256"]) == 64 for item in snapshots["instances"])
        )

    def test_generation_is_reproducible(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "swe_bench_lite"
            (target / "raw").mkdir(parents=True)
            for name in (
                "dev.rows.json",
                "dataset-metadata.json",
                "dev-00000-of-00001.parquet",
            ):
                (target / "raw" / name).write_bytes(
                    (BENCHMARK_ROOT / "raw" / name).read_bytes()
                )
            first = generate(target)
            first_payloads = {
                name: path.read_bytes() for name, path in first.items()
            }
            second = generate(target)
            self.assertEqual(
                first_payloads,
                {name: path.read_bytes() for name, path in second.items()},
            )


if __name__ == "__main__":
    unittest.main()
