from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence


REQUIRED_COLUMNS = {
    "Process",
    "Category",
    "Metric",
    "Timestamp",
    "TimestampRaw",
    "Value",
}

TRACE_REQUIRED_COLUMNS = {
    "Process",
    "Group",
    "Process ID",
    "Context ID",
    "Thread ID",
    "Track",
    "Block Name",
    "TimestampStart",
    "TimestampEnd",
    "Value",
}

SNAPSHOT_REQUIRED_COLUMNS = {
    "ID",
    "Name",
    "Parameters",
    "Context",
    "Thread",
}

SNAPSHOT_ADDITIVE_METRICS = {
    "Clocks",
    "Fragments Shaded",
    "SP Memory Read (Bytes)",
    "Texture Memory Read BW (Bytes)",
    "Vertex Memory Read (Bytes)",
    "Vertices Shaded",
}

FLUSH_REASON_RE = re.compile(r"Flush Reason:\s*([^\s]+)", re.IGNORECASE)
SURFACE_WIDTH_RE = re.compile(r"Surface Width:\s*(\d+)px", re.IGNORECASE)
SURFACE_HEIGHT_RE = re.compile(r"Surface Height:\s*(\d+)px", re.IGNORECASE)
SURFACE_MSAA_RE = re.compile(r"MSAA:\s*(\d+)", re.IGNORECASE)
SURFACE_BINS_RE = re.compile(r"Number of Bins:\s*(\d+)", re.IGNORECASE)
SURFACE_BIN_WIDTH_RE = re.compile(r"Bin Width:\s*(\d+)", re.IGNORECASE)
SURFACE_BIN_HEIGHT_RE = re.compile(r"Bin Height:\s*(\d+)", re.IGNORECASE)
SURFACE_RENDER_MODE_RE = re.compile(r"Render Mode:\s*(.+?)\s+BucketID:", re.IGNORECASE)

INSTANCE_SUFFIX_RE = re.compile(r"\[\d+\]$")

METRIC_ALIASES = {
    "ALU/Fragment": "ALU / Fragment",
    "ALU/Vertex": "ALU / Vertex",
    "Textures/Fragment": "Textures / Fragment",
    "Textures/Vertex": "Textures / Vertex",
    "Fragment ALU Instructions (Full)": "Fragment ALU Instructions / Sec (Full)",
    "Fragment ALU Instructions (Half)": "Fragment ALU Instructions / Sec (Half)",
    "Texture Memory Read BW": "Texture Memory Read BW (Bytes/Second)",
    "Vertex Memory Read": "Vertex Memory Read (Bytes/Second)",
    "Write Total": "Write Total (Bytes/sec)",
}

SEVERITY_SCORE = {
    "high": 90,
    "medium": 60,
    "low": 30,
    "info": 10,
}


class CsvLoadError(ValueError):
    pass


@dataclass(frozen=True)
class ProfileRow:
    process: str
    category: str
    metric: str
    timestamp: int
    timestamp_raw: int
    value: float


@dataclass(frozen=True)
class MetricStats:
    name: str
    category: str
    count: int
    avg: float
    minimum: float
    p50: float
    p95: float
    maximum: float
    raw_names: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["min"] = data.pop("minimum")
        data["max"] = data.pop("maximum")
        return data


@dataclass(frozen=True)
class ProfileSummary:
    source: str
    source_files: tuple[str, ...]
    row_count: int
    process_names: tuple[str, ...]
    categories: tuple[str, ...]
    duration_seconds: float | None
    metrics: dict[str, MetricStats]

    def get(self, metric_name: str) -> MetricStats | None:
        return self.metrics.get(normalize_metric_name(metric_name))


@dataclass(frozen=True)
class BottleneckIssue:
    title: str
    severity: str
    score: float
    metric: str
    evidence: str
    interpretation: str
    recommendation: str
    confidence: str = "medium"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class TraceRow:
    process: str
    group: str
    process_id: str
    context_id: str
    thread_id: str
    track: str
    block_name: str
    timestamp_start: int
    timestamp_end: int | None
    value: float | None
    extra_data: str


@dataclass(frozen=True)
class SnapshotRow:
    row_number: int
    event_id: str
    name: str
    parameters: str
    context: str
    thread: str
    metrics: dict[str, float]
    non_finite_metrics: tuple[str, ...]
    has_metric_fields: bool


def normalize_metric_name(metric: str) -> str:
    name = INSTANCE_SUFFIX_RE.sub("", metric).strip()
    name = re.sub(r"\s+", " ", name)
    return METRIC_ALIASES.get(name, name)


def parse_int(text: str, *, row_number: int, column: str) -> int:
    try:
        return int(float(text))
    except ValueError as exc:
        raise CsvLoadError(f"第 {row_number} 行 `{column}` 不是有效数字: {text!r}") from exc


def parse_float(text: str, *, row_number: int, column: str) -> float:
    try:
        return float(text)
    except ValueError as exc:
        raise CsvLoadError(f"第 {row_number} 行 `{column}` 不是有效数字: {text!r}") from exc


def load_csv(path: str | Path) -> list[ProfileRow]:
    csv_path = Path(path)
    if not csv_path.exists():
        raise CsvLoadError(f"CSV 文件不存在: {csv_path}")
    if not csv_path.is_file():
        raise CsvLoadError(f"路径不是文件: {csv_path}")

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise CsvLoadError("CSV 为空或缺少表头")

        missing = REQUIRED_COLUMNS.difference(reader.fieldnames)
        if missing:
            raise CsvLoadError(f"CSV 缺少必要列: {', '.join(sorted(missing))}")

        rows: list[ProfileRow] = []
        for index, raw in enumerate(reader, start=2):
            rows.append(
                ProfileRow(
                    process=(raw.get("Process") or "").strip(),
                    category=(raw.get("Category") or "").strip(),
                    metric=(raw.get("Metric") or "").strip(),
                    timestamp=parse_int(raw.get("Timestamp") or "", row_number=index, column="Timestamp"),
                    timestamp_raw=parse_int(raw.get("TimestampRaw") or "", row_number=index, column="TimestampRaw"),
                    value=parse_float(raw.get("Value") or "", row_number=index, column="Value"),
                )
            )

    if not rows:
        raise CsvLoadError("CSV 没有数据行")
    return rows


def resolve_csv_inputs(inputs: Sequence[str | Path]) -> list[Path]:
    if not inputs:
        raise CsvLoadError("未提供 CSV 输入")

    resolved: list[Path] = []
    for raw_input in inputs:
        text = str(raw_input)
        path = Path(text)

        if path.exists():
            if path.is_dir():
                csv_files = sorted(item for item in path.iterdir() if item.is_file() and item.suffix.lower() == ".csv")
                if not csv_files:
                    raise CsvLoadError(f"目录中没有 CSV 文件: {path}")
                resolved.extend(csv_files)
            elif path.is_file():
                resolved.append(path)
            else:
                raise CsvLoadError(f"路径不是文件或目录: {path}")
            continue

        if glob.has_magic(text):
            matches = sorted(
                Path(match)
                for match in glob.glob(text, recursive=True)
                if Path(match).is_file() and Path(match).suffix.lower() == ".csv"
            )
            if not matches:
                raise CsvLoadError(f"glob 没有匹配到 CSV 文件: {text}")
            resolved.extend(matches)
            continue

        raise CsvLoadError(f"CSV 文件不存在: {path}")

    deduped: list[Path] = []
    seen: set[str] = set()
    for path in resolved:
        key = str(path.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)

    if not deduped:
        raise CsvLoadError("没有可读取的 CSV 文件")
    return deduped


def load_csv_files(inputs: Sequence[str | Path]) -> tuple[list[ProfileRow], tuple[str, ...]]:
    source_paths = resolve_csv_inputs(inputs)
    rows: list[ProfileRow] = []

    for path in source_paths:
        try:
            rows.extend(load_csv(path))
        except CsvLoadError as exc:
            raise CsvLoadError(f"读取 CSV 失败 ({path}): {exc}") from exc

    if not rows:
        raise CsvLoadError("CSV 输入没有数据行")
    return rows, tuple(str(path) for path in source_paths)


def percentile(sorted_values: list[float], value: float) -> float:
    if not sorted_values:
        return math.nan
    if len(sorted_values) == 1:
        return sorted_values[0]
    index = round((len(sorted_values) - 1) * value)
    return sorted_values[max(0, min(len(sorted_values) - 1, index))]


def summarize(rows: list[ProfileRow], source_files: tuple[str, ...]) -> ProfileSummary:
    values_by_metric: dict[str, list[float]] = defaultdict(list)
    raw_names_by_metric: dict[str, set[str]] = defaultdict(set)
    categories_by_metric: dict[str, Counter[str]] = defaultdict(Counter)

    for row in rows:
        metric_name = normalize_metric_name(row.metric)
        values_by_metric[metric_name].append(row.value)
        raw_names_by_metric[metric_name].add(row.metric)
        categories_by_metric[metric_name][row.category] += 1

    metrics: dict[str, MetricStats] = {}
    for name, values in values_by_metric.items():
        ordered = sorted(values)
        metrics[name] = MetricStats(
            name=name,
            category=categories_by_metric[name].most_common(1)[0][0],
            count=len(values),
            avg=sum(values) / len(values),
            minimum=ordered[0],
            p50=percentile(ordered, 0.5),
            p95=percentile(ordered, 0.95),
            maximum=ordered[-1],
            raw_names=tuple(sorted(raw_names_by_metric[name])),
        )

    timestamps = [row.timestamp for row in rows]
    duration_seconds = (max(timestamps) - min(timestamps)) / 1_000_000 if timestamps else None
    source = source_files[0] if len(source_files) == 1 else f"{len(source_files)} CSV files"

    return ProfileSummary(
        source=source,
        source_files=source_files,
        row_count=len(rows),
        process_names=tuple(sorted({row.process for row in rows if row.process})),
        categories=tuple(name for name, _ in Counter(row.category for row in rows).most_common()),
        duration_seconds=duration_seconds,
        metrics=dict(sorted(metrics.items())),
    )


def fmt(value: float, suffix: str = "") -> str:
    if abs(value) >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B{suffix}"
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.2f}M{suffix}"
    if abs(value) >= 1_000:
        return f"{value:,.0f}{suffix}"
    return f"{value:.2f}{suffix}"


def issue(
    *,
    title: str,
    severity: str,
    metric: MetricStats,
    evidence: str,
    interpretation: str,
    recommendation: str,
    confidence: str = "medium",
    extra_score: float = 0,
) -> BottleneckIssue:
    return BottleneckIssue(
        title=title,
        severity=severity,
        score=SEVERITY_SCORE[severity] + extra_score,
        metric=metric.name,
        evidence=evidence,
        interpretation=interpretation,
        recommendation=recommendation,
        confidence=confidence,
    )


def evaluate_rules(summary: ProfileSummary) -> list[BottleneckIssue]:
    issues: list[BottleneckIssue] = []

    def get_metric(*names: str) -> MetricStats | None:
        for name in names:
            metric = summary.get(name)
            if metric:
                return metric
        return None

    def percent_evidence(metric: MetricStats) -> str:
        return (
            f"avg={fmt(metric.avg, '%')}, p50={fmt(metric.p50, '%')}, "
            f"p95={fmt(metric.p95, '%')}, max={fmt(metric.maximum, '%')}"
        )

    # Thresholds mirror references/EvaluatePrompts.md. Qualitative-only guidance is not turned into hard failures.
    polygon_area = summary.get("Average Polygon Area")
    if polygon_area and polygon_area.avg < 4:
        issues.append(
            issue(
                title="平均多边形面积低于理想下限",
                severity="low",
                metric=polygon_area,
                evidence=f"avg={fmt(polygon_area.avg)}, p95={fmt(polygon_area.p95)}",
                interpretation="Average Polygon Area 理想上至少为 4；过低可能说明图元过碎。",
                recommendation="检查小三角形密度、网格拆分和 LOD；是否过大还需要结合 bin dimensions 判断。",
                confidence="medium",
                extra_score=min(10, 4 - polygon_area.avg),
            )
        )

    prims_clipped = summary.get("% Prims Clipped")
    if prims_clipped and prims_clipped.avg > 2:
        issues.append(
            issue(
                title="Prims clipped 高于理想阈值",
                severity="medium",
                metric=prims_clipped,
                evidence=percent_evidence(prims_clipped),
                interpretation="% Prims Clipped 理想值低于 2%，当前平均值偏高。",
                recommendation="检查视锥裁剪、近远裁剪面、过大网格和摄像机外仍提交的 draw。",
                confidence="medium",
                extra_score=min(12, prims_clipped.avg - 2),
            )
        )

    prim_rejected = summary.get("% Prims Trivially Rejected")
    if prim_rejected and prim_rejected.avg > 2:
        issues.append(
            issue(
                title="Prims trivially rejected 高于理想阈值",
                severity="medium",
                metric=prim_rejected,
                evidence=percent_evidence(prim_rejected),
                interpretation="% Prims Trivially Rejected 理想值低于 2%，当前平均值偏高。",
                recommendation="检查不可见网格提交、LOD/occlusion culling、粒子特效包围盒和批处理后的大包围盒。",
                confidence="medium",
                extra_score=min(12, prim_rejected.avg - 2),
            )
        )

    reused = get_metric("Reused Vertices / Second", "Reused Vertices")
    if reused and reused.avg <= 0:
        issues.append(
            issue(
                title="顶点复用计数为 0",
                severity="medium",
                metric=reused,
                evidence=f"avg={fmt(reused.avg)}, max={fmt(reused.maximum)}",
                interpretation="Reused Vertices 越高越好，通常表示 indexed draws；当前没有观察到顶点复用。",
                recommendation="确认 Mesh 使用 index buffer，避免逐帧重建无法复用的网格；必要时结合 Vertices Shaded 验证。",
                confidence="medium",
                extra_score=8,
            )
        )

    alu_capacity = summary.get("% Shader ALU Capacity Utilized")
    if alu_capacity and alu_capacity.avg < 50:
        issues.append(
            issue(
                title="Shader ALU 容量利用低于理想范围",
                severity="medium",
                metric=alu_capacity,
                evidence=percent_evidence(alu_capacity),
                interpretation="% Shader ALU Capacity Utilized 理想范围为 50%-100%，当前平均值偏低。",
                recommendation="结合 Shaders Busy、Shaders Stalled、Texture Fetch Stall 判断是否受纹理、内存或同步等待限制。",
                confidence="medium",
                extra_score=min(15, (50 - alu_capacity.avg) / 2),
            )
        )

    shader_busy = summary.get("% Shaders Busy")
    if shader_busy and shader_busy.avg < 50:
        issues.append(
            issue(
                title="Shaders Busy 低于理想范围",
                severity="low",
                metric=shader_busy,
                evidence=percent_evidence(shader_busy),
                interpretation="% Shaders Busy 理想范围为 50%-100%，当前平均值偏低。",
                recommendation="结合 GPU Utilization、CPU/VSync/帧率上限和 stall 指标判断是否存在非 shader 侧限制。",
                confidence="medium",
                extra_score=min(12, (50 - shader_busy.avg) / 3),
            )
        )

    stalled = summary.get("% Shaders Stalled")
    if stalled and stalled.avg > 10:
        issues.append(
            issue(
                title="Shader stall 超过建议阈值",
                severity="high",
                metric=stalled,
                evidence=f"avg={fmt(stalled.avg, '%')}, p95={fmt(stalled.p95, '%')}, max={fmt(stalled.maximum, '%')}",
                interpretation="shader 等待比例偏高，常与纹理 fetch、缓存未命中、内存访问或长 shader 路径有关。",
                recommendation="从最贵材质和后处理开始减少采样、分支和纹理读取，并检查 Texture Fetch Stall、Texture Pipes Busy 的关联。",
                confidence="high",
                extra_score=min(12, stalled.avg - 10),
            )
        )

    time_alus = summary.get("% Time ALUs Working")
    if time_alus and time_alus.avg < 50:
        issues.append(
            issue(
                title="ALU 工作时间低于理想范围",
                severity="medium",
                metric=time_alus,
                evidence=percent_evidence(time_alus),
                interpretation="% Time ALUs Working 理想范围为 50%-100%，当前平均值偏低。",
                recommendation="结合 ALU Capacity、Shaders Busy 和 stall 指标判断 ALU 是否被纹理/内存等待掩盖。",
                confidence="medium",
                extra_score=min(15, (50 - time_alus.avg) / 2),
            )
        )

    efu = summary.get("% Time EFUs Working")
    if efu and efu.avg < 20:
        issues.append(
            issue(
                title="EFU 利用低于理想下限",
                severity="low",
                metric=efu,
                evidence=percent_evidence(efu),
                interpretation="% Time EFUs Working 理想值至少为 20%，当前平均值偏低。",
                recommendation="不要单独围绕 EFU 优化，先结合 Shaders Busy、Shaders Stalled 和纹理 stall 判断主瓶颈。",
                confidence="medium",
                extra_score=min(10, (20 - efu.avg) / 2),
            )
        )

    shading_fragments = summary.get("% Time Shading Fragments")
    if shading_fragments and shading_fragments.avg < 60:
        issues.append(
            issue(
                title="片元 shading 时间低于传统管线参考值",
                severity="low",
                metric=shading_fragments,
                evidence=percent_evidence(shading_fragments),
                interpretation="传统 vertex/fragment 管线中，% Time Shading Fragments 通常希望至少覆盖约 60% 的帧时间。",
                recommendation="结合项目是否为 compute-heavy/GPU-driven 管线判断；若不是，检查是否存在 CPU、VSync、binning 或其他阶段限制。",
                confidence="low",
                extra_score=min(10, (60 - shading_fragments.avg) / 6),
            )
        )
    elif shading_fragments and shading_fragments.p95 < 100:
        issues.append(
            issue(
                title="片元 shading 未达到 100% 峰值参考",
                severity="info",
                metric=shading_fragments,
                evidence=percent_evidence(shading_fragments),
                interpretation="传统 vertex/fragment 管线中，该指标理想情况下可能在至少 60% 帧时间内接近 100%。",
                recommendation="仅作为上下文信号使用；结合 workload 类型和其他阶段指标判断是否需要行动。",
                confidence="low",
            )
        )

    shading_vertices = summary.get("% Time Shading Vertices")
    if shading_vertices and shading_vertices.avg > 20:
        issues.append(
            issue(
                title="顶点 shading 时间高于传统管线参考值",
                severity="medium",
                metric=shading_vertices,
                evidence=percent_evidence(shading_vertices),
                interpretation="传统 vertex/fragment 管线中，% Time Shading Vertices 理想上通常低于 10%-20% 的帧时间。",
                recommendation="检查顶点数量、skinning、动态网格、LOD 和顶点 shader 成本。",
                confidence="medium",
                extra_score=min(12, (shading_vertices.avg - 20) / 2),
            )
        )

    occupancy = summary.get("% Wave Context Occupancy")
    if occupancy and occupancy.avg < 50:
        issues.append(
            issue(
                title="Wave context occupancy 低于理想下限",
                severity="medium",
                metric=occupancy,
                evidence=percent_evidence(occupancy),
                interpretation="% Wave Context Occupancy 平均值理想上至少为 50%，当前偏低。",
                recommendation="结合 shader 分支、寄存器压力、occupancy、纹理等待和 workload 分布继续定位。",
                confidence="medium",
                extra_score=min(15, (50 - occupancy.avg) / 2),
            )
        )

    full_alu = summary.get("Fragment ALU Instructions / Sec (Full)")
    half_alu = summary.get("Fragment ALU Instructions / Sec (Half)")
    if full_alu and half_alu and full_alu.avg >= half_alu.avg:
        issues.append(
            issue(
                title="Full precision 片元 ALU 指令不低于 half precision",
                severity="medium",
                metric=full_alu,
                evidence=f"full avg={fmt(full_alu.avg)}, half avg={fmt(half_alu.avg)}",
                interpretation="Fragment ALU Instructions (Full) 理想上应明显低于 Half，当前 full 不低于 half。",
                recommendation="检查片元 shader 精度声明，能接受画质差异时优先使用 half/min16float 路径。",
                confidence="medium",
                extra_score=6,
            )
        )

    texture_fetch = summary.get("% Texture Fetch Stall")
    if texture_fetch and texture_fetch.avg > 2:
        severity = "medium" if texture_fetch.avg < 16 else "high"
        issues.append(
            issue(
                title="Texture fetch stall 高于理想值",
                severity=severity,
                metric=texture_fetch,
                evidence=f"avg={fmt(texture_fetch.avg, '%')}, p95={fmt(texture_fetch.p95, '%')}, max={fmt(texture_fetch.maximum, '%')}",
                interpretation="纹理读取等待超过理想 2%，说明 shader 存在一定纹理取数等待。",
                recommendation="检查大纹理、无 mipmap、cache locality 差和过多全屏采样；优先优化被频繁采样的材质和后处理 pass。",
                confidence="medium",
                extra_score=min(10, texture_fetch.avg),
            )
        )

    system_memory = summary.get("% Stalled on System Memory")
    if system_memory and system_memory.avg > 2:
        severity = "high" if system_memory.avg >= 30 else "medium"
        issues.append(
            issue(
                title="System memory stall 高于理想阈值",
                severity=severity,
                metric=system_memory,
                evidence=percent_evidence(system_memory),
                interpretation="% Stalled on System Memory 理想上通常低于 2%；短 spike 到 30% 可接受，持续高值需要关注。",
                recommendation="检查主存访问、render target 读写、带宽压力、纹理/缓冲区访问模式和同步等待。",
                confidence="medium",
                extra_score=min(12, system_memory.avg - 2),
            )
        )

    texture_l1 = summary.get("% Texture L1 Miss")
    if texture_l1 and texture_l1.avg >= 50:
        issues.append(
            issue(
                title="Texture L1 miss 高于理想范围",
                severity="medium",
                metric=texture_l1,
                evidence=percent_evidence(texture_l1),
                interpretation="% Texture L1 Miss 理想上应在 0%-50% 以下波动，当前平均值不低于 50%。",
                recommendation="检查 mipmap、纹理局部性、大纹理访问、过多随机采样和后处理全屏采样。",
                confidence="medium",
                extra_score=min(12, (texture_l1.avg - 50) / 2),
            )
        )

    texture_l2 = summary.get("% Texture L2 Miss")
    if texture_l2 and texture_l2.avg >= 40:
        issues.append(
            issue(
                title="Texture L2 miss 高于理想范围",
                severity="medium",
                metric=texture_l2,
                evidence=percent_evidence(texture_l2),
                interpretation="% Texture L2 Miss 理想上应在 0%-40% 以下波动，当前平均值不低于 40%。",
                recommendation="检查纹理工作集大小、压缩格式、mipmap、cache locality 和频繁采样路径。",
                confidence="medium",
                extra_score=min(12, (texture_l2.avg - 40) / 2),
            )
        )

    vertex_fetch = summary.get("% Vertex Fetch Stall")
    if vertex_fetch and (vertex_fetch.avg > 0 or vertex_fetch.maximum > 70):
        severity = "high" if vertex_fetch.maximum > 70 else "low"
        issues.append(
            issue(
                title="Vertex fetch stall 高于理想状态",
                severity=severity,
                metric=vertex_fetch,
                evidence=percent_evidence(vertex_fetch),
                interpretation="% Vertex Fetch Stall 理想上通常为 0%，且偶发 spike 不应超过 70%。",
                recommendation="检查顶点缓冲布局、动态网格上传、skinning、顶点数据带宽和 vertex cache 使用。",
                confidence="medium",
                extra_score=min(12, vertex_fetch.maximum / 10),
            )
        )

    cp_overhead = summary.get("% CP Overhead")
    if cp_overhead and cp_overhead.maximum > 20:
        issues.append(
            issue(
                title="CP Overhead 超过不可接受上限",
                severity="high",
                metric=cp_overhead,
                evidence=percent_evidence(cp_overhead),
                interpretation="% CP Overhead 应接近 0%，且不应超过 20%；当前最大值超过 20%。",
                recommendation="检查命令提交、driver overhead、draw call 数量、状态切换和同步点。",
                confidence="high",
                extra_score=min(15, cp_overhead.maximum - 20),
            )
        )

    for metric_name, title, recommendation in [
        (
            "% Linear Filtered",
            "Linear filtered 采样占比较高",
            "线性过滤有成本；检查高频采样材质、后处理、全屏 pass、mipmap 和分辨率。",
        ),
        (
            "% Texture Pipes Busy",
            "Texture pipes busy 偏高",
            "该指标需结合画质目标和其他 stall 判断；优先检查纹理采样次数、RenderTexture 分辨率和全屏 blit 链。",
        ),
    ]:
        metric = summary.get(metric_name)
        if metric and metric.avg > 0:
            issues.append(
                issue(
                    title=title,
                    severity="info",
                    metric=metric,
                    evidence=percent_evidence(metric),
                    interpretation="EvaluatePrompts.md 对该指标给出方向性建议，但没有稳定的硬阈值。",
                    recommendation=recommendation,
                    confidence="low",
                    extra_score=min(10, metric.avg / 10),
                )
            )

    for metric_name, title, recommendation in [
        (
            "% Nearest Filtered",
            "Nearest filtered 占比较低",
            "Nearest filtering 通常更便宜；是否可提高占比取决于画质需求。",
        ),
        (
            "% Time Compute",
            "Compute 活跃度偏低",
            "Compute workload 的理想值随应用差异很大；仅在预期 compute-heavy 时继续检查。",
        ),
    ]:
        metric = summary.get(metric_name)
        if metric and metric.avg <= 0:
            issues.append(
                issue(
                    title=title,
                    severity="info",
                    metric=metric,
                    evidence=percent_evidence(metric),
                    interpretation="EvaluatePrompts.md 对该指标给出上下文相关建议，没有通用硬阈值。",
                    recommendation=recommendation,
                    confidence="low",
                )
            )

    concurrent_binning = summary.get("Concurrent binning")
    if concurrent_binning and concurrent_binning.avg > 30:
        issues.append(
            issue(
                title="Binning 阶段占比过高",
                severity="medium",
                metric=concurrent_binning,
                evidence=percent_evidence(concurrent_binning),
                interpretation="EvaluatePrompts.md 建议 binning pass 占 renderpass 的 10%-20%，30% 通常过高。",
                recommendation="检查几何量、顶点读取、不可见物体提交、renderpass 结构和 binning 相关 workload。",
                confidence="medium",
                extra_score=min(12, concurrent_binning.avg - 30),
            )
        )

    return sorted(issues, key=lambda item: item.score, reverse=True)


def build_context(summary: ProfileSummary, issues: list[BottleneckIssue], top_n: int) -> dict[str, object]:
    fps = summary.get("FPS")
    return {
        "summary": {
            "source": summary.source,
            "source_files": summary.source_files,
            "row_count": summary.row_count,
            "process_names": summary.process_names,
            "categories": summary.categories,
            "duration_seconds": summary.duration_seconds,
            "fps": fps.to_dict() if fps else None,
        },
        "rule_issues": [item.to_dict() for item in issues[:top_n]],
        "metric_stats": [metric.to_dict() for metric in summary.metrics.values()],
        "data_boundaries": [
            "Only aggregated CSV counters are available.",
            "Do not infer device model, SoC, target FPS, temperature, power, draw calls, materials, shaders, or GameObjects unless present in metrics.",
            "Aggregated counters can suggest bottleneck direction, not exact object/material/drawcall root cause.",
        ],
    }


def render_rule_report(summary: ProfileSummary, issues: list[BottleneckIssue], *, top_n: int = 8) -> str:
    lines: list[str] = []
    lines.append("# Snapdragon Profiler 瓶颈分析")
    lines.append("")
    lines.append("## 概览")
    lines.append(f"- 数据源: `{summary.source}`")
    if summary.source_files:
        lines.append(f"- 输入文件数: {len(summary.source_files)}")
        for source_file in summary.source_files:
            lines.append(f"  - `{source_file}`")
    lines.append(f"- 数据行: {summary.row_count}")
    if summary.process_names:
        lines.append(f"- 进程: {', '.join(summary.process_names)}")
    if summary.duration_seconds is not None:
        lines.append(f"- 捕获时长: {summary.duration_seconds:.2f}s")

    fps = summary.get("FPS")
    if fps:
        lines.append(
            f"- FPS: avg={fps.avg:.2f}, p50={fps.p50:.2f}, p95={fps.p95:.2f}, "
            f"min={fps.minimum:.2f}, max={fps.maximum:.2f}"
        )
    lines.append("")

    lines.append("## Top 瓶颈")
    if issues:
        for index, item in enumerate(issues[:top_n], start=1):
            lines.append(f"{index}. **{item.title}** ({item.severity}, confidence={item.confidence})")
            lines.append(f"   - 指标: `{item.metric}`")
            lines.append(f"   - 证据: {item.evidence}")
            lines.append(f"   - 判断: {item.interpretation}")
            lines.append(f"   - 建议: {item.recommendation}")
    else:
        lines.append("- 未命中内置阈值规则；建议补充更多 counter 或扩大采样窗口。")
    lines.append("")

    lines.append("## 关键指标快照")
    key_metrics = [
        "FPS",
        "GPU % Utilization",
        "Average Polygon Area",
        "% Prims Clipped",
        "% Prims Trivially Rejected",
        "Reused Vertices / Second",
        "% Shader ALU Capacity Utilized",
        "% Shaders Busy",
        "% Shaders Stalled",
        "% Time ALUs Working",
        "% Time EFUs Working",
        "% Time Shading Fragments",
        "% Time Shading Vertices",
        "% Wave Context Occupancy",
        "Fragment ALU Instructions / Sec (Full)",
        "Fragment ALU Instructions / Sec (Half)",
        "% Linear Filtered",
        "% Nearest Filtered",
        "% Texture Pipes Busy",
        "% Texture Fetch Stall",
        "% Stalled on System Memory",
        "% Texture L1 Miss",
        "% Texture L2 Miss",
        "% Vertex Fetch Stall",
        "% CP Overhead",
        "Concurrent binning",
        "GPU % Bus Busy",
        "Texture Memory Read BW (Bytes/Second)",
        "Vertex Memory Read (Bytes/Second)",
        "Write Total (Bytes/sec)",
    ]
    for name in key_metrics:
        metric = summary.get(name)
        if metric:
            lines.append(
                f"- `{metric.name}`: avg={metric.avg:.2f}, p95={metric.p95:.2f}, "
                f"min={metric.minimum:.2f}, max={metric.maximum:.2f}, n={metric.count}"
            )
    lines.append("")

    lines.append("## 数据缺口")
    lines.append("- 当前 CSV 是 counter 聚合数据，不能直接定位到具体材质、Renderer Feature、drawcall 或 GameObject。")
    lines.append("- 下一步应结合 Unity Profiler、Frame Debugger、RenderDoc 或 Snapdragon Profiler per-draw 数据验证。")
    lines.append("- 如 GPU 利用率不高但 FPS 仍低，需要补充 CPU、VSync、温控、目标帧率相关数据。")
    return "\n".join(lines).rstrip() + "\n"


def read_trace_rows(path: Path) -> list[TraceRow]:
    if not path.exists():
        raise CsvLoadError(f"Trace CSV 文件不存在: {path}")
    if not path.is_file():
        raise CsvLoadError(f"Trace CSV 路径不是文件: {path}")

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise CsvLoadError("Trace CSV 为空或缺少表头")

        normalized_fields = {(name or "").lstrip("\ufeff").strip() for name in reader.fieldnames}
        missing = TRACE_REQUIRED_COLUMNS.difference(normalized_fields)
        if missing:
            raise CsvLoadError(f"Trace CSV 缺少必要列: {', '.join(sorted(missing))}")

        rows: list[TraceRow] = []
        for index, raw in enumerate(reader, start=2):
            normalized = {
                (key or "").lstrip("\ufeff").strip(): (value or "")
                for key, value in raw.items()
            }
            if not any(value.strip() for value in normalized.values()):
                continue

            end_text = normalized.get("TimestampEnd", "").strip()
            value_text = normalized.get("Value", "").strip()
            value = parse_float(value_text, row_number=index, column="Value") if value_text else None
            if value is not None and not math.isfinite(value):
                raise CsvLoadError(f"第 {index} 行 `Value` 不是有限数值: {value_text!r}")

            rows.append(
                TraceRow(
                    process=normalized.get("Process", "").strip(),
                    group=normalized.get("Group", "").strip(),
                    process_id=normalized.get("Process ID", "").strip(),
                    context_id=normalized.get("Context ID", "").strip(),
                    thread_id=normalized.get("Thread ID", "").strip(),
                    track=normalized.get("Track", "").strip(),
                    block_name=normalized.get("Block Name", "").strip(),
                    timestamp_start=parse_int(
                        normalized.get("TimestampStart", "").strip(),
                        row_number=index,
                        column="TimestampStart",
                    ),
                    timestamp_end=(
                        parse_int(end_text, row_number=index, column="TimestampEnd")
                        if end_text
                        else None
                    ),
                    value=value,
                    extra_data=normalized.get("Extra Data", "").strip(),
                )
            )

    if not rows:
        raise CsvLoadError("Trace CSV 没有数据行")
    return rows


def trace_duration_us(row: TraceRow) -> int:
    if row.timestamp_end is None:
        return 0
    return max(0, row.timestamp_end - row.timestamp_start)


def is_gpu_stage_row(row: TraceRow) -> bool:
    if "gpu" in row.group.casefold():
        return True
    return row.track.casefold() in {
        "surfaces",
        "render",
        "binning",
        "gmem load color",
        "gmem load depth stencil",
        "gmem store color",
    }


def interval_union_us(intervals: Iterable[tuple[int, int]]) -> int:
    ordered = sorted((start, end) for start, end in intervals if end > start)
    if not ordered:
        return 0

    total = 0
    current_start, current_end = ordered[0]
    for start, end in ordered[1:]:
        if start <= current_end:
            current_end = max(current_end, end)
            continue
        total += current_end - current_start
        current_start, current_end = start, end
    return total + current_end - current_start


def trace_metric_summary(rows: list[TraceRow], source: Path) -> ProfileSummary | None:
    metric_rows = [row for row in rows if row.value is not None and (row.track or row.block_name)]
    if not metric_rows:
        return None

    capture_start = min(row.timestamp_start for row in rows)
    capture_end = max(row.timestamp_end if row.timestamp_end is not None else row.timestamp_start for row in rows)
    rows_by_metric: dict[str, list[TraceRow]] = defaultdict(list)
    raw_names_by_metric: dict[str, set[str]] = defaultdict(set)
    categories_by_metric: dict[str, Counter[str]] = defaultdict(Counter)
    for row in metric_rows:
        raw_name = row.track or row.block_name
        name = normalize_metric_name(raw_name)
        rows_by_metric[name].append(row)
        raw_names_by_metric[name].add(raw_name)
        categories_by_metric[name][row.group] += 1

    metrics: dict[str, MetricStats] = {}
    for name, samples in rows_by_metric.items():
        ordered_samples = sorted(samples, key=lambda item: item.timestamp_start)
        weighted_values: list[tuple[float, float]] = []
        for index, sample in enumerate(ordered_samples):
            next_timestamp = (
                ordered_samples[index + 1].timestamp_start
                if index + 1 < len(ordered_samples)
                else capture_end
            )
            weight = float(max(0, next_timestamp - sample.timestamp_start))
            weighted_values.append((float(sample.value), weight))

        total_weight = sum(weight for _, weight in weighted_values)
        values = [value for value, _ in weighted_values]
        if total_weight <= 0:
            weighted_values = [(value, 1.0) for value in values]
            total_weight = float(len(weighted_values))

        def weighted_percentile(q: float) -> float:
            threshold = total_weight * q
            cumulative = 0.0
            for value, weight in sorted(weighted_values, key=lambda item: item[0]):
                cumulative += weight
                if cumulative >= threshold:
                    return value
            return max(values)

        metrics[name] = MetricStats(
            name=name,
            category=categories_by_metric[name].most_common(1)[0][0],
            count=len(values),
            avg=sum(value * weight for value, weight in weighted_values) / total_weight,
            minimum=min(values),
            p50=weighted_percentile(0.50),
            p95=weighted_percentile(0.95),
            maximum=max(values),
            raw_names=tuple(sorted(raw_names_by_metric[name])),
        )

    return ProfileSummary(
        source=str(source),
        source_files=(str(source),),
        row_count=len(metric_rows),
        process_names=tuple(sorted({row.process_id or row.process for row in metric_rows if row.process_id or row.process})),
        categories=tuple(name for name, _ in Counter(row.group for row in metric_rows).most_common()),
        duration_seconds=(capture_end - capture_start) / 1_000_000.0,
        metrics=dict(sorted(metrics.items())),
    )


def summarize_trace_stages(rows: list[TraceRow], capture_span_us: int) -> list[dict[str, object]]:
    durations: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in rows:
        duration = trace_duration_us(row)
        if duration <= 0:
            continue
        track = row.track or row.block_name or "(unnamed)"
        durations[(row.group or "(ungrouped)", track)].append(float(duration))

    result: list[dict[str, object]] = []
    for (group, track), values in durations.items():
        ordered = sorted(values)
        total_us = sum(values)
        result.append(
            {
                "group": group,
                "track": track,
                "count": len(values),
                "total_ms": round(total_us / 1000.0, 6),
                "avg_ms": round((total_us / len(values)) / 1000.0, 6),
                "p50_ms": round(percentile(ordered, 0.50) / 1000.0, 6),
                "p95_ms": round(percentile(ordered, 0.95) / 1000.0, 6),
                "max_ms": round(ordered[-1] / 1000.0, 6),
                "summed_capture_pct": round(total_us / capture_span_us * 100.0, 4) if capture_span_us else 0.0,
            }
        )
    return sorted(result, key=lambda item: (float(item["total_ms"]), int(item["count"])), reverse=True)


def extract_flush_reasons(rows: list[TraceRow]) -> list[dict[str, object]]:
    reasons: Counter[str] = Counter()
    for row in rows:
        match = FLUSH_REASON_RE.search(row.extra_data)
        if match:
            reasons[match.group(1)] += 1
    return [{"reason": reason, "count": count} for reason, count in reasons.most_common()]


def regex_value(pattern: re.Pattern[str], text: str, default: str = "") -> str:
    match = pattern.search(text)
    return match.group(1).strip() if match else default


def extract_surface_configs(rows: list[TraceRow]) -> list[dict[str, object]]:
    configs: dict[tuple[str, ...], dict[str, object]] = {}
    for row in rows:
        if row.track.casefold() != "surfaces" and not row.block_name.casefold().startswith("surface"):
            continue
        width = regex_value(SURFACE_WIDTH_RE, row.extra_data)
        height = regex_value(SURFACE_HEIGHT_RE, row.extra_data)
        if not width or not height:
            dimensions = re.search(r"Surface\s*\[(\d+)\s*,\s*(\d+)\]", row.block_name, re.IGNORECASE)
            if dimensions:
                width, height = dimensions.group(1), dimensions.group(2)
        key = (
            width,
            height,
            regex_value(SURFACE_RENDER_MODE_RE, row.extra_data, "unknown"),
            regex_value(SURFACE_MSAA_RE, row.extra_data),
            regex_value(SURFACE_BINS_RE, row.extra_data),
            regex_value(SURFACE_BIN_WIDTH_RE, row.extra_data),
            regex_value(SURFACE_BIN_HEIGHT_RE, row.extra_data),
        )
        config = configs.setdefault(
            key,
            {
                "width": width,
                "height": height,
                "render_mode": key[2],
                "msaa": key[3],
                "number_of_bins": key[4],
                "bin_width": key[5],
                "bin_height": key[6],
                "count": 0,
                "total_ms": 0.0,
            },
        )
        config["count"] = int(config["count"]) + 1
        config["total_ms"] = float(config["total_ms"]) + trace_duration_us(row) / 1000.0

    result = []
    for config in configs.values():
        config["total_ms"] = round(float(config["total_ms"]), 6)
        result.append(config)
    return sorted(result, key=lambda item: (int(item["count"]), float(item["total_ms"])), reverse=True)


def build_trace_frames(rows: list[TraceRow], stage_rows: list[TraceRow]) -> list[dict[str, object]]:
    swap_timestamps = sorted(
        {
            row.timestamp_start
            for row in rows
            if (match := FLUSH_REASON_RE.search(row.extra_data)) and match.group(1).casefold() == "swap"
        }
    )
    frames: list[dict[str, object]] = []
    for frame_index, (start, end) in enumerate(zip(swap_timestamps, swap_timestamps[1:]), start=1):
        if end <= start:
            continue
        clipped_intervals: list[tuple[int, int]] = []
        duration_by_track: Counter[str] = Counter()
        event_count = 0
        for row in stage_rows:
            row_end = row.timestamp_end or row.timestamp_start
            overlap_start = max(start, row.timestamp_start)
            overlap_end = min(end, row_end)
            if overlap_end <= overlap_start:
                continue
            duration = overlap_end - overlap_start
            clipped_intervals.append((overlap_start, overlap_end))
            duration_by_track[row.track or row.block_name or "(unnamed)"] += duration
            event_count += 1

        interval_us = end - start
        active_us = interval_union_us(clipped_intervals)

        def total_for(prefix: str, *, exact: bool = False) -> int:
            prefix_folded = prefix.casefold()
            return sum(
                value
                for track, value in duration_by_track.items()
                if (track.casefold() == prefix_folded if exact else track.casefold().startswith(prefix_folded))
            )

        frames.append(
            {
                "swap_interval_index": frame_index,
                "start_us": start,
                "end_us": end,
                "swap_interval_ms": round(interval_us / 1000.0, 6),
                "observed_swap_rate_hz": round(1_000_000.0 / interval_us, 4),
                "gpu_active_ms": round(active_us / 1000.0, 6),
                "gpu_active_pct": round(active_us / interval_us * 100.0, 4),
                "stage_event_count": event_count,
                "render_ms": round(total_for("render", exact=True) / 1000.0, 6),
                "binning_ms": round(total_for("binning", exact=True) / 1000.0, 6),
                "gmem_load_ms": round(total_for("gmem load") / 1000.0, 6),
                "gmem_store_ms": round(total_for("gmem store") / 1000.0, 6),
            }
        )
    return frames


def trace_frame_stats(frames: list[dict[str, object]]) -> dict[str, object] | None:
    if not frames:
        return None
    values = [float(row["swap_interval_ms"]) for row in frames]
    ordered = sorted(values)
    return {
        "count": len(values),
        "avg_ms": sum(values) / len(values),
        "p50_ms": percentile(ordered, 0.50),
        "p95_ms": percentile(ordered, 0.95),
        "max_ms": ordered[-1],
        "min_ms": ordered[0],
    }


def trace_issue(
    *,
    title: str,
    severity: str,
    metric: str,
    evidence: str,
    interpretation: str,
    recommendation: str,
    confidence: str,
    extra_score: float = 0.0,
) -> dict[str, object]:
    return {
        "title": title,
        "severity": severity,
        "score": SEVERITY_SCORE[severity] + extra_score,
        "metric": metric,
        "evidence": evidence,
        "interpretation": interpretation,
        "recommendation": recommendation,
        "confidence": confidence,
    }


def evaluate_trace_rules(
    metric_summary: ProfileSummary | None,
    stage_stats: list[dict[str, object]],
    frames: list[dict[str, object]],
) -> list[dict[str, object]]:
    issues = [item.to_dict() for item in evaluate_rules(metric_summary)] if metric_summary else []
    stage_by_name = {str(item["track"]).casefold(): item for item in stage_stats}
    binning = stage_by_name.get("binning")
    render = stage_by_name.get("render")
    if binning and render:
        binning_ms = float(binning["total_ms"])
        render_ms = float(render["total_ms"])
        denominator = binning_ms + render_ms
        share = binning_ms / denominator * 100.0 if denominator else 0.0
        if share > 30.0:
            issues.append(
                trace_issue(
                    title="Binning 阶段时长占比偏高",
                    severity="medium",
                    metric="Binning / (Binning + Render)",
                    evidence=f"binning={binning_ms:.3f} ms, render={render_ms:.3f} ms, share={share:.2f}%",
                    interpretation="Trace 中 Binning 汇总时长超过 EvaluatePrompts.md 的 30% 关注线。",
                    recommendation="检查几何量、不可见物体提交、顶点读取、LOD 和 render pass 结构。",
                    confidence="medium",
                    extra_score=min(12.0, share - 30.0),
                )
            )

    frame_stats = trace_frame_stats(frames)
    if frame_stats:
        p50 = float(frame_stats["p50_ms"])
        p95 = float(frame_stats["p95_ms"])
        maximum = float(frame_stats["max_ms"])
        if p50 > 0 and p95 >= p50 * 1.25 and p95 - p50 >= 2.0:
            severity = "medium" if p95 >= p50 * 1.75 else "low"
            issues.append(
                trace_issue(
                    title="Swap 间隔波动明显",
                    severity=severity,
                    metric="Swap interval",
                    evidence=f"p50={p50:.3f} ms, p95={p95:.3f} ms, max={maximum:.3f} ms",
                    interpretation="观测到的 Swap 间隔尾部明显高于中位数，提示 frame pacing 波动；它不等同于纯 GPU 帧耗时。",
                    recommendation="对齐最长 Swap 区间的 CPU 调度、Render/Binning 阶段和同步事件继续定位。",
                    confidence="medium",
                    extra_score=min(12.0, (p95 / p50 - 1.0) * 10.0),
                )
            )

    for stage in stage_stats:
        p95 = float(stage["p95_ms"])
        maximum = float(stage["max_ms"])
        if p95 > 0 and maximum >= p95 * 3.0 and maximum - p95 >= 1.0:
            issues.append(
                trace_issue(
                    title=f"{stage['track']} 存在长尾事件",
                    severity="low",
                    metric=str(stage["track"]),
                    evidence=f"p95={p95:.3f} ms, max={maximum:.3f} ms, count={stage['count']}",
                    interpretation="该阶段最大耗时显著高于自身 p95，提示偶发长事件。",
                    recommendation="在 longest events 和对应 Swap 区间中检查上下游阶段及同步事件。",
                    confidence="low",
                    extra_score=min(10.0, maximum / p95),
                )
            )

    return sorted(issues, key=lambda item: float(item["score"]), reverse=True)


def markdown_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_trace_report(context: dict[str, object], top_n: int) -> str:
    summary = context["summary"]
    stage_stats = context["stage_stats"]
    frame_stats = context["swap_interval_stats"]
    issues = context["rule_issues"]
    longest_events = context["longest_events"]
    flush_reasons = context["flush_reasons"]
    surface_configs = context["surface_configs"]

    lines = [
        "# Snapdragon Profiler Trace Capture 分析",
        "",
        "## 概览",
        "",
        f"- 数据源: `{summary['source']}`",
        f"- 数据行: {summary['row_count']}",
        f"- 捕获时长: {summary['duration_seconds']:.3f} s",
        f"- 有时长的 Trace 事件: {summary['stage_event_count']}",
        f"- GPU 阶段事件: {summary['gpu_stage_event_count']}",
        f"- Counter 样本: {summary['metric_sample_count']}",
        f"- GPU 活跃区间并集: {summary['gpu_active_ms']:.3f} ms ({summary['gpu_active_pct']:.2f}%)",
    ]
    if summary["process_ids"]:
        lines.append(f"- Process ID: {', '.join(summary['process_ids'])}")
    if summary["context_ids"]:
        lines.append(f"- Context ID: {', '.join(summary['context_ids'])}")
    if frame_stats:
        lines.extend(
            [
                f"- Swap 区间: {frame_stats['count']}",
                f"- Swap interval: avg={frame_stats['avg_ms']:.3f} ms, p50={frame_stats['p50_ms']:.3f} ms, "
                f"p95={frame_stats['p95_ms']:.3f} ms, max={frame_stats['max_ms']:.3f} ms",
            ]
        )
    lines.extend(["", "## 主要诊断", ""])
    if issues:
        for index, item in enumerate(issues[:top_n], start=1):
            lines.extend(
                [
                    f"{index}. **{item['title']}** ({item['severity']}, confidence={item['confidence']})",
                    f"   - 指标: `{item['metric']}`",
                    f"   - 证据: {item['evidence']}",
                    f"   - 判断: {item['interpretation']}",
                    f"   - 建议: {item['recommendation']}",
                ]
            )
    else:
        lines.append("- 未命中内置规则；仍需结合目标帧率和 CPU/系统 Trace 判断。")

    lines.extend(
        [
            "",
            "## Trace 阶段汇总",
            "",
            "| Group | Track | Count | Total ms* | Avg ms | P95 ms | Max ms |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for item in stage_stats[:30]:
        lines.append(
            f"| {markdown_cell(item['group'])} | {markdown_cell(item['track'])} | {item['count']} | "
            f"{item['total_ms']} | {item['avg_ms']} | {item['p95_ms']} | {item['max_ms']} |"
        )
    lines.extend(["", "> Total ms 为事件时长求和；不同 Track 可能重叠，不能直接相加为 GPU 帧耗时。", ""])

    lines.extend(
        [
            "## 最长事件",
            "",
            "| Group | Track | Block | Start us | Duration ms |",
            "|---|---|---|---:|---:|",
        ]
    )
    for item in longest_events[:20]:
        lines.append(
            f"| {markdown_cell(item['group'])} | {markdown_cell(item['track'])} | "
            f"{markdown_cell(item['block_name'])} | {item['timestamp_start_us']} | {item['duration_ms']} |"
        )

    lines.extend(["", "## Flush 原因", ""])
    if flush_reasons:
        for item in flush_reasons:
            lines.append(f"- `{item['reason']}`: {item['count']}")
    else:
        lines.append("- CSV 中没有可解析的 Flush Reason。")

    lines.extend(["", "## Surface 配置", ""])
    if surface_configs:
        lines.extend(
            [
                "| Resolution | Mode | MSAA | Bins | Bin Size | Count | Total ms |",
                "|---|---|---:|---:|---|---:|---:|",
            ]
        )
        for item in surface_configs[:20]:
            lines.append(
                f"| {item['width']}x{item['height']} | {markdown_cell(item['render_mode'])} | {item['msaa']} | "
                f"{item['number_of_bins']} | {item['bin_width']}x{item['bin_height']} | {item['count']} | {item['total_ms']} |"
            )
    else:
        lines.append("- CSV 中没有 Surface 配置事件。")

    lines.extend(
        [
            "",
            "## 数据边界",
            "",
            "- 时间戳按 Snapdragon Profiler Trace CSV 的微秒值解释。",
            "- Swap 间隔是观测到的提交/交换间隔，不等同于纯 GPU frame time，也不能单独证明 GPU-bound。",
            "- Counter 是 Trace 事件采样值；与 Realtime 的固定周期采样口径可能不同。",
            "- Trace 聚合可以定位阶段和时间区间，不能直接证明具体材质、Shader、Draw Call 或 GameObject 根因。",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def analyze_trace(source: Path, out_dir: Path, top_events: int, top_issues: int) -> dict[str, object]:
    rows = read_trace_rows(source)
    capture_start = min(row.timestamp_start for row in rows)
    capture_end = max(row.timestamp_end if row.timestamp_end is not None else row.timestamp_start for row in rows)
    capture_span_us = max(0, capture_end - capture_start)
    stage_rows = [row for row in rows if trace_duration_us(row) > 0 and row.group.casefold() != "metrics"]
    gpu_stage_rows = [row for row in stage_rows if is_gpu_stage_row(row)]
    stage_stats = summarize_trace_stages(stage_rows, capture_span_us)
    frames = build_trace_frames(rows, gpu_stage_rows)
    metric_summary = trace_metric_summary(rows, source)
    issues = evaluate_trace_rules(metric_summary, stage_stats, frames)
    flush_reasons = extract_flush_reasons(rows)
    surface_configs = extract_surface_configs(rows)
    active_us = interval_union_us(
        (row.timestamp_start, row.timestamp_end or row.timestamp_start) for row in gpu_stage_rows
    )

    longest_events = [
        {
            "group": row.group,
            "track": row.track,
            "block_name": row.block_name,
            "process_id": row.process_id,
            "context_id": row.context_id,
            "thread_id": row.thread_id,
            "timestamp_start_us": row.timestamp_start,
            "timestamp_end_us": row.timestamp_end,
            "duration_ms": round(trace_duration_us(row) / 1000.0, 6),
        }
        for row in sorted(stage_rows, key=trace_duration_us, reverse=True)[:top_events]
    ]

    metric_stats = [metric.to_dict() for metric in metric_summary.metrics.values()] if metric_summary else []
    metric_rows = []
    for item in metric_stats:
        metric_rows.append(
            {
                "name": item["name"],
                "category": item["category"],
                "count": item["count"],
                "avg": item["avg"],
                "min": item["min"],
                "p50": item["p50"],
                "p95": item["p95"],
                "max": item["max"],
                "raw_names": " | ".join(item["raw_names"]),
            }
        )

    context = {
        "summary": {
            "capture_mode": "trace",
            "source": str(source),
            "row_count": len(rows),
            "capture_start_us": capture_start,
            "capture_end_us": capture_end,
            "duration_seconds": capture_span_us / 1_000_000.0,
            "stage_event_count": len(stage_rows),
            "gpu_stage_event_count": len(gpu_stage_rows),
            "metric_sample_count": sum(1 for row in rows if row.value is not None),
            "gpu_active_ms": active_us / 1000.0,
            "gpu_active_pct": active_us / capture_span_us * 100.0 if capture_span_us else 0.0,
            "process_ids": sorted({row.process_id for row in rows if row.process_id}),
            "context_ids": sorted({row.context_id for row in rows if row.context_id}),
            "thread_ids": sorted({row.thread_id for row in rows if row.thread_id}),
            "groups": [name for name, _ in Counter(row.group for row in rows).most_common()],
            "tracks": [name for name, _ in Counter(row.track for row in rows).most_common()],
        },
        "rule_issues": issues[:top_issues],
        "stage_stats": stage_stats,
        "swap_interval_stats": trace_frame_stats(frames),
        "worst_swap_intervals": sorted(frames, key=lambda item: float(item["swap_interval_ms"]), reverse=True)[:100],
        "metric_stats": metric_stats,
        "longest_events": longest_events,
        "flush_reasons": flush_reasons,
        "surface_configs": surface_configs,
        "data_boundaries": [
            "Trace timestamps are interpreted as microseconds.",
            "Swap intervals are presentation/submission intervals, not pure GPU frame times.",
            "Trace counter samples may use event-driven sampling and are not guaranteed to match Realtime sampling semantics.",
            "Summed track durations can overlap and must not be added as total GPU frame time.",
            "Trace aggregation cannot identify a specific material, shader, draw call, or GameObject without per-draw or project evidence.",
        ],
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    stage_path = out_dir / "trace_stage_summary.csv"
    frame_path = out_dir / "trace_swap_interval_summary.csv"
    metric_path = out_dir / "trace_metric_summary.csv"
    event_path = out_dir / "trace_longest_events.csv"
    surface_path = out_dir / "trace_surface_summary.csv"
    report_path = out_dir / "trace_summary.md"

    write_dict_csv(
        stage_path,
        ["group", "track", "count", "total_ms", "avg_ms", "p50_ms", "p95_ms", "max_ms", "summed_capture_pct"],
        stage_stats,
    )
    write_dict_csv(
        frame_path,
        [
            "swap_interval_index",
            "start_us",
            "end_us",
            "swap_interval_ms",
            "observed_swap_rate_hz",
            "gpu_active_ms",
            "gpu_active_pct",
            "stage_event_count",
            "render_ms",
            "binning_ms",
            "gmem_load_ms",
            "gmem_store_ms",
        ],
        frames,
    )
    write_dict_csv(
        metric_path,
        ["name", "category", "count", "avg", "min", "p50", "p95", "max", "raw_names"],
        metric_rows,
    )
    write_dict_csv(
        event_path,
        [
            "group",
            "track",
            "block_name",
            "process_id",
            "context_id",
            "thread_id",
            "timestamp_start_us",
            "timestamp_end_us",
            "duration_ms",
        ],
        longest_events,
    )
    write_dict_csv(
        surface_path,
        ["width", "height", "render_mode", "msaa", "number_of_bins", "bin_width", "bin_height", "count", "total_ms"],
        surface_configs,
    )
    report_path.write_text(render_trace_report(context, top_issues), encoding="utf-8")

    return {
        "context": context,
        "report_path": report_path,
        "stage_path": stage_path,
        "frame_path": frame_path,
        "metric_path": metric_path,
        "event_path": event_path,
        "surface_path": surface_path,
    }


UNITY_CPU_REQUIRED_COLUMNS = {
    "frame",
    "thread",
    "sample",
    "depth",
    "startMs",
    "durationMs",
}

UNITY_CPU_DETAILED_REQUIRED_COLUMNS = {
    "FrameIndex",
    "ThreadName",
    "SampleName",
    "Depth",
    "StartTimeMs",
    "DurationMs",
}


@dataclass
class CpuOpenSample:
    frame: int
    thread: str
    sample: str
    depth: int
    inclusive_ms: float
    supplied_self_ms: float | None = None
    gc_alloc_bytes: int = 0
    child_ms: float = 0.0


def percentile_values(values: list[float], q: float) -> float:
    return percentile(sorted(values), q) if values else math.nan


def classify_cpu_marker(sample: str) -> str:
    if not sample:
        return "ThreadRoot"
    name = sample.lower()
    if name == "idle" or "wait" in name or "semaphore" in name:
        return "Waiting"
    if "gc.alloc" in name or "garbagecollect" in name or "gc." in name:
        return "GC"
    if "script" in name or "behaviour" in name or "mono" in name or (".dll!" in name and "[invoke]" in name):
        return "Scripts"
    if "physics" in name or "physx" in name or "pxscene" in name:
        return "Physics"
    if "canvas" in name or "layout" in name or "graphic" in name or "ugui" in name or "ui." in name:
        return "UI"
    if "anim" in name:
        return "Animation"
    if "render" in name or "camera" in name or "gfx" in name or "draw" in name or "cull" in name or "particle" in name:
        return "Rendering"
    if "audio" in name:
        return "Audio"
    if "load" in name or "asset" in name or "resource" in name:
        return "Loading"
    if "profiler" in name:
        return "Profiler"
    return "Other"


def is_reportable_cpu_marker(sample: str) -> bool:
    if not sample:
        return False
    if sample in {"Main Thread", "Render Thread", "Idle", "Semaphore.WaitForSignal"}:
        return False
    if sample.startswith("Profiler."):
        return False
    return True


def is_main_thread(thread: str) -> bool:
    return thread.strip().lower() == "main thread"


def is_render_thread(thread: str) -> bool:
    return "render" in thread.strip().lower()


def parse_optional_cpu_float(raw: dict[str, str], column: str, row_number: int) -> float | None:
    text = (raw.get(column) or "").strip()
    if not text:
        return None
    value = parse_float(text, row_number=row_number, column=column)
    if not math.isfinite(value):
        raise CsvLoadError(f"第 {row_number} 行 `{column}` 必须是有限数值: {text!r}")
    return value


def parse_optional_cpu_int(raw: dict[str, str], column: str, row_number: int) -> int | None:
    text = (raw.get(column) or "").strip()
    if not text:
        return None
    return parse_int(text, row_number=row_number, column=column)


def read_unity_cpu_rows(path: Path) -> Iterable[dict[str, object]]:
    if not path.exists():
        raise CsvLoadError(f"CSV 文件不存在: {path}")
    if not path.is_file():
        raise CsvLoadError(f"路径不是文件: {path}")

    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames is None:
            raise CsvLoadError("CSV 为空或缺少表头")
        raw_headers = list(reader.fieldnames)
        headers = [re.sub(r"\s+", " ", (name or "").strip()) for name in raw_headers]
        if any(not name for name in headers):
            raise CsvLoadError("Unity CPU CSV 包含空表头")
        duplicates = sorted(name for name, count in Counter(headers).items() if count > 1)
        if duplicates:
            raise CsvLoadError(f"Unity CPU CSV 去除空白后存在重复表头: {', '.join(duplicates)}")

        header_map = dict(zip(raw_headers, headers))
        if UNITY_CPU_REQUIRED_COLUMNS.issubset(headers):
            source_format = "raw_frame_data_view"
            columns = {
                "frame": "frame",
                "thread": "thread",
                "sample": "sample",
                "depth": "depth",
                "start_ms": "startMs",
                "duration_ms": "durationMs",
            }
        elif UNITY_CPU_DETAILED_REQUIRED_COLUMNS.issubset(headers):
            source_format = "detailed_cpu_export"
            columns = {
                "frame": "FrameIndex",
                "thread": "ThreadName",
                "sample": "SampleName",
                "depth": "Depth",
                "start_ms": "StartTimeMs",
                "duration_ms": "DurationMs",
            }
        else:
            legacy_missing = sorted(UNITY_CPU_REQUIRED_COLUMNS.difference(headers))
            detailed_missing = sorted(UNITY_CPU_DETAILED_REQUIRED_COLUMNS.difference(headers))
            raise CsvLoadError(
                "Unity CPU CSV 表头不匹配支持的格式；"
                f"RawFrameDataView 缺少: {', '.join(legacy_missing) or '无'}；"
                f"详细 CPU 导出缺少: {', '.join(detailed_missing) or '无'}"
            )

        for index, raw in enumerate(reader, start=2):
            clean_raw = {header_map.get(key, key): value for key, value in raw.items() if key is not None}
            frame_column = columns["frame"]
            thread_column = columns["thread"]
            sample_column = columns["sample"]
            depth_column = columns["depth"]
            start_column = columns["start_ms"]
            duration_column = columns["duration_ms"]
            frame = parse_int(clean_raw.get(frame_column) or "", row_number=index, column=frame_column)
            depth = parse_int(clean_raw.get(depth_column) or "", row_number=index, column=depth_column)
            start_ms = parse_float(clean_raw.get(start_column) or "", row_number=index, column=start_column)
            duration_ms = parse_float(clean_raw.get(duration_column) or "", row_number=index, column=duration_column)
            if not math.isfinite(start_ms) or not math.isfinite(duration_ms) or duration_ms < 0:
                raise CsvLoadError(f"第 {index} 行 CPU 时间字段无效")
            thread = (clean_raw.get(thread_column) or "").strip()
            if not thread:
                thread_index = (clean_raw.get("ThreadIndex") or "").strip()
                thread = f"Thread {thread_index}" if thread_index else "Unknown Thread"
            gc_alloc_bytes = parse_optional_cpu_int(clean_raw, "GCAllocBytes", index) or 0
            if gc_alloc_bytes < 0:
                raise CsvLoadError(f"第 {index} 行 `GCAllocBytes` 不能为负数: {gc_alloc_bytes}")
            yield {
                "frame": frame,
                "thread": thread,
                "sample": (clean_raw.get(sample_column) or "").strip(),
                "depth": depth,
                "duration_ms": duration_ms,
                "self_ms": parse_optional_cpu_float(clean_raw, "SelfTimeMs", index),
                "cpu_frame_time_ms": parse_optional_cpu_float(clean_raw, "CPUFrameTimeMs", index),
                "gpu_frame_time_ms": parse_optional_cpu_float(clean_raw, "GPUFrameTimeMs", index),
                "fps": parse_optional_cpu_float(clean_raw, "FPS", index),
                "gc_alloc_bytes": gc_alloc_bytes,
                "source_format": source_format,
            }


def write_dict_csv(path: Path, fieldnames: list[str], rows: Iterable[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def cpu_issue(
    *,
    title: str,
    severity: str,
    score: float,
    evidence: str,
    interpretation: str,
    recommendation: str,
    confidence: str = "medium",
) -> dict[str, object]:
    return {
        "title": title,
        "severity": severity,
        "score": score,
        "evidence": evidence,
        "interpretation": interpretation,
        "recommendation": recommendation,
        "confidence": confidence,
    }


def evaluate_unity_cpu_rules(
    frame_rows: list[dict[str, object]],
    reportable_marker_rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    issues: list[dict[str, object]] = []
    main_times = [float(row["main_thread_ms"]) for row in frame_rows if float(row["main_thread_ms"]) > 0]
    if main_times:
        avg_main = sum(main_times) / len(main_times)
        p95_main = percentile_values(main_times, 0.95)
        max_main = max(main_times)
        over_16 = sum(1 for value in main_times if value > 16.67)
        over_33 = sum(1 for value in main_times if value > 33.33)
        if p95_main > 16.67:
            severity = "high" if p95_main > 33.33 else "medium"
            score = 90 if severity == "high" else 65
            issues.append(
                cpu_issue(
                    title="Main Thread 帧时超过 60 FPS 预算",
                    severity=severity,
                    score=score + min(15, max(0.0, p95_main - 16.67)),
                    evidence=(
                        f"avg={avg_main:.3f} ms, p95={p95_main:.3f} ms, max={max_main:.3f} ms, "
                        f"frames>16.67ms={over_16}/{len(main_times)}, frames>33.33ms={over_33}/{len(main_times)}"
                    ),
                    interpretation="Main Thread 聚合帧时超过 16.67 ms，按 60 FPS 预算存在 CPU 侧稳定性风险。",
                    recommendation="优先查看 worst frames 的 Top Main Self Markers，先处理高 self-time 且高频出现的脚本、UI、渲染提交或等待 marker。",
                    confidence="high",
                )
            )
        if over_33 > 0:
            issues.append(
                cpu_issue(
                    title="Main Thread 存在 30 FPS 预算风险帧",
                    severity="medium",
                    score=72 + min(10, over_33),
                    evidence=f"frames>33.33ms={over_33}/{len(main_times)}, max={max_main:.3f} ms",
                    interpretation="至少一帧 Main Thread 超过 33.33 ms，存在明显 spike / jank 风险。",
                    recommendation="按 `unity_cpu_worst_frames.csv` 定位这些帧的 top self/inclusive marker，并对对应系统做 A/B 关闭或降级验证。",
                    confidence="high",
                )
            )

    total_reportable_self = sum(float(row["total_self_ms"]) for row in reportable_marker_rows)
    category_self: Counter[str] = Counter()
    for row in reportable_marker_rows:
        category_self[str(row["category"])] += float(row["total_self_ms"])

    for category, threshold, severity, title, recommendation in [
        ("UI", 10.0, "medium", "UI self-time 占比较高", "检查 Canvas rebuild、Layout、Graphic、UGUI batch 更新和频繁 SetActive/RectTransform 变更。"),
        ("Rendering", 15.0, "medium", "Rendering self-time 占比较高", "检查渲染提交、Present、culling、draw/queue 相关 marker，并结合 GPU counter 判断 CPU 提交或 GPU 等待。"),
        ("Waiting", 10.0, "medium", "Waiting self-time 占比较高", "检查 Job 等待、RenderThread/GPU 同步、VSync、Present 和线程间同步点。"),
    ]:
        if total_reportable_self <= 0:
            continue
        pct = category_self[category] / total_reportable_self * 100.0
        if pct >= threshold:
            issues.append(
                cpu_issue(
                    title=title,
                    severity=severity,
                    score=60 + min(20, pct - threshold),
                    evidence=f"{category} total_self={category_self[category]:.3f} ms, reportable_self_pct={pct:.2f}%",
                    interpretation=f"{category} 类 marker 在可报告 self-time 中占比较高。",
                    recommendation=recommendation,
                    confidence="medium",
                )
            )

    gc_markers = [row for row in reportable_marker_rows if row["category"] == "GC"]
    if gc_markers:
        max_gc_self = max(float(row["max_self_ms"]) for row in gc_markers)
        total_gc_self = sum(float(row["total_self_ms"]) for row in gc_markers)
        if max_gc_self >= 2.0:
            issues.append(
                cpu_issue(
                    title="GC marker 存在单次高 self-time",
                    severity="medium",
                    score=68 + min(12, max_gc_self),
                    evidence=f"GC total_self={total_gc_self:.3f} ms, max_self={max_gc_self:.3f} ms",
                    interpretation="GC 相关 marker 出现毫秒级 self-time，可能造成单帧 spike。",
                    recommendation="检查对应 worst frame 的 GC.Alloc/Collect marker，减少热路径分配并验证 Incremental GC 设置。",
                    confidence="medium",
                )
            )

    profiler_p95 = percentile_values([float(row["profiler_overhead_ms"]) for row in frame_rows], 0.95) if frame_rows else math.nan
    profiler_max = max((float(row["profiler_overhead_ms"]) for row in frame_rows), default=0.0)
    if profiler_p95 >= 1.0 or profiler_max >= 2.0:
        issues.append(
            cpu_issue(
                title="Profiler overhead 偏高",
                severity="low",
                score=40 + min(10, profiler_max),
                evidence=f"profiler_overhead p95={profiler_p95:.3f} ms, max={profiler_max:.3f} ms",
                interpretation="Profiler 自身开销在部分帧中不可忽略，可能影响绝对帧时判断。",
                recommendation="复测时固定 Profiler 配置，必要时降低采样开销或用 Development Build/Release 对照。",
                confidence="medium",
            )
        )

    return sorted(issues, key=lambda item: float(item["score"]), reverse=True)


def build_unity_cpu_context(
    source: Path,
    source_format: str,
    frame_rows: list[dict[str, object]],
    marker_rows: list[dict[str, object]],
    reportable_marker_rows: list[dict[str, object]],
    category_rows: list[dict[str, object]],
    worst_frame_rows: list[dict[str, object]],
    issues: list[dict[str, object]],
    data_quality: dict[str, object],
) -> dict[str, object]:
    main_times = [float(row["main_thread_ms"]) for row in frame_rows if float(row["main_thread_ms"]) > 0]
    render_times = [
        float(row["render_thread_ms"])
        for row in frame_rows
        if row["render_thread_ms"] != "" and float(row["render_thread_ms"]) > 0
    ]
    cpu_frame_times = [float(row["cpu_frame_time_ms"]) for row in frame_rows if row["cpu_frame_time_ms"] != ""]
    gpu_frame_times = [float(row["gpu_frame_time_ms"]) for row in frame_rows if row["gpu_frame_time_ms"] != ""]
    fps_values = [float(row["fps"]) for row in frame_rows if row["fps"] != ""]
    sample_count = sum(int(row["sample_count"]) for row in frame_rows)
    return {
        "summary": {
            "source": str(source),
            "source_format": source_format,
            "frame_count": len(frame_rows),
            "valid_cpu_frame_count": len(main_times),
            "sample_count": sample_count,
            "main_thread_avg_ms": sum(main_times) / len(main_times) if main_times else None,
            "main_thread_p95_ms": percentile_values(main_times, 0.95) if main_times else None,
            "main_thread_max_ms": max(main_times) if main_times else None,
            "render_thread_avg_ms": sum(render_times) / len(render_times) if render_times else None,
            "render_thread_p95_ms": percentile_values(render_times, 0.95) if render_times else None,
            "render_thread_max_ms": max(render_times) if render_times else None,
            "frames_over_16_67_ms": sum(1 for value in main_times if value > 16.67),
            "frames_over_33_33_ms": sum(1 for value in main_times if value > 33.33),
            "cpu_frame_time_avg_ms": sum(cpu_frame_times) / len(cpu_frame_times) if cpu_frame_times else None,
            "cpu_frame_time_p95_ms": percentile_values(cpu_frame_times, 0.95) if cpu_frame_times else None,
            "cpu_frame_time_max_ms": max(cpu_frame_times) if cpu_frame_times else None,
            "gpu_frame_time_avg_ms": sum(gpu_frame_times) / len(gpu_frame_times) if gpu_frame_times else None,
            "gpu_frame_time_p95_ms": percentile_values(gpu_frame_times, 0.95) if gpu_frame_times else None,
            "gpu_frame_time_max_ms": max(gpu_frame_times) if gpu_frame_times else None,
            "fps_avg": sum(fps_values) / len(fps_values) if fps_values else None,
            "fps_p05": percentile_values(fps_values, 0.05) if fps_values else None,
            "fps_min": min(fps_values) if fps_values else None,
            "gc_alloc_count": sum(int(row["gc_alloc_count"]) for row in frame_rows),
            "gc_alloc_bytes": sum(int(row["gc_alloc_bytes"]) for row in frame_rows),
        },
        "rule_issues": issues,
        "marker_stats": reportable_marker_rows[:50],
        "category_stats": category_rows,
        "worst_frames": worst_frame_rows,
        "data_quality": data_quality,
        "data_boundaries": [
            "Unity CPU CSV is RawFrameDataView-style or detailed CPU sample data.",
            "CPU CSV can identify CPU frame, thread, marker, category, self-time, inclusive-time, and worst-frame directions.",
            "GPUFrameTimeMs values that are zero or absent are treated as unavailable, not as zero-cost GPU frames.",
            "Do not infer GPU counters, GPU-bound status, device model, materials, shaders, draw calls, GameObjects, temperature, power, or thermal throttling from CPU CSV alone.",
            "Marker names can suggest Unity systems, but aggregated CSV does not prove exact source code root cause without profiler timeline or project context.",
        ],
        "raw_marker_count": len(marker_rows),
    }


def analyze_unity_cpu(
    source: Path,
    out_dir: Path,
    top_frames: int,
    top_markers_per_frame: int,
) -> dict[str, object]:
    out_dir.mkdir(parents=True, exist_ok=True)

    marker_calls: Counter[str] = Counter()
    marker_frames: dict[str, set[int]] = defaultdict(set)
    marker_inclusive_total: Counter[str] = Counter()
    marker_self_total: Counter[str] = Counter()
    marker_inclusive_values: dict[str, list[float]] = defaultdict(list)
    marker_self_values: dict[str, list[float]] = defaultdict(list)
    marker_max_inclusive: Counter[str] = Counter()
    marker_max_self: Counter[str] = Counter()
    marker_gc_alloc_total: Counter[str] = Counter()
    marker_gc_alloc_max: Counter[str] = Counter()

    category_self_total: Counter[str] = Counter()
    category_inclusive_total: Counter[str] = Counter()
    category_calls: Counter[str] = Counter()

    frame_data: dict[int, dict[str, object]] = defaultdict(
        lambda: {
            "thread_count": set(),
            "sample_count": 0,
            "main_thread_ms": 0.0,
            "render_thread_ms": 0.0,
            "has_render_thread_root": False,
            "other_thread_root_max_ms": 0.0,
            "cpu_frame_time_ms": None,
            "gpu_frame_time_ms": None,
            "fps": None,
            "gc_alloc_count": 0,
            "gc_alloc_bytes": 0,
            "gc_alloc_time_ms": 0.0,
            "profiler_overhead_ms": 0.0,
            "main_marker_self": Counter(),
            "main_marker_inclusive": Counter(),
        }
    )

    stack: list[CpuOpenSample] = []
    current_group: tuple[int, str] | None = None
    row_count = 0
    source_formats: set[str] = set()
    thread_names: set[str] = set()
    supplied_self_sample_count = 0
    positive_cpu_frame_sample_count = 0
    positive_gpu_frame_sample_count = 0
    positive_fps_sample_count = 0

    def close_sample() -> None:
        node = stack.pop()
        derived_self_ms = max(0.0, node.inclusive_ms - node.child_ms)
        self_ms = (
            min(node.inclusive_ms, max(0.0, node.supplied_self_ms))
            if node.supplied_self_ms is not None
            else derived_self_ms
        )

        marker_calls[node.sample] += 1
        marker_frames[node.sample].add(node.frame)
        marker_inclusive_total[node.sample] += node.inclusive_ms
        marker_self_total[node.sample] += self_ms
        marker_inclusive_values[node.sample].append(node.inclusive_ms)
        marker_self_values[node.sample].append(self_ms)
        marker_max_inclusive[node.sample] = max(marker_max_inclusive[node.sample], node.inclusive_ms)
        marker_max_self[node.sample] = max(marker_max_self[node.sample], self_ms)
        marker_gc_alloc_total[node.sample] += node.gc_alloc_bytes
        marker_gc_alloc_max[node.sample] = max(marker_gc_alloc_max[node.sample], node.gc_alloc_bytes)

        category = classify_cpu_marker(node.sample)
        category_calls[category] += 1
        category_inclusive_total[category] += node.inclusive_ms
        category_self_total[category] += self_ms

        data = frame_data[node.frame]
        if node.gc_alloc_bytes > 0 or node.sample == "GC.Alloc":
            data["gc_alloc_count"] = int(data["gc_alloc_count"]) + 1
            data["gc_alloc_time_ms"] = float(data["gc_alloc_time_ms"]) + node.inclusive_ms
        data["gc_alloc_bytes"] = int(data["gc_alloc_bytes"]) + node.gc_alloc_bytes
        if category == "Profiler":
            data["profiler_overhead_ms"] = float(data["profiler_overhead_ms"]) + self_ms

        if is_main_thread(node.thread):
            data["main_marker_self"][node.sample] += self_ms
            data["main_marker_inclusive"][node.sample] += node.inclusive_ms

        if stack:
            stack[-1].child_ms += node.inclusive_ms
        elif node.depth == 0:
            if is_main_thread(node.thread):
                data["main_thread_ms"] = max(float(data["main_thread_ms"]), node.inclusive_ms)
            elif is_render_thread(node.thread):
                data["render_thread_ms"] = max(float(data["render_thread_ms"]), node.inclusive_ms)
                data["has_render_thread_root"] = True
            else:
                data["other_thread_root_max_ms"] = max(float(data["other_thread_root_max_ms"]), node.inclusive_ms)

    for row in read_unity_cpu_rows(source):
        row_count += 1
        frame = int(row["frame"])
        thread = str(row["thread"])
        sample = str(row["sample"])
        depth = int(row["depth"])
        duration_ms = float(row["duration_ms"])
        supplied_self_ms = float(row["self_ms"]) if row["self_ms"] is not None else None
        gc_alloc_bytes = int(row["gc_alloc_bytes"])
        source_formats.add(str(row["source_format"]))
        thread_names.add(thread)
        if supplied_self_ms is not None:
            supplied_self_sample_count += 1

        data = frame_data[frame]
        data["sample_count"] = int(data["sample_count"]) + 1
        thread_count = data["thread_count"]
        if isinstance(thread_count, set):
            thread_count.add(thread)

        cpu_frame_time_ms = row["cpu_frame_time_ms"]
        if cpu_frame_time_ms is not None and float(cpu_frame_time_ms) > 0:
            positive_cpu_frame_sample_count += 1
            data["cpu_frame_time_ms"] = max(float(data["cpu_frame_time_ms"] or 0.0), float(cpu_frame_time_ms))
        gpu_frame_time_ms = row["gpu_frame_time_ms"]
        if gpu_frame_time_ms is not None and float(gpu_frame_time_ms) > 0:
            positive_gpu_frame_sample_count += 1
            data["gpu_frame_time_ms"] = max(float(data["gpu_frame_time_ms"] or 0.0), float(gpu_frame_time_ms))
        fps = row["fps"]
        if fps is not None and float(fps) > 0:
            positive_fps_sample_count += 1
            data["fps"] = max(float(data["fps"] or 0.0), float(fps))

        group = (frame, thread)
        if current_group != group:
            while stack:
                close_sample()
            current_group = group

        while stack and depth <= stack[-1].depth:
            close_sample()

        stack.append(
            CpuOpenSample(
                frame=frame,
                thread=thread,
                sample=sample,
                depth=depth,
                inclusive_ms=duration_ms,
                supplied_self_ms=supplied_self_ms,
                gc_alloc_bytes=gc_alloc_bytes,
            )
        )

    while stack:
        close_sample()

    if row_count == 0:
        raise CsvLoadError("Unity CPU CSV 没有数据行")
    if len(source_formats) != 1:
        raise CsvLoadError("Unity CPU CSV 在同一文件中检测到多个表头格式")
    source_format = next(iter(source_formats))

    marker_rows: list[dict[str, object]] = []
    for sample, calls in marker_calls.items():
        inclusive_total = marker_inclusive_total[sample]
        self_total = marker_self_total[sample]
        self_values = marker_self_values[sample]
        inclusive_values = marker_inclusive_values[sample]
        marker_rows.append(
            {
                "sample": sample,
                "category": classify_cpu_marker(sample),
                "calls": calls,
                "frames": len(marker_frames[sample]),
                "total_self_ms": round(self_total, 6),
                "total_inclusive_ms": round(inclusive_total, 6),
                "avg_self_ms": round(self_total / calls, 6),
                "p50_self_ms": round(percentile_values(self_values, 0.50), 6),
                "p95_self_ms": round(percentile_values(self_values, 0.95), 6),
                "max_self_ms": round(marker_max_self[sample], 6),
                "avg_inclusive_ms": round(inclusive_total / calls, 6),
                "p95_inclusive_ms": round(percentile_values(inclusive_values, 0.95), 6),
                "max_inclusive_ms": round(marker_max_inclusive[sample], 6),
                "total_gc_alloc_bytes": marker_gc_alloc_total[sample],
                "max_gc_alloc_bytes": marker_gc_alloc_max[sample],
            }
        )
    marker_rows.sort(key=lambda item: (float(item["total_self_ms"]), float(item["max_self_ms"])), reverse=True)
    reportable_marker_rows = [row for row in marker_rows if is_reportable_cpu_marker(str(row["sample"]))]
    reportable_total_self_ms = sum(float(row["total_self_ms"]) for row in reportable_marker_rows)

    frame_rows: list[dict[str, object]] = []
    worst_frame_rows: list[dict[str, object]] = []
    for frame, data in sorted(frame_data.items()):
        main_ms = float(data["main_thread_ms"])
        explicit_cpu_frame_ms = data["cpu_frame_time_ms"]
        if main_ms <= 0 and explicit_cpu_frame_ms is not None:
            main_ms = float(explicit_cpu_frame_ms)
        render_ms = float(data["render_thread_ms"])
        render_thread_ms: float | str = round(render_ms, 6) if data["has_render_thread_root"] else ""
        other_thread_root_max_ms = float(data["other_thread_root_max_ms"])
        sample_count = int(data["sample_count"])
        thread_count = len(data["thread_count"]) if isinstance(data["thread_count"], set) else 0
        gc_alloc_count = int(data["gc_alloc_count"])
        gc_alloc_bytes = int(data["gc_alloc_bytes"])
        gc_alloc_time_ms = float(data["gc_alloc_time_ms"])
        profiler_overhead_ms = float(data["profiler_overhead_ms"])
        gpu_frame_time_ms = data["gpu_frame_time_ms"]
        fps = data["fps"]
        frame_rows.append(
            {
                "frame": frame,
                "main_thread_ms": round(main_ms, 6),
                "render_thread_ms": render_thread_ms,
                "other_thread_root_max_ms": round(other_thread_root_max_ms, 6),
                "cpu_frame_time_ms": round(float(explicit_cpu_frame_ms), 6) if explicit_cpu_frame_ms is not None else "",
                "gpu_frame_time_ms": round(float(gpu_frame_time_ms), 6) if gpu_frame_time_ms is not None else "",
                "fps": round(float(fps), 6) if fps is not None else "",
                "complete_cpu_frame": main_ms > 0,
                "thread_count": thread_count,
                "sample_count": sample_count,
                "gc_alloc_count": gc_alloc_count,
                "gc_alloc_bytes": gc_alloc_bytes,
                "gc_alloc_time_ms": round(gc_alloc_time_ms, 6),
                "profiler_overhead_ms": round(profiler_overhead_ms, 6),
            }
        )

        main_marker_self = data["main_marker_self"]
        main_marker_inclusive = data["main_marker_inclusive"]
        top_self = [
            item
            for item in main_marker_self.most_common()
            if is_reportable_cpu_marker(item[0])
        ][:top_markers_per_frame]
        top_inclusive = [
            item
            for item in main_marker_inclusive.most_common()
            if is_reportable_cpu_marker(item[0])
        ][:top_markers_per_frame]
        if main_ms > 0:
            worst_frame_rows.append(
                {
                    "frame": frame,
                    "main_thread_ms": round(main_ms, 6),
                    "render_thread_ms": render_thread_ms,
                    "other_thread_root_max_ms": round(other_thread_root_max_ms, 6),
                    "cpu_frame_time_ms": round(float(explicit_cpu_frame_ms), 6) if explicit_cpu_frame_ms is not None else "",
                    "gpu_frame_time_ms": round(float(gpu_frame_time_ms), 6) if gpu_frame_time_ms is not None else "",
                    "fps": round(float(fps), 6) if fps is not None else "",
                    "sample_count": sample_count,
                    "gc_alloc_count": gc_alloc_count,
                    "gc_alloc_bytes": gc_alloc_bytes,
                    "top_main_self_markers": " | ".join(f"{name}:{value:.3f}" for name, value in top_self),
                    "top_main_inclusive_markers": " | ".join(f"{name}:{value:.3f}" for name, value in top_inclusive),
                }
            )
    worst_frame_rows.sort(key=lambda item: float(item["main_thread_ms"]), reverse=True)
    worst_frame_rows = worst_frame_rows[:top_frames]

    category_rows: list[dict[str, object]] = []
    for category, total_self in category_self_total.most_common():
        calls = category_calls[category]
        category_rows.append(
            {
                "category": category,
                "calls": calls,
                "total_self_ms": round(total_self, 6),
                "total_inclusive_ms": round(category_inclusive_total[category], 6),
                "avg_self_ms": round(total_self / calls, 6),
            }
        )

    marker_path = out_dir / "unity_cpu_marker_summary.csv"
    frame_path = out_dir / "unity_cpu_frame_summary.csv"
    worst_path = out_dir / "unity_cpu_worst_frames.csv"
    category_path = out_dir / "unity_cpu_category_summary.csv"
    report_path = out_dir / "unity_cpu_summary.md"

    write_dict_csv(
        marker_path,
        [
            "sample",
            "category",
            "calls",
            "frames",
            "total_self_ms",
            "total_inclusive_ms",
            "avg_self_ms",
            "p50_self_ms",
            "p95_self_ms",
            "max_self_ms",
            "avg_inclusive_ms",
            "p95_inclusive_ms",
            "max_inclusive_ms",
            "total_gc_alloc_bytes",
            "max_gc_alloc_bytes",
        ],
        marker_rows,
    )
    write_dict_csv(
        frame_path,
        [
            "frame",
            "main_thread_ms",
            "render_thread_ms",
            "other_thread_root_max_ms",
            "cpu_frame_time_ms",
            "gpu_frame_time_ms",
            "fps",
            "complete_cpu_frame",
            "thread_count",
            "sample_count",
            "gc_alloc_count",
            "gc_alloc_bytes",
            "gc_alloc_time_ms",
            "profiler_overhead_ms",
        ],
        frame_rows,
    )
    write_dict_csv(
        worst_path,
        [
            "frame",
            "main_thread_ms",
            "render_thread_ms",
            "other_thread_root_max_ms",
            "cpu_frame_time_ms",
            "gpu_frame_time_ms",
            "fps",
            "sample_count",
            "gc_alloc_count",
            "gc_alloc_bytes",
            "top_main_self_markers",
            "top_main_inclusive_markers",
        ],
        worst_frame_rows,
    )
    write_dict_csv(category_path, ["category", "calls", "total_self_ms", "total_inclusive_ms", "avg_self_ms"], category_rows)

    main_times = [float(row["main_thread_ms"]) for row in frame_rows if float(row["main_thread_ms"]) > 0]
    gpu_frame_times = [float(row["gpu_frame_time_ms"]) for row in frame_rows if row["gpu_frame_time_ms"] != ""]
    fps_values = [float(row["fps"]) for row in frame_rows if row["fps"] != ""]
    total_gc_alloc_bytes = sum(int(row["gc_alloc_bytes"]) for row in frame_rows)
    total_gc_alloc_count = sum(int(row["gc_alloc_count"]) for row in frame_rows)
    over_16 = sum(1 for value in main_times if value > 16.67)
    over_33 = sum(1 for value in main_times if value > 33.33)
    report_lines = [
        "# Unity CPU Profiler Summary",
        "",
        f"- Source: `{source}`",
        f"- Source format: `{source_format}`",
        f"- Frames: {len(frame_rows)}",
        f"- Valid CPU frames: {len(main_times)}",
        f"- Samples: {sum(int(row['sample_count']) for row in frame_rows)}",
        f"- Main Thread avg: {sum(main_times) / len(main_times):.3f} ms" if main_times else "- Main Thread avg: n/a",
        f"- Main Thread p95: {percentile_values(main_times, 0.95):.3f} ms" if main_times else "- Main Thread p95: n/a",
        f"- Main Thread max: {max(main_times):.3f} ms" if main_times else "- Main Thread max: n/a",
        f"- GPU Frame Time avg: {sum(gpu_frame_times) / len(gpu_frame_times):.3f} ms" if gpu_frame_times else "- GPU Frame Time: unavailable",
        f"- FPS avg/p05/min: {sum(fps_values) / len(fps_values):.3f} / {percentile_values(fps_values, 0.05):.3f} / {min(fps_values):.3f}" if fps_values else "- FPS: unavailable",
        f"- GC allocations: {total_gc_alloc_count} events, {total_gc_alloc_bytes} bytes",
        f"- Frames > 16.67 ms: {over_16}",
        f"- Frames > 33.33 ms: {over_33}",
        "",
        "## Top Self-Time Markers",
        "",
        "| Marker | Category | Total Self % | Calls | Max Self ms | GC Alloc Bytes |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in reportable_marker_rows[:20]:
        total_self_pct = (
            float(row["total_self_ms"]) / reportable_total_self_ms * 100.0
            if reportable_total_self_ms > 0
            else 0.0
        )
        report_lines.append(
            f"| {row['sample']} | {row['category']} | {total_self_pct:.2f}% | {row['calls']} | {row['max_self_ms']} | {row['total_gc_alloc_bytes']} |"
        )

    report_lines.extend(
        [
            "",
            "## Worst Frames",
            "",
            "| Frame | Main ms | Render ms | FPS | GC Alloc Count | GC Alloc Bytes | Top Main Self Markers |",
            "|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for row in worst_frame_rows[:20]:
        report_lines.append(
            f"| {row['frame']} | {row['main_thread_ms']} | {row['render_thread_ms'] or 'n/a'} | {row['fps'] or 'n/a'} | {row['gc_alloc_count']} | {row['gc_alloc_bytes']} | {row['top_main_self_markers']} |"
        )

    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    issues = evaluate_unity_cpu_rules(frame_rows, reportable_marker_rows)
    data_quality = {
        "thread_names": sorted(thread_names),
        "incomplete_cpu_frame_count": sum(1 for row in frame_rows if not row["complete_cpu_frame"]),
        "explicit_cpu_frame_count": sum(1 for row in frame_rows if row["cpu_frame_time_ms"] != ""),
        "explicit_gpu_frame_count": len(gpu_frame_times),
        "fps_frame_count": len(fps_values),
        "supplied_self_time_sample_count": supplied_self_sample_count,
        "derived_self_time_sample_count": row_count - supplied_self_sample_count,
        "positive_cpu_frame_field_sample_count": positive_cpu_frame_sample_count,
        "positive_gpu_frame_field_sample_count": positive_gpu_frame_sample_count,
        "positive_fps_field_sample_count": positive_fps_sample_count,
        "gpu_frame_time_available": bool(gpu_frame_times),
    }
    context = build_unity_cpu_context(
        source,
        source_format,
        frame_rows,
        marker_rows,
        reportable_marker_rows,
        category_rows,
        worst_frame_rows,
        issues,
        data_quality,
    )

    return {
        "marker_path": marker_path,
        "frame_path": frame_path,
        "worst_path": worst_path,
        "category_path": category_path,
        "report_path": report_path,
        "frame_count": len(frame_rows),
        "sample_count": sum(int(row["sample_count"]) for row in frame_rows),
        "context": context,
    }


def parse_snapshot_number(text: str) -> tuple[float | None, bool]:
    value = text.strip().replace("−", "-")
    if not value:
        return None, False
    if value.casefold() in {"inf", "+inf", "-inf", "infinity", "+infinity", "-infinity", "∞", "+∞", "-∞"}:
        return None, True
    try:
        number = float(value)
    except ValueError:
        return None, True
    if not math.isfinite(number):
        return None, True
    return number, False


def read_snapshot_rows(path: Path) -> tuple[list[SnapshotRow], tuple[str, ...]]:
    if not path.exists():
        raise CsvLoadError(f"Snapshot CSV 文件不存在: {path}")
    if not path.is_file():
        raise CsvLoadError(f"Snapshot CSV 路径不是文件: {path}")

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise CsvLoadError("Snapshot CSV 为空或缺少表头")

        raw_headers = list(reader.fieldnames)
        clean_headers = [re.sub(r"\s+", " ", (name or "").strip()) for name in raw_headers]
        if any(not name for name in clean_headers):
            raise CsvLoadError("Snapshot CSV 包含空表头")
        duplicates = sorted(name for name, count in Counter(clean_headers).items() if count > 1)
        if duplicates:
            raise CsvLoadError(f"Snapshot CSV 去除空白后存在重复表头: {', '.join(duplicates)}")

        missing = SNAPSHOT_REQUIRED_COLUMNS.difference(clean_headers)
        if missing:
            raise CsvLoadError(f"Snapshot CSV 缺少必要列: {', '.join(sorted(missing))}")

        header_map = dict(zip(raw_headers, clean_headers))
        raw_metric_headers = [name for name in clean_headers if name not in SNAPSHOT_REQUIRED_COLUMNS]
        metric_columns = tuple(normalize_metric_name(name) for name in raw_metric_headers)
        duplicate_metrics = sorted(name for name, count in Counter(metric_columns).items() if count > 1)
        if duplicate_metrics:
            raise CsvLoadError(f"Snapshot CSV 指标名归一化后重复: {', '.join(duplicate_metrics)}")
        if not metric_columns:
            raise CsvLoadError("Snapshot CSV 未包含任何 Counter 列")

        metric_name_map = dict(zip(raw_metric_headers, metric_columns))
        rows: list[SnapshotRow] = []
        for row_number, raw in enumerate(reader, start=2):
            clean = {header_map[key]: (value or "").strip() for key, value in raw.items() if key in header_map}
            metrics: dict[str, float] = {}
            non_finite_metrics: list[str] = []
            has_metric_fields = False
            for raw_name, metric_name in metric_name_map.items():
                text = clean.get(raw_name, "")
                has_metric_fields = has_metric_fields or bool(text)
                number, invalid = parse_snapshot_number(text)
                if number is not None:
                    metrics[metric_name] = number
                elif invalid:
                    non_finite_metrics.append(metric_name)

            rows.append(
                SnapshotRow(
                    row_number=row_number,
                    event_id=clean.get("ID", ""),
                    name=clean.get("Name", ""),
                    parameters=clean.get("Parameters", ""),
                    context=clean.get("Context", ""),
                    thread=clean.get("Thread", ""),
                    metrics=metrics,
                    non_finite_metrics=tuple(non_finite_metrics),
                    has_metric_fields=has_metric_fields,
                )
            )

    if not rows:
        raise CsvLoadError("Snapshot CSV 没有数据行")
    if not any(row.event_id for row in rows):
        raise CsvLoadError("Snapshot CSV 没有带 ID 的已分析 GPU 命令")
    return rows, metric_columns


def is_snapshot_api_name(name: str) -> bool:
    stripped = name.strip()
    return bool(re.match(r"^(?:gl|egl|vk|cl)[A-Z0-9_]", stripped)) or stripped.casefold().startswith("adreno_")


def is_snapshot_marker(row: SnapshotRow) -> bool:
    return not row.event_id and bool(row.name) and not is_snapshot_api_name(row.name)


def snapshot_pass_context(rows: Sequence[SnapshotRow], event_index: int) -> dict[str, object]:
    """Associate a profiled command with the nearest readable Marker block above it."""
    event = rows[event_index]
    cursor = event_index - 1
    nearest_block: list[SnapshotRow] = []

    while cursor >= 0:
        if is_snapshot_marker(rows[cursor]):
            nearest_block.append(rows[cursor])
            cursor -= 1
            while cursor >= 0 and is_snapshot_marker(rows[cursor]):
                nearest_block.append(rows[cursor])
                cursor -= 1
            break
        cursor -= 1

    if not nearest_block:
        return {"pass_name": "", "pass_match_method": "unavailable", "pass_marker_rows": ""}

    nearest_block.reverse()
    event_clocks = max(0.0, event.metrics.get("Clocks", 0.0))
    enclosing = [
        marker
        for marker in nearest_block
        if marker.metrics.get("Clocks", 0.0) > 0
        and marker.metrics.get("Clocks", 0.0) >= event_clocks
    ]
    if enclosing:
        selected = enclosing
        method = "clock_enclosing_marker_block"
    else:
        positive = [marker for marker in nearest_block if marker.metrics.get("Clocks", 0.0) > 0]
        selected = [positive[-1] if positive else nearest_block[-1]]
        method = "nearest_marker_fallback"

    unique_markers: list[SnapshotRow] = []
    seen_names: set[str] = set()
    for marker in selected:
        if marker.name not in seen_names:
            unique_markers.append(marker)
            seen_names.add(marker.name)
    return {
        "pass_name": " > ".join(marker.name for marker in unique_markers),
        "pass_match_method": method,
        "pass_marker_rows": " | ".join(str(marker.row_number) for marker in unique_markers),
    }


def snapshot_command_category(name: str) -> str:
    lowered = name.casefold()
    if "draw" in lowered and lowered.startswith(("gl", "vk")):
        return "draw"
    if "dispatch" in lowered:
        return "dispatch"
    if "clear" in lowered:
        return "clear"
    if "swap" in lowered or "present" in lowered:
        return "present"
    if any(token in lowered for token in ("wait", "sync", "fence", "finish")):
        return "sync"
    if any(token in lowered for token in ("blit", "copy", "resolve")):
        return "copy"
    if any(token in lowered for token in ("bind", "useprogram", "uniform", "attrib", "enable", "disable", "mask", "func", "parameter")):
        return "state"
    return "other"


def snapshot_metric_weight(row: SnapshotRow, metric_name: str) -> float:
    if metric_name.startswith("%"):
        return max(0.0, row.metrics.get("Clocks", 0.0))
    if metric_name.endswith("/ Fragment"):
        return max(0.0, row.metrics.get("Fragments Shaded", 0.0))
    if metric_name.endswith("/ Vertex"):
        return max(0.0, row.metrics.get("Vertices Shaded", 0.0))
    return 1.0


def snapshot_metric_stats(rows: Sequence[SnapshotRow], metric_columns: Sequence[str]) -> list[dict[str, object]]:
    stats: list[dict[str, object]] = []
    for name in metric_columns:
        values = [row.metrics[name] for row in rows if name in row.metrics]
        non_finite_count = sum(name in row.non_finite_metrics for row in rows)
        if not values:
            stats.append(
                {
                    "name": name,
                    "count": 0,
                    "missing_count": len(rows) - non_finite_count,
                    "non_finite_count": non_finite_count,
                    "zero_count": 0,
                    "min": None,
                    "avg": None,
                    "p50": None,
                    "p95": None,
                    "max": None,
                    "sum": None,
                    "aggregate": None,
                    "aggregate_method": "unavailable",
                }
            )
            continue

        ordered = sorted(values)
        arithmetic_mean = sum(values) / len(values)
        if name in SNAPSHOT_ADDITIVE_METRICS:
            aggregate = sum(values)
            aggregate_method = "sum"
        else:
            weighted = [
                (row.metrics[name], snapshot_metric_weight(row, name))
                for row in rows
                if name in row.metrics and snapshot_metric_weight(row, name) > 0
            ]
            if weighted:
                total_weight = sum(weight for _, weight in weighted)
                aggregate = sum(value * weight for value, weight in weighted) / total_weight
                if name.startswith("%"):
                    aggregate_method = "clock_weighted_mean"
                elif name.endswith("/ Fragment"):
                    aggregate_method = "fragment_weighted_mean"
                elif name.endswith("/ Vertex"):
                    aggregate_method = "vertex_weighted_mean"
                else:
                    aggregate_method = "weighted_mean"
            else:
                aggregate = arithmetic_mean
                aggregate_method = "arithmetic_mean"

        stats.append(
            {
                "name": name,
                "count": len(values),
                "missing_count": len(rows) - len(values) - non_finite_count,
                "non_finite_count": non_finite_count,
                "zero_count": sum(value == 0 for value in values),
                "min": round(ordered[0], 6),
                "avg": round(arithmetic_mean, 6),
                "p50": round(percentile_values(ordered, 0.50), 6),
                "p95": round(percentile_values(ordered, 0.95), 6),
                "max": round(ordered[-1], 6),
                "sum": round(sum(values), 6),
                "aggregate": round(aggregate, 6),
                "aggregate_method": aggregate_method,
            }
        )
    return stats


def snapshot_group_summary(
    groups: dict[str, list[SnapshotRow]],
    total_event_clocks: float,
    *,
    name_key: str,
) -> list[dict[str, object]]:
    summary: list[dict[str, object]] = []
    percent_metrics = (
        "% Shader ALU Capacity Utilized",
        "% Shaders Busy",
        "% Shaders Stalled",
        "% Stalled on System Memory",
        "% Texture Pipes Busy",
    )
    for group_name, group_rows in groups.items():
        clocks = [row.metrics.get("Clocks", 0.0) for row in group_rows]
        total_clocks = sum(clocks)
        item: dict[str, object] = {
            name_key: group_name,
            "category": snapshot_command_category(group_name) if name_key == "command" else "marker",
            "occurrences": len(group_rows),
            "total_clocks": round(total_clocks, 6),
            "avg_clocks": round(total_clocks / len(group_rows), 6),
            "p95_clocks": round(percentile_values(clocks, 0.95), 6),
            "max_clocks": round(max(clocks), 6),
            "clock_share_pct": round(total_clocks / total_event_clocks * 100.0, 6) if total_event_clocks else 0.0,
            "fragments_shaded": round(sum(row.metrics.get("Fragments Shaded", 0.0) for row in group_rows), 6),
            "vertices_shaded": round(sum(row.metrics.get("Vertices Shaded", 0.0) for row in group_rows), 6),
            "texture_memory_read_bytes": round(
                sum(row.metrics.get("Texture Memory Read BW (Bytes)", 0.0) for row in group_rows), 6
            ),
            "vertex_memory_read_bytes": round(
                sum(row.metrics.get("Vertex Memory Read (Bytes)", 0.0) for row in group_rows), 6
            ),
        }
        group_stats = {entry["name"]: entry for entry in snapshot_metric_stats(group_rows, percent_metrics)}
        for metric_name in percent_metrics:
            key = re.sub(r"[^a-z0-9]+", "_", metric_name.casefold()).strip("_") + "_pct"
            item[key] = group_stats[metric_name]["aggregate"]
        summary.append(item)
    summary.sort(key=lambda item: (float(item["total_clocks"]), int(item["occurrences"])), reverse=True)
    return summary


def evaluate_snapshot_rules(metric_stats: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    by_name = {str(item["name"]): item for item in metric_stats}
    issues: list[dict[str, object]] = []

    def add_rule(
        metric_name: str,
        *,
        threshold: float,
        relation: str,
        title: str,
        severity: str,
        reference: str,
        recommendation: str,
    ) -> None:
        metric = by_name.get(metric_name)
        if not metric or metric.get("aggregate") is None:
            return
        value = float(metric["aggregate"])
        failed = value < threshold if relation == "min" else value > threshold
        if not failed:
            return
        gap = threshold - value if relation == "min" else value - threshold
        issues.append(
            {
                "title": title,
                "severity": severity,
                "score": SEVERITY_SCORE[severity] + min(20.0, abs(gap)),
                "metric": metric_name,
                "statistic": metric["aggregate_method"],
                "value": round(value, 6),
                "reference": reference,
                "evidence": f"{metric['aggregate_method']}={value:.2f}, p95={float(metric['p95']):.2f}, n={metric['count']}",
                "interpretation": "Snapshot 仅覆盖一次捕获，命令级百分比按 Clocks 加权；该信号用于定位方向，不能单独证明帧级瓶颈。",
                "recommendation": recommendation,
                "confidence": "low",
            }
        )

    add_rule(
        "Average Polygon Area", threshold=4, relation="min", title="平均多边形面积低于建议值", severity="low",
        reference=">= 4", recommendation="检查小三角形、LOD 和不可见几何提交，并用 Realtime/Trace 验证帧级影响。",
    )
    add_rule(
        "% Prims Clipped", threshold=2, relation="max", title="裁剪图元比例偏高", severity="low",
        reference="< 2%", recommendation="检查视锥外几何、裁剪面和相机覆盖范围。",
    )
    add_rule(
        "% Prims Trivially Rejected", threshold=2, relation="max", title="快速拒绝图元比例偏高", severity="low",
        reference="< 2%", recommendation="检查遮挡、过度提交和前后向剔除策略。",
    )
    add_rule(
        "% Shader ALU Capacity Utilized", threshold=50, relation="min", title="Shader ALU 容量利用率偏低", severity="low",
        reference="50%-100%", recommendation="优先查看高 Clocks 命令的 stall、纹理和分支特征，不要仅以提高 ALU 利用率为目标。",
    )
    add_rule(
        "% Shaders Busy", threshold=50, relation="min", title="Shader Busy 比例偏低", severity="low",
        reference="50%-100%", recommendation="结合高 Clocks 命令和 Realtime GPU 利用率确认是否存在供给、同步或固定功能阶段限制。",
    )
    add_rule(
        "% Shaders Stalled", threshold=10, relation="max", title="Shader Stall 偏高", severity="medium",
        reference="< 10%", recommendation="从高 Clocks 且 Stall 高的命令开始检查纹理、系统内存访问和依赖链。",
    )
    add_rule(
        "% Stalled on System Memory", threshold=2, relation="max", title="系统内存等待偏高", severity="medium",
        reference="通常 < 2%", recommendation="检查高占比命令的 buffer/texture 访问，并补采带宽、缓存和 Realtime 持续性证据。",
    )
    add_rule(
        "% Texture Fetch Stall", threshold=2, relation="max", title="纹理取样等待偏高", severity="medium",
        reference="通常 < 2%", recommendation="检查高 Clocks draw 的纹理数量、格式、过滤和缓存局部性。",
    )
    add_rule(
        "% Time ALUs Working", threshold=50, relation="min", title="ALU 工作时间占比偏低", severity="low",
        reference="50%-100%", recommendation="结合 Shader Busy、Stall 和固定功能 Counter 判断供给不足方向。",
    )
    add_rule(
        "% Time EFUs Working", threshold=20, relation="min", title="EFU 工作时间占比低于建议值", severity="low",
        reference=">= 20%", recommendation="作为工作负载特征保留；除非与帧时间和高成本命令相关，否则不单独优化。",
    )
    add_rule(
        "% Wave Context Occupancy", threshold=50, relation="min", title="Wave Context Occupancy 偏低", severity="low",
        reference="平均 >= 50%", recommendation="检查寄存器压力、线程组规模和长延迟依赖，并用 shader 变体验证。",
    )
    add_rule(
        "% CP Overhead", threshold=20, relation="max", title="CP Overhead 过高", severity="high",
        reference="不应超过 20%", recommendation="检查命令数量、状态切换、小批次提交和同步点。",
    )
    return sorted(issues, key=lambda item: float(item["score"]), reverse=True)


def render_snapshot_report(context: dict[str, object], top_n: int) -> str:
    summary = context["summary"]
    issues = context["rule_issues"]
    top_commands = context["top_commands"]
    marker_stats = context["marker_stats"]
    api_stats = context["api_call_stats"]
    metric_stats = context["metric_stats"]
    data_quality = context["data_quality"]
    lines = [
        "# Snapdragon Profiler Snapshot 摘要",
        "",
        f"- 数据源: `{summary['source']}`",
        f"- 数据行: {summary['row_count']}",
        f"- 已分析 GPU 命令: {summary['profiled_event_count']}",
        f"- Marker 行: {summary['marker_row_count']}",
        f"- API 调用行: {summary['api_call_count']}",
        f"- Draw / Dispatch / Clear / Present: {summary['draw_call_count']} / {summary['dispatch_call_count']} / {summary['clear_call_count']} / {summary['present_call_count']}",
        f"- 命令 Clocks 合计: {summary['total_event_clocks']}",
        "",
        "## 规则信号",
        "",
    ]
    if issues:
        lines.extend(["| 级别 | 指标 | 证据 | 参考 |", "|---|---|---|---|"])
        for item in issues[:top_n]:
            lines.append(
                f"| {item['severity']} | {markdown_cell(item['metric'])} | {markdown_cell(item['evidence'])} | {markdown_cell(item['reference'])} |"
            )
    else:
        lines.append("- 未命中可应用于 Snapshot 聚合值的内置阈值规则。")

    lines.extend(
        [
            "",
            "## 高 Clocks GPU 命令",
            "",
            "| ID | Pass / Marker（向上关联） | API 命令 | 类型 | Clocks | 占命令 Clocks | Parameters |",
            "|---:|---|---|---|---:|---:|---|",
        ]
    )
    for item in top_commands[:20]:
        lines.append(
            f"| {markdown_cell(item['id'])} | {markdown_cell(item['pass_name'] or '未找到')} | "
            f"{markdown_cell(item['name'])} | {item['category']} | "
            f"{item['event_clocks']} | {float(item['clock_share_pct']):.2f}% | {markdown_cell(item['parameters'])} |"
        )

    lines.extend(
        [
            "",
            "## 高 Clocks Marker",
            "",
            "Marker 可能嵌套，以下 Clocks 与占比用于排序，Marker 之间不可相加。",
            "",
            "| Marker | 次数 | Clocks | 占命令 Clocks | Max Clocks |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for item in marker_stats[:20]:
        lines.append(
            f"| {markdown_cell(item['marker'])} | {item['occurrences']} | {item['total_clocks']} | "
            f"{float(item['clock_share_pct']):.2f}% | {item['max_clocks']} |"
        )

    lines.extend(
        [
            "",
            "## API 调用概览",
            "",
            "| API | 类型 | 调用数 | 已分析事件数 |",
            "|---|---|---:|---:|",
        ]
    )
    for item in api_stats[:20]:
        lines.append(f"| {markdown_cell(item['name'])} | {item['category']} | {item['calls']} | {item['profiled_events']} |")

    lines.extend(
        [
            "",
            "## Counter 聚合",
            "",
            "| Counter | 聚合值 | 方法 | p95（逐命令） | 有效数 | 非有限数 |",
            "|---|---:|---|---:|---:|---:|",
        ]
    )
    for item in metric_stats:
        aggregate = "n/a" if item["aggregate"] is None else item["aggregate"]
        p95 = "n/a" if item["p95"] is None else item["p95"]
        lines.append(
            f"| {markdown_cell(item['name'])} | {aggregate} | {item['aggregate_method']} | {p95} | "
            f"{item['count']} | {item['non_finite_count']} |"
        )

    lines.extend(
        [
            "",
            "## 数据质量与边界",
            "",
            f"- 重复事件 ID: {data_quality['duplicate_event_id_count']}；无名称行: {data_quality['blank_name_count']}。",
            f"- 非有限或不可解析 Counter 值: {data_quality['non_finite_value_count']}；这些值未参与聚合。",
        ]
    )
    for boundary in context["data_boundaries"]:
        lines.append(f"- {boundary}")
    return "\n".join(lines).rstrip() + "\n"


def analyze_snapshot(
    source: Path,
    out_dir: Path,
    top_commands: int,
    top_markers: int,
    top_issues: int,
) -> dict[str, object]:
    rows, metric_columns = read_snapshot_rows(source)
    out_dir.mkdir(parents=True, exist_ok=True)

    event_rows = [row for row in rows if row.event_id]
    marker_rows = [row for row in rows if is_snapshot_marker(row)]
    api_rows = [row for row in rows if row.event_id or is_snapshot_api_name(row.name)]
    total_event_clocks = sum(row.metrics.get("Clocks", 0.0) for row in event_rows)

    metrics = snapshot_metric_stats(event_rows, metric_columns)
    issues = evaluate_snapshot_rules(metrics)

    command_groups: dict[str, list[SnapshotRow]] = defaultdict(list)
    marker_groups: dict[str, list[SnapshotRow]] = defaultdict(list)
    api_groups: dict[str, list[SnapshotRow]] = defaultdict(list)
    for row in event_rows:
        command_groups[row.name or "(blank)"].append(row)
    for row in marker_rows:
        marker_groups[row.name].append(row)
    for row in api_rows:
        api_groups[row.name or "(blank)"].append(row)

    command_stats = snapshot_group_summary(command_groups, total_event_clocks, name_key="command")
    marker_stats = snapshot_group_summary(marker_groups, total_event_clocks, name_key="marker")

    api_stats: list[dict[str, object]] = []
    for name, group_rows in api_groups.items():
        api_stats.append(
            {
                "name": name,
                "category": snapshot_command_category(name),
                "calls": len(group_rows),
                "profiled_events": sum(bool(row.event_id) for row in group_rows),
            }
        )
    api_stats.sort(key=lambda item: (int(item["calls"]), int(item["profiled_events"])), reverse=True)

    sorted_events = sorted(
        event_rows,
        key=lambda row: (row.metrics.get("Clocks", 0.0), row.row_number),
        reverse=True,
    )
    row_index_by_number = {row.row_number: index for index, row in enumerate(rows)}
    top_command_rows: list[dict[str, object]] = []
    for row in sorted_events[:top_commands]:
        clocks = row.metrics.get("Clocks", 0.0)
        pass_context = snapshot_pass_context(rows, row_index_by_number[row.row_number])
        item: dict[str, object] = {
            "id": row.event_id,
            **pass_context,
            "name": row.name,
            "category": snapshot_command_category(row.name),
            "parameters": row.parameters,
            "context": row.context,
            "thread": row.thread,
            "event_clocks": round(clocks, 6),
            "clock_share_pct": round(clocks / total_event_clocks * 100.0, 6) if total_event_clocks else 0.0,
            "non_finite_metrics": " | ".join(row.non_finite_metrics),
        }
        for metric_name in metric_columns:
            item[metric_name] = row.metrics.get(metric_name, "")
        top_command_rows.append(item)

    event_id_counts = Counter(row.event_id for row in event_rows)
    data_quality = {
        "blank_name_count": sum(not row.name for row in rows),
        "duplicate_event_id_count": sum(count - 1 for count in event_id_counts.values() if count > 1),
        "non_finite_value_count": sum(len(row.non_finite_metrics) for row in event_rows),
        "non_finite_by_metric": dict(
            sorted(
                Counter(metric for row in event_rows for metric in row.non_finite_metrics).items(),
                key=lambda item: (-item[1], item[0]),
            )
        ),
        "event_rows_without_clocks": sum("Clocks" not in row.metrics for row in event_rows),
    }
    categories = Counter(snapshot_command_category(row.name) for row in event_rows)
    summary = {
        "capture_mode": "snapshot",
        "source": str(source),
        "row_count": len(rows),
        "metric_column_count": len(metric_columns),
        "profiled_event_count": len(event_rows),
        "marker_row_count": len(marker_rows),
        "api_call_count": len(api_rows),
        "draw_call_count": categories["draw"],
        "dispatch_call_count": categories["dispatch"],
        "clear_call_count": categories["clear"],
        "present_call_count": categories["present"],
        "sync_call_count": categories["sync"],
        "total_event_clocks": round(total_event_clocks, 6),
        "contexts": sorted({row.context for row in rows if row.context}),
        "threads": sorted({row.thread for row in rows if row.thread}),
    }
    context = {
        "summary": summary,
        "rule_issues": issues[:top_issues],
        "metric_stats": metrics,
        "top_commands": top_command_rows,
        "command_stats": command_stats,
        "marker_stats": marker_stats[:top_markers],
        "api_call_stats": api_stats,
        "data_quality": data_quality,
        "data_boundaries": [
            "Snapshot 是一次捕获的逐命令/Marker Counter，不是 Realtime 时间序列，也不提供帧时间分布。",
            "命令百分比 Counter 以 Clocks 加权；Clocks 仅作捕获内相对成本代理，不能直接换算为毫秒。",
            "Marker 可能嵌套，其 Clocks 和资源计数会重叠；不得跨 Marker 求和或把占比相加为帧占比。",
            "高成本命令的 Pass 名称由命令行向上扫描最近的连续 Marker 块得到；优先保留 Clocks 可包围该命令的 Marker，否则仅标记最近 Marker，属于定位线索而非精确层级证明。",
            "API 调用次数不含 CPU 执行时长；高调用数只能作为批次、状态切换或提交开销的排查线索。",
            "Snapshot 可定位高成本命令及关联 Parameters/Marker，但不能单独证明材质、Shader、GameObject 或帧级根因。",
        ],
    }

    metric_path = out_dir / "snapshot_metric_summary.csv"
    command_path = out_dir / "snapshot_command_summary.csv"
    top_command_path = out_dir / "snapshot_top_commands.csv"
    marker_path = out_dir / "snapshot_marker_summary.csv"
    api_path = out_dir / "snapshot_api_summary.csv"
    report_path = out_dir / "snapshot_summary.md"

    metric_fields = [
        "name", "count", "missing_count", "non_finite_count", "zero_count", "min", "avg", "p50", "p95",
        "max", "sum", "aggregate", "aggregate_method",
    ]
    group_fields = [
        "command", "category", "occurrences", "total_clocks", "avg_clocks", "p95_clocks", "max_clocks",
        "clock_share_pct", "fragments_shaded", "vertices_shaded", "texture_memory_read_bytes",
        "vertex_memory_read_bytes", "shader_alu_capacity_utilized_pct", "shaders_busy_pct", "shaders_stalled_pct",
        "stalled_on_system_memory_pct", "texture_pipes_busy_pct",
    ]
    marker_fields = group_fields.copy()
    marker_fields[0] = "marker"
    top_command_fields = [
        "id", "pass_name", "pass_match_method", "pass_marker_rows", "name", "category", "parameters",
        "context", "thread", "event_clocks", "clock_share_pct",
        "non_finite_metrics", *metric_columns,
    ]
    write_dict_csv(metric_path, metric_fields, metrics)
    write_dict_csv(command_path, group_fields, command_stats)
    write_dict_csv(top_command_path, top_command_fields, top_command_rows)
    write_dict_csv(marker_path, marker_fields, marker_stats)
    write_dict_csv(api_path, ["name", "category", "calls", "profiled_events"], api_stats)
    report_path.write_text(render_snapshot_report(context, top_issues), encoding="utf-8")

    return {
        "metric_path": metric_path,
        "command_path": command_path,
        "top_command_path": top_command_path,
        "marker_path": marker_path,
        "api_path": api_path,
        "report_path": report_path,
        "context": context,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="analyze-snapdragon-profiler",
        description="Analyze Snapdragon Profiler Realtime, Trace, Snapshot, and Unity Profiler CPU CSV captures without external LLM calls.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    analyze = subparsers.add_parser("analyze", help="Analyze one or more Snapdragon Profiler CSV files.")
    analyze.add_argument("csv", nargs="+", help="CSV files, directories containing CSV files, or glob patterns.")
    analyze.add_argument("--output", default="report.md", help="Write deterministic Markdown report.")
    analyze.add_argument("--context-output", default=None, help="Write JSON evidence context for Codex analysis.")
    analyze.add_argument("--top", type=int, default=8, help="Number of top bottlenecks to include.")
    analyze.add_argument("--no-llm", action="store_true", help="Compatibility flag; external LLM calls are never used.")
    analyze.add_argument("--prompt", default=None, help="Compatibility flag; Codex reads references directly.")
    analyze.add_argument("--agent-prompt", default=None, help="Compatibility flag; Codex reads references directly.")
    analyze.add_argument("--env", default=None, help="Compatibility flag; env files are ignored.")
    trace = subparsers.add_parser("analyze-trace", help="Analyze a Snapdragon Profiler Trace Capture CSV.")
    trace.add_argument("csv", type=Path, help="Trace CSV exported by Snapdragon Profiler.")
    trace.add_argument("--out-dir", type=Path, default=Path("trace_analysis"), help="Output directory.")
    trace.add_argument("--top-events", type=int, default=100, help="Longest timeline events to export.")
    trace.add_argument("--top", type=int, default=8, help="Number of top diagnostics to include.")
    trace.add_argument("--context-output", default=None, help="Write JSON evidence context for Codex analysis.")
    snapshot = subparsers.add_parser("analyze-snapshot", help="Analyze a Snapdragon Profiler Snapshot CSV.")
    snapshot.add_argument("csv", type=Path, help="Snapshot CSV exported by Snapdragon Profiler.")
    snapshot.add_argument("--out-dir", type=Path, default=Path("snapshot_analysis"), help="Output directory.")
    snapshot.add_argument("--top-commands", type=int, default=100, help="Highest-Clocks profiled commands to export.")
    snapshot.add_argument("--top-markers", type=int, default=100, help="Highest-Clocks marker groups to export.")
    snapshot.add_argument("--top", type=int, default=8, help="Number of top diagnostics to include.")
    snapshot.add_argument("--context-output", default=None, help="Write JSON evidence context for Codex analysis.")
    cpu = subparsers.add_parser(
        "analyze-cpu",
        aliases=["analyze-unity-cpu"],
        help="Analyze a Unity CPU.csv detailed export or RawFrameDataView CSV.",
    )
    cpu.add_argument("csv", type=Path, help="Unity CPU.csv detailed export or RawFrameDataView CSV.")
    cpu.add_argument("--out-dir", type=Path, default=Path("unity_cpu_analysis"), help="Output directory.")
    cpu.add_argument("--top-frames", type=int, default=100, help="Worst frame rows to export.")
    cpu.add_argument("--top-markers-per-frame", type=int, default=12, help="Markers listed in each worst frame row.")
    cpu.add_argument("--context-output", default=None, help="Write JSON evidence context for Codex analysis.")
    return parser


def analyze_command(args: argparse.Namespace) -> int:
    rows, source_files = load_csv_files(args.csv)
    summary = summarize(rows, source_files)
    issues = evaluate_rules(summary)

    output_path = Path(args.output)
    output_path.write_text(render_rule_report(summary, issues, top_n=args.top), encoding="utf-8")
    print(f"Report written: {output_path}")

    if args.context_output:
        context_path = Path(args.context_output)
        context_path.write_text(
            json.dumps(build_context(summary, issues, args.top), ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        print(f"Codex context written: {context_path}")

    return 0


def analyze_unity_cpu_command(args: argparse.Namespace) -> int:
    result = analyze_unity_cpu(args.csv, args.out_dir, args.top_frames, args.top_markers_per_frame)
    print(f"Frames: {result['frame_count']}")
    print(f"Samples: {result['sample_count']}")
    print(f"Report: {result['report_path']}")
    print(f"Frame summary: {result['frame_path']}")
    print(f"Marker summary: {result['marker_path']}")
    print(f"Worst frames: {result['worst_path']}")
    print(f"Category summary: {result['category_path']}")

    if args.context_output:
        context_path = Path(args.context_output)
        context_path.write_text(json.dumps(result["context"], ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Codex context written: {context_path}")

    return 0


def analyze_trace_command(args: argparse.Namespace) -> int:
    result = analyze_trace(args.csv, args.out_dir, args.top_events, args.top)
    summary = result["context"]["summary"]
    print(f"Rows: {summary['row_count']}")
    print(f"Timeline events: {summary['stage_event_count']}")
    print(f"GPU stage events: {summary['gpu_stage_event_count']}")
    print(f"Metric samples: {summary['metric_sample_count']}")
    print(f"Report: {result['report_path']}")
    print(f"Stage summary: {result['stage_path']}")
    print(f"Swap intervals: {result['frame_path']}")
    print(f"Metric summary: {result['metric_path']}")
    print(f"Longest events: {result['event_path']}")
    print(f"Surface summary: {result['surface_path']}")

    if args.context_output:
        context_path = Path(args.context_output)
        context_path.write_text(
            json.dumps(result["context"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Codex context written: {context_path}")

    return 0


def analyze_snapshot_command(args: argparse.Namespace) -> int:
    result = analyze_snapshot(args.csv, args.out_dir, args.top_commands, args.top_markers, args.top)
    summary = result["context"]["summary"]
    print(f"Rows: {summary['row_count']}")
    print(f"Profiled GPU commands: {summary['profiled_event_count']}")
    print(f"Marker rows: {summary['marker_row_count']}")
    print(f"API calls: {summary['api_call_count']}")
    print(f"Report: {result['report_path']}")
    print(f"Metric summary: {result['metric_path']}")
    print(f"Command summary: {result['command_path']}")
    print(f"Top commands: {result['top_command_path']}")
    print(f"Marker summary: {result['marker_path']}")
    print(f"API summary: {result['api_path']}")

    if args.context_output:
        context_path = Path(args.context_output)
        context_path.write_text(json.dumps(result["context"], ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Codex context written: {context_path}")

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "analyze":
            return analyze_command(args)
        if args.command == "analyze-trace":
            return analyze_trace_command(args)
        if args.command == "analyze-snapshot":
            return analyze_snapshot_command(args)
        if args.command in {"analyze-cpu", "analyze-unity-cpu"}:
            return analyze_unity_cpu_command(args)
    except CsvLoadError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
