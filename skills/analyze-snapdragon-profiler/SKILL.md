---
name: analyze-snapdragon-profiler
description: Analyze Snapdragon Profiler GPU CSV exports and Unity Profiler CPU RawFrameDataView CSV exports for Android/mobile game performance bottlenecks. Use when Codex needs deterministic GPU counter aggregation, Unity CPU marker/frame aggregation, bottleneck-rule evidence, and a Chinese Markdown performance report.
---

# Analyze Snapdragon Profiler

## Workflow

Generate deterministic GPU evidence from Snapdragon Profiler CSV:

```powershell
python C:/Users/huzihao/.codex/skills/analyze-snapdragon-profiler/scripts/analyze_snapdragon_profiler.py analyze <csv-file-or-dir-or-glob> --output report.md --context-output codex_context.json
```

Generate deterministic CPU evidence from Unity Profiler RawFrameDataView CSV:

```powershell
python C:/Users/huzihao/.codex/skills/analyze-snapdragon-profiler/scripts/analyze_snapdragon_profiler.py analyze-unity-cpu <unity-cpu.csv> --out-dir unity_cpu_analysis --context-output unity_cpu_context.json
```

Use generated Markdown as the rule-based baseline. For a richer narrative report, read the generated context JSON, `references/AgentPrompt.md`, and `references/EvaluatePrompts.md`, then have Codex write the final Chinese Markdown analysis.

The script accepts:

- `analyze <csv...>`: Snapdragon Profiler GPU CSV files, directories containing CSV files, or glob patterns.
- `analyze-unity-cpu <csv>`: Unity Profiler CPU CSV exported from RawFrameDataView.
- GPU `--output`: deterministic Markdown report path; defaults to `report.md`.
- GPU/CPU `--context-output`: JSON evidence context for Codex synthesis.
- GPU `--top`: number of top bottlenecks to include; defaults to `8`.
- CPU `--out-dir`: output directory for CPU CSV/Markdown summaries; defaults to `unity_cpu_analysis`.
- CPU `--top-frames`: number of worst frame rows to export; defaults to `100`.
- CPU `--top-markers-per-frame`: markers listed in each worst frame row; defaults to `12`.
- GPU `--no-llm`, `--prompt`, `--agent-prompt`, `--env`: compatibility-only flags; they do not trigger external LLM behavior.

## Data Contract

Snapdragon Profiler GPU CSV requires these columns:

- `Process`
- `Category`
- `Metric`
- `Timestamp`
- `TimestampRaw`
- `Value`

Unity Profiler CPU RawFrameDataView CSV requires these columns:

- `frame`
- `thread`
- `sample`
- `depth`
- `startMs`
- `durationMs`

For GPU metrics, normalize metric instance suffixes like `[1]`, and preserve known aliases such as `ALU/Fragment` to `ALU / Fragment`.

For Unity CPU rows, preserve stack order by `frame`, `thread`, and `depth`; compute inclusive/self time by closing stack samples as depth unwinds.

## Analysis Rules

Keep the analysis evidence deterministic:

- GPU: aggregate per metric average, p50, p95, minimum, maximum, count, raw metric names, process names, categories, source files, and capture duration.
- GPU: run local rules that mirror `references/EvaluatePrompts.md`; only use hard thresholds where the reference provides one, and keep qualitative metrics as context.
- CPU: aggregate marker self/inclusive time, calls, frames, p50/p95/max, per-frame Main Thread / Render Thread time, worst frames, category totals, GC allocation count/time, and profiler overhead.
- CPU: produce `unity_cpu_marker_summary.csv`, `unity_cpu_frame_summary.csv`, `unity_cpu_worst_frames.csv`, `unity_cpu_category_summary.csv`, and `unity_cpu_summary.md`.
- Sort bottleneck issues by severity score.
- Use Codex, not another LLM service, for final interpretation, wording, prioritization, and Markdown polish.

## Evidence Boundaries

Do not invent counters, device model, SoC, GPU, OS version, target FPS, sampling frequency, thermal state, power data, draw calls, materials, shaders, GameObjects, or benefit percentages that are not present in the JSON context.

Explicitly report data gaps. Aggregated GPU CSV counters can support bottleneck direction, but cannot directly identify a specific material, Renderer Feature, drawcall, or GameObject.

Unity CPU CSV can identify CPU frame, thread, marker, category, self-time, inclusive-time, and worst-frame directions. It cannot prove GPU-bound status, GPU counter values, materials, shaders, draw calls, GameObjects, temperature, power, or thermal throttling without additional captures.

## Resources

- `scripts/analyze_snapdragon_profiler.py`: standalone GPU/CPU CSV analyzer and evidence generator.
- `references/AgentPrompt`: Codex report-writing constraints.
- `references/EvaluatePrompts`: GPU metric threshold and interpretation guidance.
