"""Local runner for the Publish Agent — not part of the Lambda deployment."""

import argparse
import json
import logging
import os
import uuid

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


def main():
    parser = argparse.ArgumentParser(description="Run Publish Agent locally")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--url", help="Run full pipeline: research → execute → publish")
    group.add_argument("--execute-file", help="Path to saved execute result JSON")
    parser.add_argument("--research-file", help="Path to saved research result JSON (required with --execute-file)")
    args = parser.parse_args()

    task_id = f"local-{uuid.uuid4().hex[:8]}"

    if args.url:
        from src.agents.research.agent import run_research
        from src.agents.execute.agent import run_execute

        print(f"=== Research Agent  task_id={task_id} ===")
        print(f"URL: {args.url}\n")
        research_result = run_research(task_id, args.url)
        print("\n=== Research Result ===")
        print(json.dumps(research_result, indent=2, ensure_ascii=False))

        if research_result.get("verdict") != "go":
            print("\nVerdict is not 'go' — skipping execute and publish.")
            return

        print(f"\n=== Execute Agent  task_id={task_id} ===")
        execute_result = run_execute(task_id, research_result)
        print("\n=== Execute Result ===")
        print(json.dumps(execute_result, indent=2, ensure_ascii=False))
    else:
        if not args.research_file:
            print("--research-file is required when using --execute-file")
            raise SystemExit(1)
        with open(args.research_file) as f:
            research_result = json.load(f)
        with open(args.execute_file) as f:
            execute_result = json.load(f)
        print(f"=== Loaded research from {args.research_file} ===")
        print(f"=== Loaded execute from {args.execute_file} ===")
        print(f"task_id={task_id}")

    from src.agents.publish.agent import run_publish

    print(f"\n=== Publish Agent  task_id={task_id} ===")
    publish_result = run_publish(task_id, research_result, execute_result)
    print("\n=== Publish Result ===")
    print(json.dumps(publish_result, indent=2, ensure_ascii=False))

    # Save output
    os.makedirs("output", exist_ok=True)
    out_path = f"output/{task_id}-publish.json"
    with open(out_path, "w") as f:
        json.dump(publish_result, f, indent=2, ensure_ascii=False)
    print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    main()
