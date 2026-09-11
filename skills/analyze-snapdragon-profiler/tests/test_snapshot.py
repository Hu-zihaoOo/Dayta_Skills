from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "analyze_snapdragon_profiler.py"
SPEC = importlib.util.spec_from_file_location("analyze_snapdragon_profiler_snapshot", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


SNAPSHOT_CSV = """ ID, Name, Parameters, Context, Thread, % Shaders Busy, % Shaders Stalled, ALU / Fragment, Clocks, Fragments Shaded, Vertices Shaded, Texture Memory Read BW (Bytes)
,RenderPass,,,0x1,50,5,2,400,20,10,128
0,glDrawElements,"( count = 3 )",1,0x1,80,12,∞,100,10,3,64
,glBindTexture,"( texture = 7 )",1,0x1,,,,,,,
,PostProcessing,,,0x1,20,4,2,300,30,6,192
1,glDrawArrays,"( count = 6 )",1,0x1,20,4,2,300,30,6,192
"""


class SnapshotTests(unittest.TestCase):
    def test_snapshot_analysis_outputs_weighted_metrics_and_rankings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "Snapshot.csv"
            source.write_text(SNAPSHOT_CSV, encoding="utf-8")

            rows, metric_columns = MODULE.read_snapshot_rows(source)
            self.assertEqual(len(rows), 5)
            self.assertIn("% Shaders Busy", metric_columns)
            self.assertEqual(rows[1].non_finite_metrics, ("ALU / Fragment",))

            result = MODULE.analyze_snapshot(
                source,
                root / "out",
                top_commands=10,
                top_markers=10,
                top_issues=10,
            )
            context = result["context"]
            summary = context["summary"]

            self.assertEqual(summary["capture_mode"], "snapshot")
            self.assertEqual(summary["profiled_event_count"], 2)
            self.assertEqual(summary["marker_row_count"], 2)
            self.assertEqual(summary["api_call_count"], 3)
            self.assertEqual(summary["draw_call_count"], 2)
            self.assertEqual(summary["total_event_clocks"], 400.0)

            busy = next(item for item in context["metric_stats"] if item["name"] == "% Shaders Busy")
            self.assertEqual(busy["aggregate_method"], "clock_weighted_mean")
            self.assertAlmostEqual(busy["aggregate"], 35.0)

            alu_fragment = next(item for item in context["metric_stats"] if item["name"] == "ALU / Fragment")
            self.assertEqual(alu_fragment["non_finite_count"], 1)
            self.assertAlmostEqual(alu_fragment["aggregate"], 2.0)

            self.assertEqual(context["top_commands"][0]["id"], "1")
            self.assertEqual(context["top_commands"][0]["pass_name"], "PostProcessing")
            self.assertEqual(context["top_commands"][0]["pass_match_method"], "clock_enclosing_marker_block")
            self.assertEqual(context["top_commands"][1]["pass_name"], "RenderPass")
            self.assertEqual(context["marker_stats"][0]["marker"], "RenderPass")
            self.assertAlmostEqual(context["marker_stats"][0]["clock_share_pct"], 100.0)

            top_csv = result["top_command_path"].read_text(encoding="utf-8-sig")
            self.assertIn("pass_name", top_csv)
            report = result["report_path"].read_text(encoding="utf-8")
            self.assertIn("PostProcessing", report)

            for key in (
                "metric_path",
                "command_path",
                "top_command_path",
                "marker_path",
                "api_path",
                "report_path",
            ):
                self.assertTrue(result[key].is_file(), key)

    def test_snapshot_requires_profiled_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "Snapshot.csv"
            source.write_text("ID,Name,Parameters,Context,Thread,Clocks\n,Marker,,,,1\n", encoding="utf-8")
            with self.assertRaises(MODULE.CsvLoadError):
                MODULE.read_snapshot_rows(source)


if __name__ == "__main__":
    unittest.main()
