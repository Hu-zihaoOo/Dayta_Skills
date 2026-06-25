---
name: analyze-snapdragon-profiler
description: Analyze Snapdragon Profiler CSV exports for Android/mobile GPU performance bottlenecks. Use when Codex needs to inspect Snapdragon Profiler CSV files, directories, or glob inputs; compute deterministic metric statistics and bottleneck-rule evidence; then use Codex itself to synthesize the final Chinese Markdown report.
---

# Analyze Snapdragon Profiler

## Workflow

Run the bundled standalone script to produce deterministic evidence:

```powershell
python C:/Users/huzihao/.codex/skills/analyze-snapdragon-profiler/scripts/analyze_snapdragon_profiler.py analyze <csv-file-or-dir-or-glob> --output report.md --context-output codex_context.json
```

Use `report.md` as the rule-based baseline. For a richer narrative report, read `codex_context.json`, `references/AgentPrompt.md`, and `references/EvaluatePrompts.md`, then have Codex write the final Markdown analysis. 

The script accepts:

- `analyze <csv...>`: CSV files, directories containing CSV files, or glob patterns.
- `--output`: deterministic Markdown baseline path; defaults to `report.md`.
- `--context-output`: JSON evidence context for Codex synthesis.
- `--top`: number of top bottlenecks to include; defaults to `8`.
- `--no-llm`, `--prompt`, `--agent-prompt`, `--env`: compatibility-only flags; they do not trigger external LLM behavior.

## Data Contract

Require these CSV columns:

- `Process`
- `Category`
- `Metric`
- `Timestamp`
- `TimestampRaw`
- `Value`

Normalize metric instance suffixes like `[1]`, and preserve known aliases such as `ALU/Fragment` to `ALU / Fragment`.

## Analysis Rules

Keep the analysis evidence deterministic:

- Aggregate per metric: average, p50, p95, minimum, maximum, count, raw metric names, process names, categories, source files, and capture duration.
- Run local rules that mirror `references/EvaluatePrompts.md`; only use hard thresholds where the reference provides one, and keep qualitative metrics as context.
- Sort bottleneck issues by severity score.
- Use Codex, not another LLM service, for final interpretation, wording, prioritization, and Markdown polish.

## Evidence Boundaries

Do not invent counters, device model, SoC, GPU, OS version, target FPS, sampling frequency, thermal state, power data, draw calls, materials, shaders, GameObjects, or benefit percentages that are not present in the JSON context.

Explicitly report data gaps. Aggregated CSV counters can support bottleneck direction, but cannot directly identify a specific material, Renderer Feature, draw call, or GameObject.

## Resources

- `scripts/analyze_snapdragon_profiler.py`: standalone CSV analyzer and evidence generator.
- `references/AgentPrompt`: Codex report-writing constraints.
- `references/EvaluatePrompts`: metric threshold and interpretation guidance.
