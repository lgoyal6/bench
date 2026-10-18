"""Headless PNG charts of supplied record summaries. No generated data or hosting."""

import io
import math

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure


def calibration_chart(summary: dict) -> bytes:
    bins = summary["calibration"]
    if not bins:
        raise ValueError("Calibration chart needs actual pass/fail records with stated confidence")
    for bucket in bins:
        for field in ("mean_confidence", "pass_fraction"):
            if not math.isfinite(bucket[field]) or not 0 <= bucket[field] <= 1:
                raise ValueError("Invalid calibration point")
        if type(bucket["runs"]) is not int or bucket["runs"] < 1:
            raise ValueError("Calibration bin needs a positive observed sample count")
    figure = Figure(figsize=(7, 5), dpi=140, layout="constrained")
    FigureCanvasAgg(figure)
    axis = figure.subplots()
    axis.plot([0, 1], [0, 1], color="#94a3b8", linestyle="--", label="Perfect calibration")
    axis.scatter(
        [b["mean_confidence"] for b in bins],
        [b["pass_fraction"] for b in bins],
        color="#2563eb",
        s=70,
        label="Observed bins",
    )
    for bucket in bins:
        axis.annotate(
            f"n={bucket['runs']}",
            (bucket["mean_confidence"], bucket["pass_fraction"]),
            xytext=(5, -14),
            textcoords="offset points",
        )
    axis.set(
        xlim=(-0.04, 1.04),
        ylim=(-0.04, 1.04),
        xlabel="Mean stated pass probability",
        ylabel="Observed pass fraction",
        title=f"{summary['task_id']}: {summary['agent']} / {summary['model']}",
    )
    axis.text(
        0,
        -0.18,
        "Descriptive bins, not an estimate of statistical certainty. Rig errors excluded.",
        transform=axis.transAxes,
        fontsize=8,
    )
    axis.legend(loc="lower right")
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png")
    figure.clear()
    return buffer.getvalue()
