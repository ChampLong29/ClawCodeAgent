"""Collect verified Train/Dev Episode batches with the local Agent runtime."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from claw.data_pipeline import collect_local_training_episodes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-suite", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--generation-commit", required=True)
    parser.add_argument("--split", choices=("train", "dev"), default="train")
    parser.add_argument("--task-id", action="append", default=[])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--model")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int)
    parser.add_argument("--max-turns", type=int, default=50)
    parser.add_argument(
        "--prompt-version", default="agent-system-prompt.v1"
    )
    args = parser.parse_args()
    result = collect_local_training_episodes(
        manifest_path=args.task_suite,
        output_root=args.output_root,
        generation_commit=args.generation_commit,
        split=args.split,
        task_ids=args.task_id,
        limit=args.limit,
        model_ref=args.model,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        max_turns=args.max_turns,
        prompt_version=args.prompt_version,
    )
    print(
        json.dumps(
            {
                "collection_id": result.manifest.collection_id,
                "split": result.manifest.split,
                "task_count": len(result.manifest.task_ids),
                "success_count": sum(item.success for item in result.episodes),
                "manifest": str(result.manifest_path),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
