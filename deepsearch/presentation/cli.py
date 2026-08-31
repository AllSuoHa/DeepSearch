from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import subprocess
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ..bootstrap import DeepSearchAgent, apply_profile
from ..domain.models import ReportSpecification, ResearchBrief
from ..infrastructure.config import load_settings
from ..infrastructure.customer_service import CustomerServicePublisher
from ..infrastructure.storage import FileReportStorage
from ..scheduling.topics import ResearchTaskManager, ResearchTaskScheduler, ScheduledResearchTask

# cli.py 位于 deepsearch/presentation/；仓库级入口和资源位于其上两级。
# 集中定义根目录可避免 Web 子命令错误地寻找 deepsearch/streamlit_app.py。
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="deepsearch", description="迭代式 AI 深度搜索引擎")
    parser.add_argument("--config", default=None, help="配置文件路径（默认 ./config.json）")
    parser.add_argument("--mock", action="store_true", help="完全离线的确定性演示模式")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ask = subparsers.add_parser("ask", help="执行一次研究")
    ask.add_argument("question", help="要研究的问题")
    ask.add_argument("--no-stream", action="store_true", help="不在终端显示完整报告")

    subparsers.add_parser("interactive", help="进入支持追问的交互模式")
    history = subparsers.add_parser("history", help="列出历史报告")
    history.add_argument("--query", default="", help="按主题或正文搜索")

    # tasks 是 2.1 的正式入口；topics 保留为兼容别名。
    for command_name, command_help in (("tasks", "管理自主研究任务"), ("topics", "兼容旧版主题命令")):
        tasks = subparsers.add_parser(command_name, help=command_help)
        task_sub = tasks.add_subparsers(dest="task_command", required=True)
        task_sub.add_parser("list", help="列出任务")
        add = task_sub.add_parser("add", help="添加或更新研究任务")
        add.add_argument("name")
        add.add_argument("question")
        add.add_argument("--time", default="10:00", help="HH:MM")
        add.add_argument("--schedule-type", choices=["daily", "weekly", "once"], default="daily")
        add.add_argument("--weekdays", default="0", help="每周执行日，0=周一，逗号分隔")
        add.add_argument("--date", default="", help="单次任务日期 YYYY-MM-DD")
        add.add_argument("--domain", default="通用")
        add.add_argument("--types", default="知识,新闻,公告", help="信息类型，逗号分隔")
        add.add_argument("--time-scope", default="不限")
        add.add_argument("--format", choices=["markdown", "text", "json"], default="markdown")
        add.add_argument("--words", type=int, default=1200)
        add.add_argument("--audience", default="通用读者")
        add.add_argument("--sections", default="摘要,关键结论,详细分析,建议与下一步,证据局限与争议")
        add.add_argument("--instructions", default="")
        add.add_argument("--profile", choices=["快速", "均衡", "深度"], default="深度")
        add.add_argument(
            "--deliver-to-customer-service",
            action="store_true",
            help="研究完成后自动提交到 CustomerService 知识库",
        )
        add.add_argument(
            "--classification",
            choices=["public", "internal", "confidential"],
            default="internal",
            help="提交文档的数据密级",
        )
        for action, action_help in (("remove", "删除任务"), ("run", "立即运行任务"), ("enable", "启用任务"), ("disable", "停用任务")):
            action_parser = task_sub.add_parser(action, help=action_help)
            action_parser.add_argument("name")

    schedule = subparsers.add_parser("schedule", help="运行轻量定时器")
    schedule.add_argument("--once", action="store_true", help="只检查一次到期任务")
    schedule.add_argument("--poll", type=int, default=30, help="轮询秒数")
    delivery = subparsers.add_parser("delivery", help="查看或重试 CustomerService 投递队列")
    delivery_sub = delivery.add_subparsers(dest="delivery_command", required=True)
    delivery_sub.add_parser("list", help="列出待投递报告")
    retry_delivery = delivery_sub.add_parser("retry", help="立即重试到期报告")
    retry_delivery.add_argument("--force", action="store_true", help="也重试 4xx 等不可自动重试记录")
    web = subparsers.add_parser("web", help="启动 Streamlit 研究工作台")
    web.add_argument("--port", type=int, default=8501, help="Web 服务端口")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings(args.config, mode="mock" if args.mock else None)
    _configure_logging(settings.config_path.parent, settings.log_level)
    if args.command == "web":
        return _run_web(args.port)
    agent = DeepSearchAgent(settings)
    try:
        if args.command == "ask":
            result = agent.research(args.question, _progress)
            if not args.no_stream:
                _stream_markdown(result.report)
            return 0 if result.validation.valid else 2
        if args.command == "interactive":
            return _interactive(agent)
        if args.command == "history":
            paths = FileReportStorage(settings.reports_dir).list(args.query)
            print("\n".join(str(path) for path in paths) if paths else "没有找到历史报告。")
            return 0
        if args.command in {"topics", "tasks"}:
            return _tasks(args, settings)
        if args.command == "schedule":
            scheduler = ResearchTaskScheduler(
                settings,
                agent_factory=lambda profile: DeepSearchAgent(apply_profile(settings, profile)),
            )
            if args.once:
                completed = scheduler.run_due(progress=_progress)
                print("已运行：" + "、".join(completed) if completed else "当前没有到期研究任务。")
            else:
                print(f"定时器已启动，每 {max(5, args.poll)} 秒检查一次。按 Ctrl+C 停止。", flush=True)
                scheduler.serve(args.poll, _progress)
            return 0
        if args.command == "delivery":
            return _delivery(args, settings)
    except KeyboardInterrupt:
        print("\n已停止。")
        return 130
    except (ValueError, OSError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    return 0


def _run_web(port: int) -> int:
    if importlib.util.find_spec("streamlit") is None:
        print('未安装 Streamlit。请先运行：python -m pip install -e ".[ui]"', file=sys.stderr)
        return 2
    command = [
        sys.executable, "-m", "streamlit", "run", str(PROJECT_ROOT / "streamlit_app.py"),
        f"--server.port={max(1, min(65535, port))}",
    ]
    return subprocess.run(command, cwd=PROJECT_ROOT, check=False).returncode


def _interactive(agent: DeepSearchAgent) -> int:
    print("DeepSearch 交互模式。输入问题开始；输入 /quit 退出。", flush=True)
    previous = None
    while True:
        try:
            question = input("\n研究问题> ").strip()
        except EOFError:
            return 0
        if question.lower() in {"/quit", "/exit", "quit", "exit"}:
            return 0
        if not question:
            continue
        previous = agent.research(question, _progress) if previous is None else agent.follow_up(previous, question, _progress)
        _stream_markdown(previous.report)


def _tasks(args, settings) -> int:
    """管理带周期、领域、来源类型和报告规格的自主研究任务。"""

    manager = ResearchTaskManager(settings)
    if args.task_command == "list":
        tasks = manager.list()
        if not tasks:
            print("尚未配置自主研究任务。")
        for task in tasks:
            report = task.brief.report
            print(
                f"{task.name} | {task.schedule_type} {task.run_time} | {task.profile} | "
                f"{task.brief.domain} | {report.output_format}/{report.target_words}字 | "
                f"{'CustomerService/' + task.data_classification if task.deliver_to_customer_service else '本地'} | "
                f"{'启用' if task.enabled else '停用'}"
            )
    elif args.task_command == "add":
        report = ReportSpecification(
            output_format=args.format,
            target_words=args.words,
            audience=args.audience,
            sections=tuple(item.strip() for item in args.sections.split(",") if item.strip()),
            custom_instructions=args.instructions,
        )
        brief = ResearchBrief(
            domain=args.domain,
            information_types=tuple(item.strip() for item in args.types.split(",") if item.strip()),
            time_scope=args.time_scope,
            report=report,
        )
        task = ScheduledResearchTask(
            name=args.name,
            question=args.question,
            schedule_type=args.schedule_type,
            run_time=args.time,
            weekdays=tuple(int(item.strip()) for item in args.weekdays.split(",") if item.strip()),
            run_date=args.date,
            profile=args.profile,
            deliver_to_customer_service=args.deliver_to_customer_service,
            data_classification=args.classification,
            brief=brief,
        )
        manager.add_task(task)
        print(f"自主研究任务“{args.name}”已保存。")
    elif args.task_command == "remove":
        print(f"任务“{args.name}”已删除。" if manager.remove(args.name) else "未找到该任务。")
    elif args.task_command in {"enable", "disable"}:
        enabled = args.task_command == "enable"
        print(f"任务“{args.name}”已{'启用' if enabled else '停用'}。" if manager.set_enabled(args.name, enabled) else "未找到该任务。")
    elif args.task_command == "run":
        scheduler = ResearchTaskScheduler(
            settings,
            agent_factory=lambda profile: DeepSearchAgent(apply_profile(settings, profile)),
        )
        result = scheduler.run_now(args.name, _progress)
        _stream_markdown(result.report)
    return 0


def _progress(phase: str, message: str) -> None:
    labels = {"plan": "PLAN", "search": "SEARCH", "fetch": "FETCH", "evaluate": "CHECK", "adjust": "RETRY", "verify": "VERIFY", "generate": "WRITE", "validate": "VALIDATE", "score": "SCORE", "saved": "SAVED", "deliver": "DELIVER", "follow_up": "FOLLOW-UP"}
    print(f"[{labels.get(phase, 'INFO')}] {message}", flush=True)


def _delivery(args, settings) -> int:
    """运维 CustomerService outbox，不读取或显示报告正文与凭据。"""

    publisher = CustomerServicePublisher(settings.customer_service)
    if args.delivery_command == "list":
        paths = sorted(settings.customer_service.outbox_dir.glob("*.json"))
        if not paths:
            print("CustomerService outbox 为空。")
            return 0
        for path in paths:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                print(f"损坏记录 | {path.name} | {type(exc).__name__}")
                continue
            print(
                f"{record.get('external_id', path.stem)} | attempts={record.get('attempts', 0)} | "
                f"retryable={record.get('retryable', True)} | next={record.get('next_retry_at', '-')} | "
                f"report={record.get('report_path', '-')} | error={record.get('last_error', '-')}"
            )
        return 0
    if not settings.customer_service.enabled:
        print("错误：请先在 customer_service.enabled 开启联动，再执行重试。", file=sys.stderr)
        return 2
    outcomes = publisher.flush_pending(force=args.force)
    indexed = sum(item.status == "indexed" for item in outcomes)
    queued = sum(item.status == "queued" for item in outcomes)
    print(f"投递完成：indexed={indexed}，仍在队列={queued}")
    return 0 if queued == 0 else 2


def _stream_markdown(report: str) -> None:
    print("\n" + "=" * 72)
    for paragraph in report.splitlines(keepends=True):
        print(paragraph, end="", flush=True)
    print("=" * 72)


def _configure_logging(base: Path, level: str) -> None:
    log_dir = base / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    if root.handlers:
        return
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(log_dir / "deepsearch.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)


if __name__ == "__main__":
    raise SystemExit(main())
