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
from typing import Sequence


REQUIRED_COLUMNS = {
    "Process",
    "Category",
    "Metric",
    "Timestamp",
    "TimestampRaw",
    "Value",
}

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="analyze-snapdragon-profiler",
        description="Analyze Snapdragon Profiler CSV captures without external LLM calls.",
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


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "analyze":
            return analyze_command(args)
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
