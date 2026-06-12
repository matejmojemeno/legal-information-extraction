#!/usr/bin/env python3
"""Official analysis script: summarize law-reference outputs from a final run.

This script reads the canonical law-reference analysis table, computes summary
statistics and top-ranked tables, and optionally writes thesis-ready plots for
the deterministic law-reference layer.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
import textwrap
from typing import Any, Iterable


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Official analysis stage: summarize law-reference tables and write "
            "law-focused outputs and plots."
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
        help="How many top cited laws to include in summary tables and plots.",
    )
    return parser.parse_args()


def _jsonl_iter(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            yield json.loads(line)


def _write_csv(path: Path, fieldnames: list[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


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


def _plot_top_cited_laws(path: Path, rows: list[dict[str, Any]]) -> bool:
    try:
        import matplotlib.pyplot as plt
        from matplotlib.ticker import FuncFormatter
    except Exception:
        return False

    if not rows:
        return False

    labels = []
    counts = []
    for row in rows:
        law_id = row["resolved_law_id"]
        name = row["resolved_law_name"] or ""
        short_name = textwrap.shorten(name, width=42, placeholder="...") if name else ""
        label = f"{law_id}\n{short_name}" if short_name else law_id
        labels.append(label)
        counts.append(row["occurrence_count"])

    fig_height = max(6.8, min(16, 0.5 * len(rows) + 1.8))
    fig, ax = plt.subplots(figsize=(12.2, fig_height))
    positions = list(range(len(rows)))
    ax.barh(positions, counts, color="#88A8CF", edgecolor="#7093BB", linewidth=0.6)
    ax.set_yticks(positions)
    ax.set_yticklabels(labels, fontsize=10)
    ax.invert_yaxis()
    ax.set_xlabel("Citation occurrences", fontsize=11)
    ax.set_title("Most Frequently Cited Legal Acts", fontsize=13, pad=12)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{int(value):,}".replace(",", " ")))
    _style_axes(ax)
    ax.tick_params(axis="x", labelsize=10)
    fig.tight_layout()
    _save_figure(fig, path)
    plt.close(fig)
    return True


def main() -> None:
    args = parse_args()
    run_root = Path(args.run_root).resolve()
    analysis_dir = Path(args.analysis_dir).resolve() if args.analysis_dir else run_root / "analysis"
    input_path = analysis_dir / "law_reference_table.jsonl"

    if not input_path.exists():
        raise SystemExit(f"Missing law analysis table: {input_path}")

    total_rows = 0
    resolved_rows = 0
    unresolved_rows = 0
    unique_documents: set[str] = set()
    unique_resolved_laws: set[str] = set()

    counts_by_source = Counter()
    resolved_by_source = Counter()
    unresolved_by_source = Counter()
    counts_by_classification = Counter()
    counts_by_citation_type = Counter()
    counts_by_year = Counter()
    resolved_law_counts = Counter()
    resolved_law_names: dict[str, str] = {}
    source_law_counts: defaultdict[tuple[str, str], int] = defaultdict(int)
    unresolved_classification_counts: defaultdict[tuple[str, str], int] = defaultdict(int)
    provision_counts = Counter()

    for row in _jsonl_iter(input_path):
        total_rows += 1
        document_id = row.get("document_id")
        if isinstance(document_id, str):
            unique_documents.add(document_id)

        source = row.get("source") or "unknown"
        classification = row.get("classification") or "unknown"
        citation_type = row.get("citation_type") or "unknown"
        year = row.get("decision_year")
        is_resolved = bool(row.get("is_resolved"))
        law_id = row.get("resolved_law_id")
        law_name = row.get("resolved_law_name") or ""
        detail_number = row.get("detail_number")

        counts_by_source[source] += 1
        counts_by_classification[classification] += 1
        counts_by_citation_type[citation_type] += 1
        if isinstance(year, int):
            counts_by_year[year] += 1

        if is_resolved and isinstance(law_id, str) and law_id:
            resolved_rows += 1
            unique_resolved_laws.add(law_id)
            resolved_law_counts[law_id] += 1
            resolved_law_names.setdefault(law_id, law_name)
            resolved_by_source[source] += 1
            source_law_counts[(source, law_id)] += 1
            if isinstance(detail_number, str) and detail_number:
                provision_counts[(law_id, detail_number)] += 1
        else:
            unresolved_rows += 1
            unresolved_by_source[source] += 1
            unresolved_classification_counts[(source, classification)] += 1

    top_law_rows = []
    for law_id, count in resolved_law_counts.most_common(args.top_n):
        top_law_rows.append(
            {
                "resolved_law_id": law_id,
                "resolved_law_name": resolved_law_names.get(law_id, ""),
                "occurrence_count": count,
                "source_count": sum(
                    value for (source_name, current_law_id), value in source_law_counts.items() if current_law_id == law_id
                ),
            }
        )

    top_provision_rows = []
    for (law_id, detail_number), count in provision_counts.most_common(args.top_n):
        top_provision_rows.append(
            {
                "resolved_law_id": law_id,
                "resolved_law_name": resolved_law_names.get(law_id, ""),
                "detail_number": detail_number,
                "occurrence_count": count,
            }
        )

    per_source_rows = []
    for source in sorted(counts_by_source):
        total = counts_by_source[source]
        resolved = resolved_by_source[source]
        unresolved = unresolved_by_source[source]
        per_source_rows.append(
            {
                "source": source,
                "total_occurrences": total,
                "resolved_occurrences": resolved,
                "unresolved_occurrences": unresolved,
                "resolved_share": round(resolved / total, 6) if total else 0.0,
            }
        )

    source_law_rows = []
    for (source, law_id), count in sorted(source_law_counts.items(), key=lambda item: (-item[1], item[0][0], item[0][1])):
        source_law_rows.append(
            {
                "source": source,
                "resolved_law_id": law_id,
                "resolved_law_name": resolved_law_names.get(law_id, ""),
                "occurrence_count": count,
            }
        )

    unresolved_rows_by_classification = []
    for (source, classification), count in sorted(
        unresolved_classification_counts.items(),
        key=lambda item: (-item[1], item[0][0], item[0][1]),
    ):
        unresolved_rows_by_classification.append(
            {
                "source": source,
                "classification": classification,
                "occurrence_count": count,
            }
        )

    yearly_rows = []
    for year, count in sorted(counts_by_year.items()):
        yearly_rows.append({"decision_year": year, "occurrence_count": count})

    classification_rows = [
        {"classification": classification, "occurrence_count": count}
        for classification, count in counts_by_classification.most_common()
    ]
    citation_type_rows = [
        {"citation_type": citation_type, "occurrence_count": count}
        for citation_type, count in counts_by_citation_type.most_common()
    ]

    output_dir = analysis_dir
    plots_dir = output_dir / "plots"
    top_plot_path = plots_dir / "top_cited_laws.png"
    plot_written = _plot_top_cited_laws(top_plot_path, top_law_rows)

    summary = {
        "run_root": str(run_root),
        "analysis_dir": str(output_dir),
        "law_reference_table": str(input_path),
        "total_occurrences": total_rows,
        "resolved_occurrences": resolved_rows,
        "unresolved_occurrences": unresolved_rows,
        "resolved_share": round(resolved_rows / total_rows, 6) if total_rows else 0.0,
        "unique_documents": len(unique_documents),
        "unique_resolved_laws": len(unique_resolved_laws),
        "top_n": args.top_n,
        "plots": {
            "top_cited_laws_png_written": plot_written,
            "top_cited_laws_png": str(top_plot_path) if plot_written else None,
        },
        "top_cited_laws": top_law_rows,
        "top_citation_types": citation_type_rows[:10],
        "top_classifications": classification_rows[:10],
        "per_source_summary": per_source_rows,
    }

    _write_csv(output_dir / "top_cited_laws.csv", list(top_law_rows[0].keys()) if top_law_rows else ["resolved_law_id", "resolved_law_name", "occurrence_count", "source_count"], top_law_rows)
    _write_csv(output_dir / "top_cited_provisions.csv", list(top_provision_rows[0].keys()) if top_provision_rows else ["resolved_law_id", "resolved_law_name", "detail_number", "occurrence_count"], top_provision_rows)
    _write_csv(output_dir / "law_counts_by_source.csv", ["source", "total_occurrences", "resolved_occurrences", "unresolved_occurrences", "resolved_share"], per_source_rows)
    _write_csv(output_dir / "law_counts_by_source_and_law.csv", ["source", "resolved_law_id", "resolved_law_name", "occurrence_count"], source_law_rows)
    _write_csv(output_dir / "law_unresolved_by_source_and_classification.csv", ["source", "classification", "occurrence_count"], unresolved_rows_by_classification)
    _write_csv(output_dir / "law_counts_by_year.csv", ["decision_year", "occurrence_count"], yearly_rows)
    _write_csv(output_dir / "law_counts_by_classification.csv", ["classification", "occurrence_count"], classification_rows)
    _write_csv(output_dir / "law_counts_by_citation_type.csv", ["citation_type", "occurrence_count"], citation_type_rows)

    (output_dir / "law_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        "[law-analysis] "
        f"rows={total_rows} resolved={resolved_rows} unresolved={unresolved_rows} "
        f"unique_docs={len(unique_documents)} unique_laws={len(unique_resolved_laws)}"
    )
    if plot_written:
        print(f"[law-analysis] wrote plot: {top_plot_path}")


if __name__ == "__main__":
    main()
