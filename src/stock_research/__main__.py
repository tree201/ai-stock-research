"""Command-line demo for the first vertical slice."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
from uuid import uuid4

from .documents import RawDocument
from .domain import ResearchProject
from .pipeline import ResearchPipeline
from .llm import DeepSeekProvider, OpenAICompatibleProvider, provider_from_env
from .storage import SQLiteStore
from .workflow import ResearchWorkflow
from .web import serve


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a local stock research demo")
    parser.add_argument("--demo", action="store_true", help="run the bundled Tencent sample")
    parser.add_argument("--web", action="store_true", help="start the local web MVP")
    parser.add_argument("--worker", action="store_true", help="start an RQ worker for background research jobs")
    parser.add_argument("--queue", default=os.getenv("AI_STOCK_RQ_QUEUE", "research"), help="RQ queue name")
    parser.add_argument("--redis-url", default=os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"), help="Redis connection URL")
    parser.add_argument("--port", type=int, default=8000, help="web port")
    parser.add_argument("--document", type=Path, help="path to a UTF-8 text document")
    parser.add_argument("--symbol", default="00700", help="HK symbol")
    parser.add_argument("--name", default="腾讯（示例）")
    parser.add_argument("--as-of-date", default="2025-12-31")
    parser.add_argument("--json", action="store_true", dest="as_json", help="print the structured report as JSON")
    parser.add_argument("--db", help="optional SQLite path for persistent projects, runs, events and web history")
    parser.add_argument("--llm", action="store_true", help="use the configured DeepSeek/OpenAI-compatible provider")
    parser.add_argument("--llm-base-url", help="override LLM base URL")
    parser.add_argument("--llm-model", default="deepseek-chat", help="LLM model")
    args = parser.parse_args()
    if args.worker:
        try:
            import redis
            from rq import Queue, Worker
        except ImportError as exc:
            parser.error("--worker requires optional dependencies: pip install 'stock-research-agent[worker]'")
        if args.db:
            os.environ["AI_STOCK_DB"] = args.db
        from .jobs import recover_stuck_jobs
        try:
            summary = recover_stuck_jobs(os.environ.get("AI_STOCK_DB", "./research.sqlite3"))
            print(f"任务恢复完成：续跑 {summary.get('recovered', 0)} 个，放弃 {summary.get('failed', 0)} 个")
        except Exception as exc:
            print(f"任务恢复失败：{exc}")
        connection = redis.from_url(args.redis_url)
        queue = Queue(args.queue, connection=connection)
        print(f"AI Stock Research worker listening on '{args.queue}' ({args.redis_url})")
        Worker([queue], connection=connection).work()
        return 0
    if args.web:
        if args.db:
            os.environ["AI_STOCK_DB"] = args.db
        serve(port=args.port)
        return 0
    if not args.demo and not args.document:
        parser.error("use --demo, --document or --web")
    if not args.llm:
        parser.error("a real model is required; configure DEEPSEEK_API_KEY (or an OpenAI-compatible provider) and pass --llm")

    path = args.document or Path(__file__).parents[2] / "examples" / "tencent_sample_report.txt"
    content = path.read_text(encoding="utf-8")
    as_of_date = date.fromisoformat(args.as_of_date)
    project = ResearchProject(user_id=uuid4(), company_id=uuid4(), symbol=args.symbol, name=args.name)
    store = SQLiteStore(args.db) if args.db else None
    llm_provider = None
    if args.llm:
        if args.llm_base_url:
            key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("AI_STOCK_LLM_API_KEY")
            if not key:
                parser.error("--llm requires DEEPSEEK_API_KEY or AI_STOCK_LLM_API_KEY")
            llm_provider = OpenAICompatibleProvider(args.llm_base_url, key, args.llm_model)
        else:
            llm_provider = provider_from_env()
            if llm_provider is None:
                parser.error("--llm requires DEEPSEEK_API_KEY or AI_STOCK_LLM_API_KEY")
    pipeline = ResearchPipeline(workflow=ResearchWorkflow(store=store), llm_provider=llm_provider)
    pipeline.workflow.create_project(project)
    document = RawDocument(
        company_id=project.company_id,
        source_type="local_fixture" if args.demo else "local_file",
        source_url=str(path),
        title=path.name,
        content=content,
        published_at=datetime(as_of_date.year, 12, 31, tzinfo=timezone.utc),
    )
    try:
        report = pipeline.run(
            project_id=project.id,
            question="截至截止日期，研究公司是否适合长期持有，重点关注增长、现金流、估值和风险。",
            as_of_date=as_of_date,
            documents=[document],
            dcf_assumptions={
                "revenue_prior": 609_000_000_000,
                "revenue_years": 1,
                "base_fcf": 150_000_000_000,
                "growth_rates": [0.15, 0.12, 0.10, 0.08, 0.06],
                "discount_rate": 0.09,
                "terminal_growth": 0.03,
                "net_cash": 100_000_000_000,
                "shares": 9_000_000_000,
            },
        )
        if args.as_json:
            print(json.dumps({key: value for key, value in report.items() if key != "markdown"}, ensure_ascii=False, indent=2, default=str))
        else:
            print(report["markdown"])
    finally:
        if store:
            store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
