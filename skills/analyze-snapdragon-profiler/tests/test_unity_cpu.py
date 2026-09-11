from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "analyze_snapdragon_profiler.py"
SPEC = importlib.util.spec_from_file_location("analyze_snapdragon_profiler_cpu", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


DETAILED_CPU_CSV = """FrameIndex,CPUFrameTimeMs,GPUFrameTimeMs,FPS,ThreadIndex,ThreadId,ThreadGroup,ThreadName,SampleIndex,Depth,MarkerId,CategoryId,SampleName,StartTimeMs,DurationMs,SelfTimeMs,ChildrenCount,GCAllocBytes
0,20,8,50,0,1,,Main Thread,0,0,1,1,Main Thread,100,20,1,1,0
0,20,8,50,0,1,,Main Thread,1,1,2,1,PlayerLoop,101,5,4,1,0
0,20,8,50,0,1,,Main Thread,2,2,3,1,GC.Alloc,102,1,1,0,128
1,10,0,100,0,1,,Main Thread,0,0,1,1,Main Thread,120,10,2,1,0
1,10,0,100,0,1,,Main Thread,1,1,2,1,Update.ScriptRunBehaviourUpdate,121,3,3,0,0
"""

RAW_FRAME_CPU_CSV = """frame,thread,sample,depth,startMs,durationMs
0,Main Thread,Main Thread,0,0,10
0,Main Thread,PlayerLoop,1,1,4
"""


class UnityCpuTests(unittest.TestCase):
    def test_detailed_cpu_export_uses_supplied_self_time_and_extended_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "CPU.csv"
            source.write_text(DETAILED_CPU_CSV, encoding="utf-8")

            rows = list(MODULE.read_unity_cpu_rows(source))
            self.assertEqual(len(rows), 5)
            self.assertEqual(rows[0]["source_format"], "detailed_cpu_export")
            self.assertEqual(rows[2]["gc_alloc_bytes"], 128)

            result = MODULE.analyze_unity_cpu(source, root / "out", 10, 10)
            context = result["context"]
            summary = context["summary"]

            self.assertEqual(summary["source_format"], "detailed_cpu_export")
            self.assertEqual(summary["frame_count"], 2)
            self.assertEqual(summary["valid_cpu_frame_count"], 2)
            self.assertAlmostEqual(summary["cpu_frame_time_avg_ms"], 15.0)
            self.assertAlmostEqual(summary["gpu_frame_time_avg_ms"], 8.0)
            self.assertAlmostEqual(summary["fps_avg"], 75.0)
            self.assertEqual(summary["gc_alloc_count"], 1)
            self.assertEqual(summary["gc_alloc_bytes"], 128)

            player_loop = next(row for row in context["marker_stats"] if row["sample"] == "PlayerLoop")
            self.assertAlmostEqual(player_loop["total_self_ms"], 4.0)
            self.assertEqual(context["data_quality"]["supplied_self_time_sample_count"], 5)
            self.assertTrue(context["data_quality"]["gpu_frame_time_available"])

            frame_csv = result["frame_path"].read_text(encoding="utf-8")
            marker_csv = result["marker_path"].read_text(encoding="utf-8")
            self.assertIn("cpu_frame_time_ms", frame_csv)
            self.assertIn("gc_alloc_bytes", frame_csv)
            self.assertIn("total_gc_alloc_bytes", marker_csv)

    def test_raw_frame_export_remains_supported_and_derives_self_time(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "cpu_raw.csv"
            source.write_text(RAW_FRAME_CPU_CSV, encoding="utf-8")

            result = MODULE.analyze_unity_cpu(source, root / "out", 10, 10)
            context = result["context"]
            self.assertEqual(context["summary"]["source_format"], "raw_frame_data_view")
            self.assertEqual(context["data_quality"]["derived_self_time_sample_count"], 2)
            player_loop = next(row for row in context["marker_stats"] if row["sample"] == "PlayerLoop")
            self.assertAlmostEqual(player_loop["total_self_ms"], 4.0)

    def test_analyze_cpu_command_and_compatibility_alias_are_registered(self) -> None:
        parser = MODULE.build_parser()
        self.assertEqual(parser.parse_args(["analyze-cpu", "CPU.csv"]).command, "analyze-cpu")
        self.assertEqual(parser.parse_args(["analyze-unity-cpu", "CPU.csv"]).command, "analyze-unity-cpu")


if __name__ == "__main__":
    unittest.main()
