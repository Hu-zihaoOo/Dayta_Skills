---
name: analyze-snapdragon-profiler
description: Analyze Snapdragon Profiler Realtime counter, Trace Capture timeline, Snapshot per-command CSVs, and Unity Profiler CPU.csv or RawFrameDataView CSVs for Android/mobile game performance bottlenecks. Use when Codex needs deterministic GPU counter, GPU command/marker, GPU stage, Swap interval, Surface, or Unity CPU marker/frame evidence and a Chinese Markdown report.
---

# Analyze Snapdragon Profiler

## Workflow

Generate deterministic GPU evidence from Snapdragon Profiler CSV:

```powershell
python C:/Users/huzihao/.codex/skills/analyze-snapdragon-profiler/scripts/analyze_snapdragon_profiler.py analyze <csv-file-or-dir-or-glob> --output report.md --context-output codex_context.json
```

Analyze a Snapdragon Profiler Trace Capture CSV:

```powershell
python C:/Users/huzihao/.codex/skills/analyze-snapdragon-profiler/scripts/analyze_snapdragon_profiler.py analyze-trace <trace.csv> --out-dir trace_analysis --context-output trace_context.json
```

Analyze a Snapdragon Profiler Snapshot CSV:

```powershell
python C:/Users/huzihao/.codex/skills/analyze-snapdragon-profiler/scripts/analyze_snapdragon_profiler.py analyze-snapshot <Snapshot.csv> --out-dir snapshot_analysis --context-output snapshot_context.json
```

Generate deterministic CPU evidence from a detailed Unity `CPU.csv` or RawFrameDataView CSV:

```powershell
python C:/Users/huzihao/.codex/skills/analyze-snapdragon-profiler/scripts/analyze_snapdragon_profiler.py analyze-cpu <CPU.csv> --out-dir unity_cpu_analysis --context-output unity_cpu_context.json
```

Use generated Markdown as the rule-based baseline. For a richer narrative report, read the generated context JSON and `references/AgentPrompt.md`; for GPU evidence also read `references/EvaluatePrompts.md`, for Trace read `references/TraceCapture.md`, for Snapshot read `references/Snapshot.md`, and for CPU read `references/CPU.md`. Then have Codex write the final Chinese Markdown analysis.

## Report Artifact Rule

Each user analysis request must produce exactly one final Markdown report, regardless of how many Realtime, Trace Capture, Snapshot, or Unity CPU inputs are analyzed.

- Decide the final report path once before analysis. Use the user-provided path when present; otherwise use `snapdragon_profiler_report.md` in the requested output directory.
- Treat `report.md`, `trace_summary.md`, `snapshot_summary.md`, and `unity_cpu_summary.md` emitted by analyzer commands as intermediate rule-based evidence, not separate deliverables.
- When multiple input types are present, read all context JSON and intermediate summaries, then merge them into the same final report. Realtime counters, Trace Capture timelines, Snapshot commands/Markers, and Unity CPU evidence must be sections of one diagnosis, with a combined conclusion that reconciles supporting or conflicting evidence.
- Generate analyzer Markdown in a temporary working directory when practical. After the final report is written and verified, remove only the intermediate Markdown files created by the current run; retain generated JSON and CSV evidence unless the user requests otherwise.
- Do not present, link, or describe intermediate Markdown as additional reports. If one input fails, record that failure as a data gap in the single final report instead of creating a separate error report.

The script accepts:

- `analyze <csv...>`: Snapdragon Profiler Realtime GPU CSV files, directories containing CSV files, or glob patterns.
- `analyze-trace <csv>`: one Snapdragon Profiler Trace Capture CSV.
- `analyze-snapshot <csv>`: one Snapdragon Profiler Snapshot per-command CSV.
- `analyze-cpu <csv>`: detailed Unity `CPU.csv` or RawFrameDataView CSV. `analyze-unity-cpu` remains a compatibility alias.
- Realtime GPU `--output`: deterministic Markdown report path; defaults to `report.md`.
- Realtime/Trace/Snapshot/CPU `--context-output`: JSON evidence context for Codex synthesis.
- Realtime/Trace/Snapshot `--top`: number of top diagnostics to include; defaults to `8`.
- Trace `--out-dir`: output directory; defaults to `trace_analysis`.
- Trace `--top-events`: number of longest timeline events to export; defaults to `100`.
- Snapshot `--out-dir`: output directory; defaults to `snapshot_analysis`.
- Snapshot `--top-commands`: number of highest-Clocks profiled commands to export; defaults to `100`.
- Snapshot `--top-markers`: number of highest-Clocks marker groups to export; defaults to `100`.
- CPU `--out-dir`: output directory for CPU CSV/Markdown summaries; defaults to `unity_cpu_analysis`.
- CPU `--top-frames`: number of worst frame rows to export; defaults to `100`.
- CPU `--top-markers-per-frame`: markers listed in each worst frame row; defaults to `12`.
- GPU `--no-llm`, `--prompt`, `--agent-prompt`, `--env`: compatibility-only flags; they do not trigger external LLM behavior.

## Data Contract

Snapdragon Profiler Realtime GPU CSV requires these columns:

- `Process`
- `Category`
- `Metric`
- `Timestamp`
- `TimestampRaw`
- `Value`

Snapdragon Profiler Trace Capture CSV requires these columns after trimming header whitespace:

- `Process`
- `Group`
- `Process ID`
- `Context ID`
- `Thread ID`
- `Track`
- `Block Name`
- `TimestampStart`
- `TimestampEnd`
- `Value`

`Extra Data` is optional. When present, use it for Flush reasons and Surface configuration. Read `references/TraceCapture.md` before interpreting Trace outputs.

Snapdragon Profiler Snapshot CSV requires these columns after trimming header whitespace:

- `ID`
- `Name`
- `Parameters`
- `Context`
- `Thread`

All remaining columns are treated as Counter columns. A row with `ID` is a profiled GPU command. A row without `ID` and with a non-API name is a Marker row. Read `references/Snapshot.md` before interpreting Snapshot outputs.

Unity Profiler CPU RawFrameDataView CSV requires these columns:

- `frame`
- `thread`
- `sample`
- `depth`
- `startMs`
- `durationMs`

The detailed `CPU.csv` format requires these structural columns:

- `FrameIndex`
- `ThreadName`
- `SampleName`
- `Depth`
- `StartTimeMs`
- `DurationMs`

It may additionally provide `CPUFrameTimeMs`, `GPUFrameTimeMs`, `FPS`, `ThreadIndex`, `ThreadId`, `ThreadGroup`, `SampleIndex`, `MarkerId`, `CategoryId`, `SelfTimeMs`, `ChildrenCount`, and `GCAllocBytes`. Read `references/CPU.md` before interpreting CPU outputs.

For GPU metrics, normalize metric instance suffixes like `[1]`, and preserve known aliases such as `ALU/Fragment` to `ALU / Fragment`.

For Unity CPU rows, preserve stack order by frame, thread, and depth. Prefer a valid supplied `SelfTimeMs`; otherwise derive self time by closing stack samples as depth unwinds.

## Analysis Rules

Keep the analysis evidence deterministic:

- GPU: aggregate per metric average, p50, p95, minimum, maximum, count, raw metric names, process names, categories, source files, and capture duration.
- GPU: run local rules that mirror `references/EvaluatePrompts.md`; only use hard thresholds where the reference provides one, and keep qualitative metrics as context.
- Trace: duration-weight counter values between consecutive timestamps; aggregate stage count/total/avg/p50/p95/max, interval-union GPU active time, Swap intervals, longest events, Flush reasons, and Surface configurations.
- Trace: produce `trace_stage_summary.csv`, `trace_swap_interval_summary.csv`, `trace_metric_summary.csv`, `trace_longest_events.csv`, `trace_surface_summary.csv`, and `trace_summary.md`.
- Trace: treat Binning divided by Binning plus Render summed duration above 30% as a medium-confidence signal, not proof of a render-pass root cause.
- Snapshot: aggregate Counter values only from rows with an `ID`; sum additive counters, weight percentage counters by `Clocks`, and weight per-fragment/per-vertex ratios by their matching shaded counts.
- Snapshot: rank profiled commands, aggregate command names, list API call counts, and summarize Marker rows separately. For each high-cost command, scan upward to the nearest readable Marker block and expose its inferred Pass/Marker name alongside the raw API name. Never sum Marker groups because nested Marker counters overlap.
- Snapshot: produce `snapshot_metric_summary.csv`, `snapshot_command_summary.csv`, `snapshot_top_commands.csv`, `snapshot_marker_summary.csv`, `snapshot_api_summary.csv`, and `snapshot_summary.md`.
- CPU: aggregate marker self/inclusive time, calls, frames, p50/p95/max, per-frame Main Thread / Render Thread time, supplied CPU/GPU frame time and FPS when valid, worst frames, category totals, GC allocation count/bytes/time, and profiler overhead.
- CPU: produce `unity_cpu_marker_summary.csv`, `unity_cpu_frame_summary.csv`, `unity_cpu_worst_frames.csv`, `unity_cpu_category_summary.csv`, and `unity_cpu_summary.md`.
- Sort bottleneck issues by severity score.
- Use Codex, not another LLM service, for final interpretation, wording, prioritization, and Markdown polish.

## EvaluatePrompts Coverage

For every final report that contains GPU evidence, add a compact `EvaluatePrompts 建议值核对` subsection under the key-evidence section. Cover every available metric with an explicit numeric reference, including lower-priority misses that are not expanded in the top findings. When the user requests a complete conformance audit, also list absent referenced metrics. Follow the detailed status, statistic-selection, multi-source, Binning-band, and validation rules in `references/AgentPrompt.md`.

## Evidence Boundaries

Do not invent counters, device model, SoC, GPU, OS version, target FPS, sampling frequency, thermal state, power data, draw calls, materials, shaders, GameObjects, or benefit percentages that are not present in the JSON context.

Explicitly report data gaps. Aggregated GPU CSV counters can support bottleneck direction, but cannot directly identify a specific material, Renderer Feature, drawcall, or GameObject.

Trace stage totals can overlap. Do not add Track totals as GPU frame time. Swap intervals are presentation/submission intervals, not pure GPU frame time. Trace counter sampling semantics can differ from Realtime even though both expose similarly named metrics.

Snapshot is a single capture of command/Marker counters, not a Realtime time series. `Clocks` is a relative cost proxy and cannot be converted to milliseconds without additional timing/frequency evidence. Marker rows may be nested and overlap. API call counts contain no CPU duration. Snapshot can identify expensive command IDs, parameters, and Marker directions, but cannot by itself prove a frame-level bottleneck or a specific material, Shader, or GameObject root cause.

Unity CPU CSV can identify CPU frame, thread, marker, category, self-time, inclusive-time, allocation, and worst-frame directions. Treat zero or absent `GPUFrameTimeMs` as unavailable, not as a zero-cost GPU frame. CPU data alone cannot prove GPU-bound status, GPU counter values, materials, shaders, draw calls, GameObjects, temperature, power, or thermal throttling.

## Resources

- `scripts/analyze_snapdragon_profiler.py`: standalone Realtime GPU, Trace, Snapshot, and Unity CPU CSV analyzer.
- `references/AgentPrompt`: Codex report-writing constraints.
- `references/EvaluatePrompts`: GPU metric threshold and interpretation guidance.
- `references/TraceCapture.md`: Trace schema, derived outputs, and evidence boundaries.
- `references/Snapshot.md`: Snapshot schema, aggregation semantics, derived outputs, and evidence boundaries.
- `references/CPU.md`: CPU schemas, supplied-field semantics, derived outputs, and evidence boundaries.
- `tests/test_trace_capture.py`: Trace regression coverage.
- `tests/test_snapshot.py`: Snapshot regression coverage.
- `tests/test_unity_cpu.py`: detailed and RawFrameDataView CPU regression coverage.
