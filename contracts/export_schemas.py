"""Write JSON Schemas for non-Python code (the QNX rig). Run: uv run python export_schemas.py"""

import json
from pathlib import Path

from bench_contracts import (
    Award, Bid, FaultSchedule, JobPosted, RunRequest, RunResult, Settlement, Submission, Task, Verdict,
)

OUT = Path(__file__).parent / "schemas"

for model in (Task, RunRequest, RunResult, FaultSchedule, JobPosted, Bid, Award, Submission, Verdict, Settlement):
    OUT.mkdir(exist_ok=True)
    (OUT / f"{model.__name__}.schema.json").write_text(json.dumps(model.model_json_schema(), indent=2) + "\n")
