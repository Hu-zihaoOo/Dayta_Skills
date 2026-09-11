# Snapdragon Profiler Trace Capture

Read this reference only for `analyze-trace` inputs and reports.

## Input

The parser accepts Snapdragon Profiler Trace CSV headers after trimming whitespace:

```text
Process,Group,Process ID,Context ID,Thread ID,Track,Block Name,
TimestampStart,TimestampEnd,Value,Extra Data
```

- `TimestampStart` and `TimestampEnd` are interpreted as microseconds.
- Empty `TimestampEnd` denotes an instant/counter sample rather than a duration event.
- For `Group=Metrics`, `Track` is the counter name and `Value` is its numeric sample.
- Other groups are timeline events. `Track` identifies the lane and `Block Name` identifies the event.
- `Extra Data` is optional. Recognized details include `Flush Reason`, Surface dimensions, render mode, MSAA, and bin layout.

## Derived Evidence

- Counter statistics use the interval until the next sample as a duration weight. This avoids treating dense and sparse Trace samples as equal time.
- `gpu_active_ms` is the interval union of positive-duration non-Metrics events, so overlapping tracks are counted once.
- Stage `total_ms` is the sum within that track. Different tracks can overlap; never add all stage totals as frame time.
- Swap interval rows are built between consecutive `Flush Reason: Swap` markers. They describe observed presentation/submission pacing, not pure GPU execution time.
- `Binning / (Binning + Render)` uses summed track durations. Values above 30% are a medium-confidence signal based on `EvaluatePrompts.md`; track overlap and incomplete captures can weaken it.
- Surface rows group resolution, render mode, MSAA, number of bins, and bin size parsed from `Extra Data`.

## Outputs

- `trace_summary.md`: deterministic Chinese summary and top diagnostics.
- `trace_stage_summary.csv`: per-group/track duration statistics.
- `trace_swap_interval_summary.csv`: Swap pacing plus clipped GPU-stage durations.
- `trace_metric_summary.csv`: duration-weighted counter statistics.
- `trace_longest_events.csv`: longest positive-duration timeline events.
- `trace_surface_summary.csv`: aggregated Surface configurations.
- Optional context JSON: evidence for Codex narrative synthesis.

## Boundaries

- Trace counters and Realtime counters may have different sampling semantics. Compare direction and distribution, not raw averages blindly.
- A Swap interval can contain CPU wait, queueing, synchronization, compositor behavior, or frame limiting. It does not prove GPU-bound status.
- Timeline Track totals can be hierarchical or overlapping.
- Aggregated Trace CSV can identify a stage and time range, but not a specific material, Shader, Draw Call, or GameObject unless that identity exists in the export.
