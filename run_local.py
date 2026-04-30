"""Local runner for the Research Agent — not part of the Lambda deployment."""

import json
import logging
import sys
import uuid

from src.agents.research.agent import run_research

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else None
    if not url:
        print("Usage: python run_local.py <url>")
        sys.exit(1)

    task_id = f"local-{uuid.uuid4().hex[:8]}"
    print(f"=== Research Agent  task_id={task_id} ===")
    print(f"URL: {url}\n")

    result = run_research(task_id, url)
    print("\n=== Research Result ===")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result


if __name__ == "__main__":
    main()
