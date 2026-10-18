"""Explicit backend commands. Never automatically apply schemas or insert sample data."""

import argparse
import json
from pathlib import Path

import httpx
import psycopg

from . import snowflake, telemetry, tiger
from .records import read_records


def main():
    parser = argparse.ArgumentParser(description="Bench real-data ingestion and health checks")
    parser.add_argument("command", choices=["doctor", "ingest", "answer", "probe"])
    parser.add_argument("--backend", choices=["tiger", "snowflake", "sentry"], required=True)
    parser.add_argument("--records", type=Path)
    parser.add_argument("--task-id")
    parser.add_argument("--question")
    parser.add_argument("--model")
    args = parser.parse_args()
    backend = {"tiger": tiger, "snowflake": snowflake, "sentry": telemetry}[args.backend]
    if args.command == "probe":
        if args.backend != "sentry":
            parser.error("probe requires --backend sentry")
        try:
            print(json.dumps({"event_id": telemetry.probe(), "readback_verified": False}))
        except (ValueError, httpx.HTTPError):
            parser.exit(1, "Sentry connectivity probe failed; no delivery verified.\n")
        return
    if args.command == "doctor":
        problems = backend.check_live()
        print(json.dumps({"backend": args.backend, "problems": problems}))
        raise SystemExit(bool(problems))
    if args.backend == "sentry":
        parser.error("Sentry supports only probe and doctor")
    if args.command == "answer":
        if args.backend != "snowflake" or not all((args.task_id, args.question, args.model)):
            parser.error("answer requires Snowflake, --task-id, --question and --model")
        connection = None
        try:
            connection = snowflake.Snowflake()
            print(
                json.dumps(
                    connection.answer(
                        task_id=args.task_id, question=args.question, model=args.model
                    )
                )
            )
        except (ValueError, httpx.HTTPError) as exc:
            parser.exit(
                1,
                f"Cortex answer unavailable ({type(exc).__name__}); no grounded answer verified.\n",
            )
        finally:
            if connection:
                connection.close()
        return
    if args.records is None:
        parser.error("ingest requires --records pointing to actual RunResult JSONL")
    connection = None
    try:
        records = read_records(args.records)
        if args.backend == "tiger":
            with tiger.connect() as connection:
                count = tiger.ingest(connection, records)
        else:
            connection = snowflake.Snowflake()
            count = connection.ingest(records)
        print(json.dumps({"backend": args.backend, "new_runs": count, "input_runs": len(records)}))
    except (OSError, ValueError, psycopg.Error, httpx.HTTPError) as exc:
        # Driver exceptions may contain connection strings, bound data or auth details.
        parser.exit(
            1,
            f"Ingestion failed ({type(exc).__name__}); inspect private configuration and backend state.\n",
        )
    finally:
        if connection:
            connection.close()


if __name__ == "__main__":
    main()
