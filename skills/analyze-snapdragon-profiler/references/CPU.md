# Unity CPU CSV

## Command

```powershell
python scripts/analyze_snapdragon_profiler.py analyze-cpu <CPU.csv> --out-dir unity_cpu_analysis --context-output unity_cpu_context.json
```

`analyze-unity-cpu` is a compatibility alias.

## Supported schemas

RawFrameDataView requires `frame`, `thread`, `sample`, `depth`, `startMs`, and `durationMs`.

The detailed CPU export requires `FrameIndex`, `ThreadName`, `SampleName`, `Depth`, `StartTimeMs`, and `DurationMs`. It may also contain:

- frame metadata: `CPUFrameTimeMs`, `GPUFrameTimeMs`, `FPS`
- sample metadata: `SelfTimeMs`, `GCAllocBytes`, `SampleIndex`, `MarkerId`, `CategoryId`, `ChildrenCount`
- thread metadata: `ThreadIndex`, `ThreadId`, `ThreadGroup`

Headers are trimmed before schema detection. A file must match one complete structural schema.

## Aggregation semantics

- Group stack samples by frame and thread and preserve input depth order.
- Treat `DurationMs` as inclusive time.
- Prefer supplied `SelfTimeMs`, clamped to `[0, DurationMs]`. When absent, derive self time as inclusive time minus direct child inclusive time while unwinding the stack.
- Deduplicate repeated positive frame metadata per frame. A positive `CPUFrameTimeMs` may fill a missing Main Thread root duration.
- Treat zero or absent `GPUFrameTimeMs` and `FPS` as unavailable. Never interpret a zero GPU value as a zero-cost GPU frame.
- Mark a CPU frame incomplete when neither a positive Main Thread root duration nor positive `CPUFrameTimeMs` is available. Exclude incomplete frames from frame-time averages and budget counts, but retain their sample evidence and expose the count in `data_quality`.
- Aggregate `GCAllocBytes` by marker and frame. An allocation event is a row with positive allocation bytes or a `GC.Alloc` marker. Allocation bytes show allocation activity; they do not prove a collection pause.
- Report only threads present in the input. Missing Render Thread rows mean Render Thread cost is unavailable, not zero.

## Outputs

- `unity_cpu_marker_summary.csv`: marker calls, frame coverage, self/inclusive distributions, and allocation bytes.
- `unity_cpu_frame_summary.csv`: per-frame CPU/thread timing, optional GPU/FPS metadata, completeness, allocation, and profiler overhead.
- `unity_cpu_worst_frames.csv`: worst valid Main Thread frames with top self/inclusive markers.
- `unity_cpu_category_summary.csv`: heuristic marker-category totals.
- `unity_cpu_summary.md`: deterministic baseline summary.
- Context JSON: `summary`, `rule_issues`, `marker_stats`, `category_stats`, `worst_frames`, `data_quality`, and `data_boundaries`.

## Evidence boundaries

CPU marker names and timing can localize expensive systems and frames but do not identify exact source code or object ownership unless that identity appears in the marker. Heuristic categories are navigation aids, not ground truth.

CPU data alone cannot establish GPU-bound status. A positive exported `GPUFrameTimeMs` is frame metadata, not a substitute for GPU counter, stage, command, frequency, or thermal evidence.
