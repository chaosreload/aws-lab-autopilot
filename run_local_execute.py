"""Local runner for the Execute Agent — not part of the Lambda deployment."""

import argparse
import json
import logging
import os
import uuid

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


def main():
    parser = argparse.ArgumentParser(description="Run Execute Agent locally")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--url", help="AWS What's New URL — runs research first, then execute")
    group.add_argument("--research-file", help="Path to saved research result JSON — skip research")
    args = parser.parse_args()

    task_id = f"local-{uuid.uuid4().hex[:8]}"

    if args.url:
        from src.agents.research.agent import run_research

        print(f"=== Research Agent  task_id={task_id} ===")
        print(f"URL: {args.url}\n")
        research_result = run_research(task_id, args.url)
        print("\n=== Research Result ===")
        print(json.dumps(research_result, indent=2, ensure_ascii=False))

        # Save research result for inspection
        os.makedirs("output", exist_ok=True)
        research_path = f"output/{task_id}-research.json"
        with open(research_path, "w") as f:
            json.dump(research_result, f, indent=2, ensure_ascii=False)
        print(f"Saved research to {research_path}")

        if research_result.get("verdict") != "go":
            print("\nVerdict is not 'go' — skipping execute.")
            return
    else:
        with open(args.research_file) as f:
            research_result = json.load(f)
        print(f"=== Loaded research from {args.research_file} ===")
        print(f"task_id={task_id}")

    from src.agents.execute.agent import run_execute

    print(f"\n=== Execute Agent  task_id={task_id} ===")
    execute_result = run_execute(task_id, research_result)
    print("\n=== Execute Result ===")
    print(json.dumps(execute_result, indent=2, ensure_ascii=False))

    # Save to local file
    os.makedirs("output", exist_ok=True)
    out_path = f"output/{task_id}-execute.json"
    with open(out_path, "w") as f:
        json.dump(execute_result, f, indent=2, ensure_ascii=False)
    print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    main()
