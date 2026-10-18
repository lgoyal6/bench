"""Load task definitions from tasks/<id>/task.yaml."""

from pathlib import Path

import yaml
from bench_contracts import Task


class TaskNotFound(LookupError):
    pass


def task_dir(tasks_dir: Path, task_id: str) -> Path:
    path = tasks_dir / task_id
    if not (path / "task.yaml").is_file():
        raise TaskNotFound(f"no task.yaml for {task_id} in {tasks_dir}")
    return path


def load_task(tasks_dir: Path, task_id: str) -> Task:
    data = yaml.safe_load((task_dir(tasks_dir, task_id) / "task.yaml").read_text())
    task = Task.model_validate(data)
    if task.id != task_id:
        raise ValueError(f"tasks/{task_id}/task.yaml declares id {task.id}")
    return task


def list_tasks(tasks_dir: Path) -> list[str]:
    if not tasks_dir.is_dir():
        return []
    return sorted(p.parent.name for p in tasks_dir.glob("*/task.yaml"))
