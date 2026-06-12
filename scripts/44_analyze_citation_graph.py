#!/usr/bin/env python3
"""Official analysis script: summarize citation-graph outputs from a final run.

This script reads the canonical document-reference edge table, computes graph
summary statistics, builds institution-level matrices, ranks influential and
citation-dense decisions, and writes thesis-ready graph figures.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict, deque
from pathlib import Path
import textwrap
from typing import Any, Iterable


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Official analysis stage: build citation-graph summaries, matrices, "
            "rankings, and figures from the canonical edge table."
        )
    )
    parser.add_argument(
        "--run-root",
        required=True,
        help="Path to a final production run root.",
    )
    parser.add_argument(
        "--analysis-dir",
        default=None,
        help="Optional analysis directory. Defaults to <run-root>/analysis.",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=15,
        help="How many top documents to include in rankings and plots.",
    )
    return parser.parse_args()


def _jsonl_iter(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            yield json.loads(line)


def _write_csv(
    path: Path, fieldnames: list[str], rows: Iterable[dict[str, Any]]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _safe_slug(value: str) -> str:
    return value.replace("/", "_").replace(" ", "_")


def _short_label(document_id: str, display_identifier: str | None) -> str:
    if display_identifier:
        return display_identifier
    return document_id


SOURCE_COLORS = {
    "nalus": "#F5B878",
    "ns": "#88A8CF",
    "nss": "#A8C88F",
    "uohs": "#C9B06A",
    "unknown": "#7f7f7f",
}

SOURCE_LABELS = {
    "nalus": "Constitutional Court",
    "ns": "Supreme Court",
    "nss": "Supreme Administrative Court",
    "uohs": "Office for the Protection of Competition",
    "unknown": "Unknown",
}


def _source_label(source: str) -> str:
    return SOURCE_LABELS.get(source, source)


def _wrapped_source_label(source: str) -> str:
    label = _source_label(source)
    wrapped = {
        "Constitutional Court": "Constitutional\nCourt",
        "Supreme Court": "Supreme\nCourt",
        "Supreme Administrative Court": "Supreme\nAdministrative\nCourt",
        "Office for the Protection of Competition": "Office for the Protection\nof Competition",
    }
    return wrapped.get(label, label)


def _add_source_legend(ax, sources: Iterable[str]) -> None:
    try:
        from matplotlib.patches import Patch
    except Exception:
        return

    seen = []
    for source in sources:
        if source not in seen:
            seen.append(source)
    handles = [
        Patch(
            facecolor=SOURCE_COLORS.get(source, SOURCE_COLORS["unknown"]),
            label=_source_label(source),
        )
        for source in seen
    ]
    if handles:
        ax.legend(
            handles=handles,
            title="Institution",
            loc="center left",
            bbox_to_anchor=(1.01, 0.5),
            frameon=True,
            borderaxespad=0.0,
        )


def _save_figure(fig, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=220, bbox_inches="tight")
    for suffix in [".pdf", ".svg"]:
        fig.savefig(path.with_suffix(suffix), bbox_inches="tight")


def _style_axes(ax) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#b8b8b8")
    ax.spines["bottom"].set_color("#b8b8b8")
    ax.spines["left"].set_linewidth(0.8)
    ax.spines["bottom"].set_linewidth(0.8)
    ax.grid(axis="x", color="#d9d9d9", linewidth=0.6, alpha=0.7)
    ax.set_axisbelow(True)


def _plot_top_documents(
    path: Path,
    rows: list[dict[str, Any]],
    title: str,
    value_key: str,
    x_label: str = "Citation count",
) -> bool:
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return False

    if not rows:
        return False

    labels = [
        textwrap.shorten(
            _short_label(row["document_id"], row.get("display_identifier")),
            width=32,
            placeholder="...",
        )
        for row in rows
    ]
    counts = [row[value_key] for row in rows]
    colors = [
        SOURCE_COLORS.get(row.get("source") or "unknown", SOURCE_COLORS["unknown"])
        for row in rows
    ]

    fig_height = max(6.8, min(16, 0.48 * len(rows) + 1.8))
    fig, ax = plt.subplots(figsize=(12.2, fig_height))
    positions = list(range(len(rows)))
    ax.barh(positions, counts, color=colors)
    ax.set_yticks(positions)
    ax.set_yticklabels(labels, fontsize=10)
    ax.invert_yaxis()
    ax.set_xlabel(x_label, fontsize=11)
    ax.set_title(title, fontsize=13, pad=12)
    _style_axes(ax)
    ax.tick_params(axis="x", labelsize=10)
    _add_source_legend(ax, [row.get("source") or "unknown" for row in rows])
    fig.tight_layout(rect=(0, 0, 0.86, 1))
    _save_figure(fig, path)
    plt.close(fig)
    return True


def _plot_heatmap(
    path: Path,
    sources: list[str],
    matrix: dict[tuple[str, str], float],
    title: str,
    colorbar_label: str,
) -> bool:
    try:
        import matplotlib.pyplot as plt
        from matplotlib.colors import LinearSegmentedColormap, LogNorm
    except Exception:
        return False

    if not sources:
        return False

    grid = []
    for source in sources:
        row = []
        for target in sources:
            row.append(matrix.get((source, target), 0))
        grid.append(row)

    cmap = LinearSegmentedColormap.from_list(
        "thesis_blue_log",
        ["#FFFFFF", "#E2EBF9", "#BFD1E8", "#88A8CF"],
    )
    positive_values = [
        float(value) for row in grid for value in row if float(value) > 0
    ]
    norm = (
        LogNorm(vmin=min(positive_values), vmax=max(positive_values))
        if positive_values
        else None
    )
    fig_width = max(6.6, 1.75 * len(sources) + 1.6)
    fig_height = max(5.2, 1.45 * len(sources) + 1.2)
    fig, ax = plt.subplots(figsize=(fig_width, fig_height))
    if norm is not None:
        safe_grid = [[max(float(value), norm.vmin) for value in row] for row in grid]
        im = ax.imshow(safe_grid, cmap=cmap, norm=norm)
    else:
        im = ax.imshow(grid, cmap=cmap)
    ax.set_xticks(range(len(sources)))
    ax.set_yticks(range(len(sources)))
    wrapped_labels = [_wrapped_source_label(source) for source in sources]
    ax.set_xticklabels(wrapped_labels, rotation=0, ha="center", fontsize=10)
    ax.set_yticklabels(wrapped_labels, fontsize=10)
    ax.set_xlabel("Target court", fontsize=10, labelpad=14)
    ax.set_ylabel("Source court", fontsize=10, labelpad=14)
    ax.set_title(title, fontsize=11, pad=8)
    ax.set_xticks([x - 0.5 for x in range(1, len(sources))], minor=True)
    ax.set_yticks([y - 0.5 for y in range(1, len(sources))], minor=True)
    ax.grid(which="minor", color="#ffffff", linestyle="-", linewidth=1.1)
    ax.tick_params(which="minor", bottom=False, left=False)
    for row_idx, row in enumerate(grid):
        for col_idx, value in enumerate(row):
            if float(value).is_integer():
                label = f"{int(value):,}".replace(",", " ")
            else:
                label = f"{value:.1f}"
            ax.text(
                col_idx,
                row_idx,
                label,
                ha="center",
                va="center",
                fontsize=8.5,
                color="#1f1f1f",
            )
    cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label(colorbar_label, fontsize=10)
    cbar.ax.tick_params(labelsize=9)
    for spine in ax.spines.values():
        spine.set_visible(False)
    fig.tight_layout()
    _save_figure(fig, path)
    plt.close(fig)
    return True


def _plot_small_subgraph(path: Path, graph_data: dict[str, Any]) -> bool:
    try:
        import matplotlib.pyplot as plt
        import networkx as nx
    except Exception:
        return False

    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])
    if len(nodes) < 2 or not edges:
        return False

    graph = nx.DiGraph()
    for node in nodes:
        graph.add_node(
            node["id"],
            label=textwrap.shorten(node["label"], width=26, placeholder="..."),
            source=node["source"],
            size=node["size"],
        )

    for edge in edges:
        graph.add_edge(edge["source"], edge["target"], weight=edge["weight"])

    pos = nx.spring_layout(
        graph, seed=13, k=1.2 / math.sqrt(max(2, graph.number_of_nodes()))
    )
    fig, ax = plt.subplots(figsize=(12, 9))
    node_colors = [
        SOURCE_COLORS.get(data["source"], SOURCE_COLORS["unknown"])
        for _, data in graph.nodes(data=True)
    ]
    node_sizes = [
        max(650, min(2600, 130 * data["size"])) for _, data in graph.nodes(data=True)
    ]
    nx.draw_networkx_nodes(
        graph, pos, node_size=node_sizes, node_color=node_colors, ax=ax
    )
    nx.draw_networkx_labels(
        graph,
        pos,
        labels={node: data["label"] for node, data in graph.nodes(data=True)},
        font_size=8,
        ax=ax,
    )
    widths = [
        0.8 + math.log1p(graph.edges[edge]["weight"]) * 1.2 for edge in graph.edges
    ]
    nx.draw_networkx_edges(
        graph,
        pos,
        width=widths,
        alpha=0.65,
        edge_color="#264653",
        arrows=True,
        arrowsize=18,
        ax=ax,
    )
    ax.set_title("Citation ego network around the most cited decision")
    ax.axis("off")
    _add_source_legend(ax, [data["source"] for _, data in graph.nodes(data=True)])
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    _save_figure(fig, path)
    plt.close(fig)
    return True


def _compute_pagerank(
    collapsed_exact_weights: Counter[tuple[str, str]],
    alpha: float = 0.85,
    max_iter: int = 20,
    tol: float = 1.0e-8,
) -> dict[str, float]:
    nodes: set[str] = set()
    outgoing: defaultdict[str, dict[str, float]] = defaultdict(dict)
    out_weight_sums: Counter[str] = Counter()

    for (source_id, target_id), weight in collapsed_exact_weights.items():
        if source_id == target_id:
            continue
        nodes.add(source_id)
        nodes.add(target_id)
        outgoing[source_id][target_id] = outgoing[source_id].get(
            target_id, 0.0
        ) + float(weight)
        out_weight_sums[source_id] += float(weight)

    if not nodes:
        return {}

    node_list = sorted(nodes)
    n = len(node_list)
    base = (1.0 - alpha) / n
    scores = {node: 1.0 / n for node in node_list}

    for _ in range(max_iter):
        new_scores = {node: base for node in node_list}
        sink_score = sum(
            scores[node] for node in node_list if out_weight_sums.get(node, 0.0) == 0.0
        )
        sink_share = alpha * sink_score / n
        for node in node_list:
            new_scores[node] += sink_share

        for source_id in node_list:
            total_out = out_weight_sums.get(source_id, 0.0)
            if total_out <= 0.0:
                continue
            source_score = scores[source_id]
            for target_id, weight in outgoing[source_id].items():
                new_scores[target_id] += alpha * source_score * (weight / total_out)

        delta = sum(abs(new_scores[node] - scores[node]) for node in node_list)
        scores = new_scores
        if delta < tol:
            break

    total = sum(scores.values())
    if total > 0.0:
        scores = {node: score / total for node, score in scores.items()}
    return scores


def main() -> None:
    args = parse_args()
    run_root = Path(args.run_root).resolve()
    analysis_dir = (
        Path(args.analysis_dir).resolve()
        if args.analysis_dir
        else run_root / "analysis"
    )
    edge_path = analysis_dir / "document_reference_edge_table.jsonl"
    node_path = analysis_dir / "document_reference_node_table.jsonl"

    if not edge_path.exists():
        raise SystemExit(f"Missing edge table: {edge_path}")

    node_metadata: dict[str, dict[str, Any]] = {}
    if node_path.exists():
        for row in _jsonl_iter(node_path):
            document_id = row.get("document_id")
            if isinstance(document_id, str) and document_id:
                node_metadata[document_id] = row

    total_edge_occurrences = 0
    exact_edge_occurrences = 0
    same_proceeding_edge_occurrences = 0

    source_to_source_counts = Counter()
    source_to_source_exact_counts = Counter()

    unique_source_documents: set[str] = set()
    unique_target_documents: set[str] = set()
    unique_nodes: set[str] = set()
    source_document_counts: Counter[str] = Counter()

    incoming_occ_exact = Counter()
    outgoing_occ_exact = Counter()
    incoming_occ_all = Counter()
    outgoing_occ_all = Counter()

    incoming_neighbors_exact: defaultdict[str, set[str]] = defaultdict(set)
    outgoing_neighbors_exact: defaultdict[str, set[str]] = defaultdict(set)
    undirected_exact_adj: defaultdict[str, set[str]] = defaultdict(set)
    collapsed_exact_weights = Counter()
    collapsed_all_weights = Counter()
    reference_type_counts = Counter()
    link_method_counts = Counter()
    link_scope_counts = Counter()

    for row in _jsonl_iter(edge_path):
        source_id = row.get("source_document_id")
        target_id = row.get("target_document_id")
        source = row.get("source") or "unknown"
        target = row.get("target") or "unknown"
        link_scope = row.get("link_scope") or "unknown"
        reference_type = row.get("reference_type") or "unknown"
        link_method = row.get("link_method") or "unknown"

        if not isinstance(source_id, str) or not isinstance(target_id, str):
            continue

        total_edge_occurrences += 1
        unique_source_documents.add(source_id)
        unique_target_documents.add(target_id)
        unique_nodes.add(source_id)
        unique_nodes.add(target_id)
        source_document_counts[source] += 0
        source_to_source_counts[(source, target)] += 1
        reference_type_counts[reference_type] += 1
        link_method_counts[link_method] += 1
        link_scope_counts[link_scope] += 1
        collapsed_all_weights[(source_id, target_id)] += 1
        incoming_occ_all[target_id] += 1
        outgoing_occ_all[source_id] += 1

        is_same = bool(row.get("is_same_proceeding")) or link_scope == "same_proceeding"
        if is_same:
            same_proceeding_edge_occurrences += 1
        else:
            exact_edge_occurrences += 1
            source_to_source_exact_counts[(source, target)] += 1
            incoming_occ_exact[target_id] += 1
            outgoing_occ_exact[source_id] += 1
            incoming_neighbors_exact[target_id].add(source_id)
            outgoing_neighbors_exact[source_id].add(target_id)
            collapsed_exact_weights[(source_id, target_id)] += 1
            undirected_exact_adj[source_id].add(target_id)
            undirected_exact_adj[target_id].add(source_id)

    for document_id in unique_source_documents | unique_target_documents:
        meta = node_metadata.get(document_id, {})
        source = meta.get("source") or "unknown"
        source_document_counts[source] += 1

    all_sources = sorted(
        {source for source, _ in source_to_source_counts}
        | {target for _, target in source_to_source_counts}
    )

    incoming_occ_by_target_source = Counter()
    cited_candidates = []
    for document_id, count in incoming_occ_exact.items():
        meta = node_metadata.get(document_id, {})
        incoming_occ_by_target_source[meta.get("source", "")] += count
        cited_candidates.append(
            {
                "document_id": document_id,
                "document_path": meta.get("document_path", ""),
                "source": meta.get("source", ""),
                "display_identifier": meta.get("display_identifier", ""),
                "decision_year": meta.get("decision_year", ""),
                "exact_incoming_occurrences": count,
                "exact_indegree": len(incoming_neighbors_exact.get(document_id, set())),
                "same_proceeding_incoming": int(
                    meta.get("in_degree_same_proceeding", 0) or 0
                ),
            }
        )
    cited_candidates.sort(
        key=lambda row: (
            -row["exact_indegree"],
            -row["exact_incoming_occurrences"],
            row["document_id"],
        )
    )
    top_cited_rows = cited_candidates[: args.top_n]

    pagerank_scores = _compute_pagerank(collapsed_exact_weights)
    pagerank_candidates = []
    for document_id, score in pagerank_scores.items():
        meta = node_metadata.get(document_id, {})
        pagerank_candidates.append(
            {
                "document_id": document_id,
                "document_path": meta.get("document_path", ""),
                "source": meta.get("source", ""),
                "display_identifier": meta.get("display_identifier", ""),
                "decision_year": meta.get("decision_year", ""),
                "pagerank": score,
                "exact_indegree": len(incoming_neighbors_exact.get(document_id, set())),
                "exact_incoming_occurrences": incoming_occ_exact.get(document_id, 0),
            }
        )
    pagerank_candidates.sort(
        key=lambda row: (
            -row["pagerank"],
            -row["exact_indegree"],
            row["document_id"],
        )
    )
    top_pagerank_rows = pagerank_candidates[: args.top_n]

    citing_candidates = []
    for document_id, count in outgoing_occ_exact.items():
        meta = node_metadata.get(document_id, {})
        citing_candidates.append(
            {
                "document_id": document_id,
                "document_path": meta.get("document_path", ""),
                "source": meta.get("source", ""),
                "display_identifier": meta.get("display_identifier", ""),
                "decision_year": meta.get("decision_year", ""),
                "exact_outgoing_occurrences": count,
                "exact_outdegree": len(
                    outgoing_neighbors_exact.get(document_id, set())
                ),
                "same_proceeding_outgoing": int(
                    meta.get("out_degree_same_proceeding", 0) or 0
                ),
            }
        )
    citing_candidates.sort(
        key=lambda row: (
            -row["exact_outdegree"],
            -row["exact_outgoing_occurrences"],
            row["document_id"],
        )
    )
    top_citing_rows = citing_candidates[: args.top_n]

    matrix_rows = []
    for source in all_sources:
        row = {"source": source}
        for target in all_sources:
            row[target] = source_to_source_counts.get((source, target), 0)
        matrix_rows.append(row)

    edge_count_rows = []
    for (source, target), count in sorted(
        source_to_source_counts.items(),
        key=lambda item: (-item[1], item[0][0], item[0][1]),
    ):
        edge_count_rows.append(
            {
                "source": source,
                "target": target,
                "occurrence_count": count,
                "exact_occurrence_count": source_to_source_exact_counts.get(
                    (source, target), 0
                ),
                "same_proceeding_occurrence_count": count
                - source_to_source_exact_counts.get((source, target), 0),
            }
        )

    indegree_distribution_rows = []
    for document_id in sorted(unique_nodes):
        exact_indegree = len(incoming_neighbors_exact.get(document_id, set()))
        meta = node_metadata.get(document_id, {})
        indegree_distribution_rows.append(
            {
                "document_id": document_id,
                "source": meta.get("source", ""),
                "display_identifier": meta.get("display_identifier", ""),
                "exact_indegree": exact_indegree,
                "exact_incoming_occurrences": incoming_occ_exact.get(document_id, 0),
                "same_proceeding_incoming_occurrences": incoming_occ_all.get(
                    document_id, 0
                )
                - incoming_occ_exact.get(document_id, 0),
            }
        )

    visited: set[str] = set()
    component_sizes = []
    for node in sorted(unique_nodes):
        if node in visited:
            continue
        queue = deque([node])
        visited.add(node)
        size = 0
        while queue:
            current = queue.popleft()
            size += 1
            for neighbor in undirected_exact_adj.get(current, set()):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)
        component_sizes.append(size)
    component_sizes.sort(reverse=True)

    plots_dir = analysis_dir / "plots"
    for obsolete_plot in [
        plots_dir / "citation_indegree_histogram.png",
        plots_dir / "source_to_source_heatmap.png",
    ]:
        if obsolete_plot.exists():
            obsolete_plot.unlink()
    top_cited_plot = plots_dir / "top_cited_documents.png"
    top_citing_plot = plots_dir / "top_citing_documents.png"
    top_pagerank_plot = plots_dir / "top_pagerank_documents.png"
    heatmap_plot = plots_dir / "source_to_source_heatmap_courts.png"
    normalized_heatmap_plot = (
        plots_dir / "source_to_source_heatmap_normalized_all_sources.png"
    )
    subgraph_plot = plots_dir / "small_citation_subgraph.png"

    top_cited_plot_written = _plot_top_documents(
        top_cited_plot,
        top_cited_rows,
        title="Most Frequently Cited Decisions",
        value_key="exact_indegree",
    )
    top_citing_plot_written = _plot_top_documents(
        top_citing_plot,
        top_citing_rows,
        title="Top citing documents by exact outdegree",
        value_key="exact_outdegree",
    )
    top_pagerank_plot_written = _plot_top_documents(
        top_pagerank_plot,
        top_pagerank_rows,
        title="Most Structurally Central Decisions by PageRank",
        value_key="pagerank",
        x_label="PageRank score",
    )
    court_sources = [
        source for source in ["nalus", "ns", "nss"] if source in all_sources
    ]
    court_matrix = {
        (source, target): source_to_source_counts.get((source, target), 0)
        for source in court_sources
        for target in court_sources
    }
    heatmap_plot_written = _plot_heatmap(
        heatmap_plot,
        court_sources,
        court_matrix,
        title="Court-to-Court Citation Counts",
        colorbar_label="Citation occurrences",
    )

    normalized_all_source_matrix = {}
    for source in all_sources:
        source_doc_count = source_document_counts.get(source, 0)
        for target in all_sources:
            count = source_to_source_counts.get((source, target), 0)
            normalized_all_source_matrix[(source, target)] = (
                (count / source_doc_count) * 1000 if source_doc_count else 0.0
            )
    normalized_heatmap_plot_written = _plot_heatmap(
        normalized_heatmap_plot,
        all_sources,
        normalized_all_source_matrix,
        title="Normalized Cross-Source Citation Intensity",
        colorbar_label="Citations per 1 000 source documents",
    )

    most_cited_document_id = (
        top_cited_rows[0]["document_id"] if top_cited_rows else None
    )
    subgraph_plot_written = False
    subgraph_description = None
    if most_cited_document_id:
        inbound_sources = [
            (source_id, weight)
            for (source_id, target_id), weight in collapsed_exact_weights.items()
            if target_id == most_cited_document_id
        ]
        inbound_sources.sort(key=lambda item: (-item[1], item[0]))
        inbound_sources = inbound_sources[:10]
        outbound_targets = [
            (target_id, weight)
            for (source_id, target_id), weight in collapsed_exact_weights.items()
            if source_id == most_cited_document_id
        ]
        outbound_targets.sort(key=lambda item: (-item[1], item[0]))
        outbound_targets = outbound_targets[:4]
        subgraph_nodes = {most_cited_document_id}
        subgraph_nodes.update(source_id for source_id, _ in inbound_sources)
        subgraph_nodes.update(target_id for target_id, _ in outbound_targets)
        nodes_payload = []
        for node_id in sorted(subgraph_nodes):
            meta = node_metadata.get(node_id, {})
            nodes_payload.append(
                {
                    "id": node_id,
                    "label": _short_label(node_id, meta.get("display_identifier")),
                    "source": meta.get("source", "unknown"),
                    "size": max(1, len(incoming_neighbors_exact.get(node_id, set()))),
                }
            )
        edges_payload = []
        for source_id, weight in inbound_sources:
            edges_payload.append(
                {
                    "source": source_id,
                    "target": most_cited_document_id,
                    "weight": weight,
                }
            )
        for target_id, weight in outbound_targets:
            edges_payload.append(
                {
                    "source": most_cited_document_id,
                    "target": target_id,
                    "weight": weight,
                }
            )
        subgraph_plot_written = _plot_small_subgraph(
            subgraph_plot,
            {"nodes": nodes_payload, "edges": edges_payload},
        )
        subgraph_description = {
            "central_document_id": most_cited_document_id,
            "central_label": _short_label(
                most_cited_document_id,
                node_metadata.get(most_cited_document_id, {}).get("display_identifier"),
            ),
            "incoming_neighbors": len(inbound_sources),
            "outgoing_neighbors": len(outbound_targets),
        }

    uohs_only_rows = [
        row
        for row in edge_count_rows
        if row["source"] == "uohs" or row["target"] == "uohs"
    ]
    uohs_scope_summary = [
        {
            "scope": "uohs_exact_decision",
            "occurrence_count": sum(
                row["exact_occurrence_count"]
                for row in edge_count_rows
                if row["source"] == "uohs" or row["target"] == "uohs"
            ),
        },
        {
            "scope": "uohs_same_proceeding",
            "occurrence_count": sum(
                row["same_proceeding_occurrence_count"]
                for row in edge_count_rows
                if row["source"] == "uohs" or row["target"] == "uohs"
            ),
        },
    ]

    summary = {
        "run_root": str(run_root),
        "analysis_dir": str(analysis_dir),
        "edge_table": str(edge_path),
        "node_table": str(node_path) if node_path.exists() else None,
        "total_edge_occurrences": total_edge_occurrences,
        "exact_edge_occurrences": exact_edge_occurrences,
        "same_proceeding_edge_occurrences": same_proceeding_edge_occurrences,
        "unique_source_documents": len(unique_source_documents),
        "unique_target_documents": len(unique_target_documents),
        "unique_graph_nodes": len(unique_nodes),
        "unique_collapsed_exact_edges": len(collapsed_exact_weights),
        "unique_collapsed_all_edges": len(collapsed_all_weights),
        "weak_component_count_exact": len(component_sizes),
        "largest_weak_component_size_exact": (
            component_sizes[0] if component_sizes else 0
        ),
        "top_n": args.top_n,
        "top_reference_types": [
            {"reference_type": reference_type, "occurrence_count": count}
            for reference_type, count in reference_type_counts.most_common(10)
        ],
        "top_link_methods": [
            {"link_method": method, "occurrence_count": count}
            for method, count in link_method_counts.most_common(10)
        ],
        "link_scope_counts": [
            {"link_scope": scope, "occurrence_count": count}
            for scope, count in link_scope_counts.most_common()
        ],
        "top_cited_documents": top_cited_rows,
        "top_citing_documents": top_citing_rows,
        "top_pagerank_documents": top_pagerank_rows,
        "component_sizes_exact_top20": component_sizes[:20],
        "plots": {
            "top_cited_documents_png_written": top_cited_plot_written,
            "top_citing_documents_png_written": top_citing_plot_written,
            "top_pagerank_documents_png_written": top_pagerank_plot_written,
            "source_to_source_heatmap_courts_png_written": heatmap_plot_written,
            "source_to_source_heatmap_normalized_all_sources_png_written": normalized_heatmap_plot_written,
            "small_citation_subgraph_png_written": subgraph_plot_written,
        },
        "subgraph_description": subgraph_description,
        "uohs_scope_summary": uohs_scope_summary,
        "normalized_heatmap_note": "Cells are normalized by the number of documents in the source institution and reported as citations per 1 000 source documents.",
    }

    _write_csv(
        output_path := analysis_dir / "source_to_source_matrix.csv",
        ["source", *all_sources],
        matrix_rows,
    )
    _write_csv(
        analysis_dir / "source_to_source_matrix_normalized_by_source_docs.csv",
        ["source", *all_sources],
        [
            {
                **{"source": row_source},
                **{
                    target: round(
                        normalized_all_source_matrix.get((row_source, target), 0.0), 6
                    )
                    for target in all_sources
                },
            }
            for row_source in all_sources
        ],
    )
    _write_csv(
        analysis_dir / "source_to_source_matrix_courts_only.csv",
        ["source", *court_sources],
        [
            {
                **{"source": row["source"]},
                **{target: row[target] for target in court_sources},
            }
            for row in matrix_rows
            if row["source"] in court_sources
        ],
    )
    _write_csv(
        analysis_dir / "source_to_source_edge_counts.csv",
        [
            "source",
            "target",
            "occurrence_count",
            "exact_occurrence_count",
            "same_proceeding_occurrence_count",
        ],
        edge_count_rows,
    )
    _write_csv(
        analysis_dir / "source_to_source_edge_counts_uohs_focus.csv",
        [
            "source",
            "target",
            "occurrence_count",
            "exact_occurrence_count",
            "same_proceeding_occurrence_count",
        ],
        uohs_only_rows,
    )
    _write_csv(
        analysis_dir / "uohs_link_scope_summary.csv",
        ["scope", "occurrence_count"],
        uohs_scope_summary,
    )
    _write_csv(
        analysis_dir / "top_cited_documents.csv",
        [
            "document_id",
            "document_path",
            "source",
            "display_identifier",
            "decision_year",
            "exact_indegree",
            "exact_incoming_occurrences",
            "same_proceeding_incoming",
        ],
        top_cited_rows,
    )
    _write_csv(
        analysis_dir / "top_citing_documents.csv",
        [
            "document_id",
            "document_path",
            "source",
            "display_identifier",
            "decision_year",
            "exact_outdegree",
            "exact_outgoing_occurrences",
            "same_proceeding_outgoing",
        ],
        top_citing_rows,
    )
    _write_csv(
        analysis_dir / "top_pagerank_documents.csv",
        [
            "document_id",
            "document_path",
            "source",
            "display_identifier",
            "decision_year",
            "pagerank",
            "exact_indegree",
            "exact_incoming_occurrences",
        ],
        top_pagerank_rows,
    )
    _write_csv(
        analysis_dir / "citation_indegree_distribution.csv",
        [
            "document_id",
            "source",
            "display_identifier",
            "exact_indegree",
            "exact_incoming_occurrences",
            "same_proceeding_incoming_occurrences",
        ],
        indegree_distribution_rows,
    )
    (analysis_dir / "graph_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(
        "[graph-analysis] "
        f"edges={total_edge_occurrences} exact={exact_edge_occurrences} "
        f"same_proceeding={same_proceeding_edge_occurrences} nodes={len(unique_nodes)} "
        f"collapsed_exact_edges={len(collapsed_exact_weights)}"
    )


if __name__ == "__main__":
    main()
