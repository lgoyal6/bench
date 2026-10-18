"""Export analytics for actual records to an explicitly supplied output folder."""

import argparse
import hashlib
import json
from pathlib import Path

from bench_ui.charts import calibration_chart

from .analytics import summarize
from .market import load_records


def main():
    parser = argparse.ArgumentParser(
        description="Export evidence-based Bench statistics and calibration PNGs"
    )
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        source_bytes = args.records.read_bytes()
        summaries = summarize(load_records(args.records))
        if args.records.read_bytes() != source_bytes:
            raise ValueError("Run history changed while reading; retry with a stable snapshot")
        # Render before writing anything, so invalid inputs never produce partial reports.
        charts = {}
        for summary in summaries:
            if summary.calibration:
                key = hashlib.sha256(
                    json.dumps(
                        [
                            summary.task_id,
                            summary.agent,
                            summary.model,
                        ]
                    ).encode()
                ).hexdigest()[:16]
                charts[f"calibration-{key}.png"] = calibration_chart(summary.model_dump())
        output = args.output_dir.resolve()
        if output.exists():
            raise ValueError(
                "Choose a new output directory; existing evidence will not be overwritten"
            )
        output.mkdir(parents=True, mode=0o700)
        for filename, content in charts.items():
            (output / filename).write_bytes(content)
        (output / "summary.json").write_text(
            json.dumps(
                {
                    "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
                    "groups": [s.model_dump() for s in summaries],
                    "calibration_charts": sorted(charts),
                    "disclosure": "Supplied run records, not verified provider adoption. "
                    "No cost or held-out learning results inferred.",
                },
                indent=2,
            )
        )
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Analytics export refused: {exc}\n")
    print(f"Exported {len(summaries)} groups and {len(charts)} calibration PNGs to {output}")


if __name__ == "__main__":
    main()
