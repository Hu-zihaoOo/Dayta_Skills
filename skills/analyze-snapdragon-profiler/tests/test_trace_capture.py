from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "analyze_snapdragon_profiler.py"
SPEC = importlib.util.spec_from_file_location("analyze_snapdragon_profiler", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


TRACE_CSV = """Process,Group,Process ID,Context ID,Thread ID,Track,Block Name,TimestampStart,TimestampEnd,Value, Extra Data
Process,GPU Stages,42,0x1,0x2,Flush Markers,Cmd Flush Marker,0,0,,"Cmd Flush Marker Timestamp: 0uSec Flush Reason: Swap Description: swap"
Process,Metrics,42,0x1,,% Shaders Stalled,,0,,20,
Process,GPU Stages,42,0x1,0x2,Surfaces,"Surface [100, 200]",0,1000,,"Surface Width: 100px Surface Height: 200px MSAA: 1 Number of Bins: 2 Bin Width: 50 Bin Height: 100 Render Mode: HwVizBinning BucketID: 0"
Process,GPU Stages,42,0x1,0x2,Render,Render,0,500,,
Process,Metrics,42,0x1,,% Shaders Stalled,,500,,0,
Process,GPU Stages,42,0x1,0x2,Binning,Binning,500,1000,,
Process,GPU Stages,42,0x1,0x2,Flush Markers,Cmd Flush Marker,1000,1000,,"Cmd Flush Marker Timestamp: 1000uSec Flush Reason: Swap Description: swap"
"""


class TraceCaptureTests(unittest.TestCase):
    def test_trace_analysis_outputs_timeline_and_weighted_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "Trace.csv"
            source.write_text(TRACE_CSV, encoding="utf-8")

            rows = MODULE.read_trace_rows(source)
            self.assertEqual(len(rows), 7)
            self.assertIn("Flush Reason: Swap", rows[0].extra_data)

            result = MODULE.analyze_trace(source, root / "out", top_events=10, top_issues=10)
            context = result["context"]

            self.assertEqual(context["summary"]["capture_mode"], "trace")
            self.assertEqual(context["summary"]["stage_event_count"], 3)
            self.assertEqual(context["summary"]["gpu_stage_event_count"], 3)
            self.assertEqual(context["swap_interval_stats"]["count"], 1)
            self.assertEqual(context["surface_configs"][0]["render_mode"], "HwVizBinning")

            stalled = next(item for item in context["metric_stats"] if item["name"] == "% Shaders Stalled")
            self.assertAlmostEqual(stalled["avg"], 10.0)

            issue_titles = {item["title"] for item in context["rule_issues"]}
            self.assertIn("Binning 阶段时长占比偏高", issue_titles)

            for key in ("report_path", "stage_path", "frame_path", "metric_path", "event_path", "surface_path"):
                self.assertTrue(result[key].is_file(), key)

    def test_interval_union_does_not_double_count_overlap(self) -> None:
        self.assertEqual(MODULE.interval_union_us([(0, 10), (5, 20), (30, 40)]), 30)


if __name__ == "__main__":
    unittest.main()
