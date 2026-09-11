# Snapdragon Profiler Snapshot CSV

Read this reference when analyzing `analyze-snapshot` outputs.

## Schema and row types

Required columns, after trimming whitespace:

- `ID`, `Name`, `Parameters`, `Context`, `Thread`
- One or more remaining Counter columns

Interpret rows as follows:

- Non-empty `ID`: profiled GPU command. Only these rows feed capture-wide Counter aggregation.
- Empty `ID` and a non-API `Name`: debug Marker. Marker Counter values describe that Marker scope.
- OpenGL/EGL/Vulkan/OpenCL-style name: API call. Calls without `ID` usually carry state/setup activity but no GPU Counter sample.

Blank numeric cells are missing values. `∞`, `-∞`, other non-finite values, and non-numeric values are excluded and counted in `data_quality`. Ratios commonly become infinite when their denominator is zero; do not replace them with zero.

## Aggregation

- Sum additive event counters: `Clocks`, shaded vertices/fragments, and byte counters.
- Weight `%` counters by `Clocks` across profiled command rows.
- Weight `/ Fragment` ratios by `Fragments Shaded` and `/ Vertex` ratios by `Vertices Shaded`.
- Keep arithmetic average, p50, p95, maximum, missing count, zero count, and excluded non-finite count as command-distribution context.
- Apply EvaluatePrompts thresholds to the aggregate value only when the reference gives a numeric boundary. Per-command p95/max is supporting concentration evidence, not a capture-wide threshold result.

`Clocks` is a relative cost proxy within the capture. Do not convert it to time without documented clock/timing information. A command's `clock_share_pct` is its fraction of summed profiled-command Clocks, not a measured frame-time percentage.

## Marker handling

Marker rows can be nested. A parent Marker and its child Marker may contain the same underlying GPU work, so:

- Rank Marker names by Clocks and inspect their Counter profiles.
- Repeated rows of the same Marker name may be aggregated to rank that label.
- Never sum different Marker groups or their percentages.
- Do not infer an exact parent/child tree from row order; the CSV has no explicit depth/end columns.

Use command `ID`, `Name`, `Parameters`, `Context`, and `Thread` to return from a costly command to the profiler capture. Marker names can narrow the rendering pass or Unity subsystem, but they do not prove a material, Shader, or GameObject identity unless that identity appears in the input.

For every ranked high-cost command, scan upward in CSV row order to the nearest contiguous block of readable Marker rows. Use the enclosing Marker names whose positive `Clocks` are at least the command's `Clocks` as the preferred `pass_name`, preserving outer-to-inner row order. If none meet that condition, use the nearest positive Marker (or nearest Marker when all are zero) as a fallback. Keep the raw API name separately, report the match method, and describe this association as a localization hint rather than a proven hierarchy.

## Derived outputs

- `snapshot_metric_summary.csv`: capture-wide Counter statistics from profiled commands.
- `snapshot_command_summary.csv`: profiled commands grouped by API name.
- `snapshot_top_commands.csv`: highest-Clocks command IDs with inferred readable `pass_name`, raw API name, match method, parameters, and all available Counters.
- `snapshot_marker_summary.csv`: Marker label rankings; rows may overlap semantically.
- `snapshot_api_summary.csv`: API call counts and number of profiled events per name.
- `snapshot_summary.md`: deterministic intermediate summary.
- Context JSON: `summary`, `rule_issues`, `metric_stats`, `top_commands`, `command_stats`, `marker_stats`, `api_call_stats`, `data_quality`, and `data_boundaries`.

## Evidence boundaries

Snapshot provides one capture, not a Realtime series or frame distribution. It cannot show persistence, p95 frame time, thermal behavior, CPU duration, or exact GPU milliseconds by itself. API call counts do not include CPU execution time. High call counts can motivate a batching/state-change check but do not prove CPU submission overhead.

When combining Snapshot with other captures, establish that scene and capture scope are comparable. Use Realtime for persistence, Trace for timing/stages/presentation, Unity CPU for thread and Marker time, and Snapshot for high-cost command/Marker localization. Without time alignment, combine directions only and do not claim per-frame causality.
