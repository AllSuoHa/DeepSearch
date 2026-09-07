"""可持久化的个人自动任务及轻量本地调度器。"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Callable

from ..bootstrap import DeepSearchAgent, apply_profile
from ..domain.models import ReportSpecification, ResearchBrief, ResearchResult
from ..infrastructure.config import Settings, save_settings
from ..infrastructure.customer_service import CustomerServicePublisher

logger = logging.getLogger(__name__)

SCHEDULE_TYPES = {"daily", "weekly", "once"}
OUTPUT_FORMATS = {"markdown", "text", "json"}
PROFILES = {"快速", "均衡", "深度"}
DATA_CLASSIFICATIONS = {"public", "internal", "confidential"}


@dataclass(frozen=True, slots=True)
class ScheduledResearchTask:
    """一项自动研究任务：何时运行、研究什么，以及如何交付。"""

    name: str
    question: str
    schedule_type: str = "daily"
    run_time: str = "10:00"
    weekdays: tuple[int, ...] = (0,)
    run_date: str = ""
    enabled: bool = True
    profile: str = "深度"
    deliver_to_customer_service: bool = False
    data_classification: str = "internal"
    brief: ResearchBrief = ResearchBrief()

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.question.strip():
            raise ValueError("任务名称和研究问题不能为空")
        _validate_time(self.run_time)
        if self.schedule_type not in SCHEDULE_TYPES:
            raise ValueError("周期必须是 daily、weekly 或 once")
        if self.schedule_type == "weekly" and (not self.weekdays or any(day not in range(7) for day in self.weekdays)):
            raise ValueError("每周任务必须选择 0–6 范围内的星期")
        if self.schedule_type == "once":
            _validate_date(self.run_date)
        if self.profile not in PROFILES:
            raise ValueError("研究强度必须是快速、均衡或深度")
        if self.data_classification not in DATA_CLASSIFICATIONS:
            raise ValueError("文档密级必须是 public、internal 或 confidential")
        if self.brief.report.output_format not in OUTPUT_FORMATS:
            raise ValueError("报告格式必须是 markdown、text 或 json")
        if not 300 <= self.brief.report.target_words <= 5000:
            raise ValueError("目标篇幅必须在 300–5000 字之间")

    @classmethod
    def from_dict(cls, data: dict) -> "ScheduledResearchTask":
        """读取 2.1 任务；旧版 name/query/time 会自动迁移为每日任务。"""

        brief_data = data.get("brief", {})
        report_data = brief_data.get("report", {})
        report = ReportSpecification(
            output_format=str(report_data.get("output_format", data.get("output_format", "markdown"))),
            target_words=int(report_data.get("target_words", data.get("target_words", 1200))),
            audience=str(report_data.get("audience", data.get("audience", "通用读者"))),
            language=str(report_data.get("language", "中文")),
            sections=tuple(report_data.get("sections", data.get("sections", ReportSpecification().sections))),
            custom_instructions=str(report_data.get("custom_instructions", data.get("custom_instructions", ""))),
        )
        brief = ResearchBrief(
            domain=str(brief_data.get("domain", data.get("domain", "通用"))),
            objective=str(brief_data.get("objective", data.get("objective", "形成可验证、可执行的总结报告"))),
            information_types=tuple(brief_data.get("information_types", data.get("information_types", ("知识", "新闻", "公告")))),
            time_scope=str(brief_data.get("time_scope", data.get("time_scope", "不限"))),
            report=report,
        )
        return cls(
            name=str(data.get("name", "")),
            question=str(data.get("question", data.get("query", ""))),
            schedule_type=str(data.get("schedule_type", "daily")),
            run_time=str(data.get("run_time", data.get("time", "10:00"))),
            weekdays=tuple(int(day) for day in data.get("weekdays", (0,))),
            run_date=str(data.get("run_date", "")),
            enabled=bool(data.get("enabled", True)),
            profile=str(data.get("profile", "深度")),
            deliver_to_customer_service=bool(data.get("deliver_to_customer_service", False)),
            data_classification=str(data.get("data_classification", "internal")),
            brief=brief,
        )

    def to_dict(self) -> dict:
        """序列化为不含凭据、可写入普通配置文件的字典。"""

        return {
            "schema_version": 2,
            "name": self.name,
            "question": self.question,
            "schedule_type": self.schedule_type,
            "run_time": self.run_time,
            "weekdays": list(self.weekdays),
            "run_date": self.run_date,
            "enabled": self.enabled,
            "profile": self.profile,
            "deliver_to_customer_service": self.deliver_to_customer_service,
            "data_classification": self.data_classification,
            "brief": {
                "domain": self.brief.domain,
                "objective": self.brief.objective,
                "information_types": list(self.brief.information_types),
                "time_scope": self.brief.time_scope,
                "report": {
                    "output_format": self.brief.report.output_format,
                    "target_words": self.brief.report.target_words,
                    "audience": self.brief.report.audience,
                    "language": self.brief.report.language,
                    "sections": list(self.brief.report.sections),
                    "custom_instructions": self.brief.report.custom_instructions,
                },
            },
        }

    def occurrence_key(self, current: datetime) -> str:
        """返回本次计划实例的幂等键。"""

        if self.schedule_type == "once":
            return self.run_date
        return current.strftime("%Y-%m-%d")

    def is_due(self, current: datetime, last_occurrence: str = "") -> bool:
        """判断任务在当前时间是否到期且尚未成功执行。"""

        if not self.enabled or current.strftime("%H:%M") < self.run_time:
            return False
        if self.schedule_type == "weekly" and current.weekday() not in self.weekdays:
            return False
        if self.schedule_type == "once" and current.strftime("%Y-%m-%d") < self.run_date:
            return False
        return last_occurrence != self.occurrence_key(current)


class ResearchTaskManager:
    """使用配置文件管理任务，不引入数据库。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def list(self) -> list[ScheduledResearchTask]:
        """读取所有有效任务；单条损坏配置不会阻断其余任务。"""

        tasks = []
        for raw in self.settings.topics:
            try:
                tasks.append(ScheduledResearchTask.from_dict(raw))
            except (TypeError, ValueError) as exc:
                logger.warning("忽略无效研究任务 data=%s error=%s", raw, exc)
        return tasks

    def add_task(self, task: ScheduledResearchTask) -> None:
        """按名称新增或替换任务并立即持久化。"""

        tasks = [item for item in self.list() if item.name != task.name]
        tasks.append(task)
        self._save(tasks)

    def add(self, name: str, query: str, run_time: str = "10:00", **kwargs) -> None:
        """兼容旧主题 API，并允许调用方逐步传入 2.1 字段。"""

        task = ScheduledResearchTask(name=name, question=query, run_time=run_time, **kwargs)
        self.add_task(task)

    def remove(self, name: str, save: bool = True) -> bool:
        """删除指定任务；返回任务是否存在。"""

        tasks = self.list()
        remaining = [task for task in tasks if task.name != name]
        changed = len(remaining) != len(tasks)
        if save and changed:
            self._save(remaining)
        return changed

    def get(self, name: str) -> ScheduledResearchTask | None:
        """按名称查找任务。"""

        return next((task for task in self.list() if task.name == name), None)

    def set_enabled(self, name: str, enabled: bool) -> bool:
        """启用或停用任务，并保持其他字段不变。"""

        tasks = self.list()
        changed = False
        updated = []
        for task in tasks:
            if task.name == name:
                updated.append(replace(task, enabled=enabled))
                changed = True
            else:
                updated.append(task)
        if changed:
            self._save(updated)
        return changed

    def _save(self, tasks: list[ScheduledResearchTask]) -> None:
        self.settings.topics[:] = [task.to_dict() for task in tasks]
        save_settings(self.settings)


class ResearchTaskScheduler:
    """执行每日、每周和单次任务；每个计划实例最多成功执行一次。"""

    def __init__(
        self,
        settings: Settings,
        agent: DeepSearchAgent | None = None,
        state_path: Path | None = None,
        agent_factory: Callable[[str], DeepSearchAgent] | None = None,
        publisher: CustomerServicePublisher | None = None,
    ) -> None:
        self.settings = settings
        self.agent = agent
        self.agent_factory = agent_factory
        self.publisher = publisher if publisher is not None else CustomerServicePublisher(settings.customer_service)
        self.state_path = state_path or settings.config_path.parent / ".deepsearch-schedule-state.json"

    def run_due(self, now: datetime | None = None, progress=None) -> list[str]:
        """执行所有到期任务，并只记录真正成功的计划实例。"""

        self._flush_pending()
        current = now or datetime.now().astimezone()
        state = self._load_state()
        completed = []
        for task in ResearchTaskManager(self.settings).list():
            if not task.is_due(current, str(state.get(task.name, ""))):
                continue
            try:
                self._run_task(task, progress)
            except Exception as exc:
                # 一个任务失败不阻止同一批次中的其他自动任务。
                logger.exception("自动任务失败 name=%s error=%s", task.name, exc)
                continue
            state[task.name] = task.occurrence_key(current)
            completed.append(task.name)
        self._save_state(state)
        return completed

    def run_now(self, name: str, progress=None) -> ResearchResult:
        """忽略计划时间立即运行一个已保存任务。"""

        self._flush_pending()
        task = ResearchTaskManager(self.settings).get(name)
        if task is None:
            raise ValueError(f"未找到研究任务：{name}")
        return self._run_task(task, progress)

    def serve(self, poll_seconds: int = 30, progress=None) -> None:
        """以前台循环运行轻量调度器，进程退出后即停止。"""

        while True:
            self.run_due(progress=progress)
            time.sleep(max(5, poll_seconds))

    def _run_task(self, task: ScheduledResearchTask, progress=None) -> ResearchResult:
        runner = (
            self.agent_factory(task.profile)
            if self.agent_factory is not None
            else self.agent or DeepSearchAgent(apply_profile(self.settings, task.profile))
        )
        result = runner.research(task.question, progress, task.brief)
        if task.deliver_to_customer_service:
            try:
                outcome = self.publisher.publish(task, result)
            except Exception as exc:
                # 投递器自身故障也不能让调度器误以为研究失败并重新执行昂贵搜索。
                logger.exception("CustomerService 报告投递异常 task=%s error=%s", task.name, exc)
                if progress is not None:
                    progress("deliver", "报告已生成，但知识库投递器异常，请检查日志和 outbox")
            else:
                if progress is not None:
                    if outcome.status == "indexed":
                        progress(
                            "deliver",
                            f"报告已自动写入 CustomerService 知识库（文档 {outcome.document_id}）",
                        )
                    else:
                        progress(
                            "deliver",
                            f"CustomerService 未完成入库，报告已进入安全重试队列：{outcome.error}",
                        )
        return result

    def _flush_pending(self) -> None:
        try:
            outcomes = self.publisher.flush_pending()
        except Exception as exc:
            # outbox 故障不能阻断新研究；记录后等待下一轮人工检查。
            logger.exception("CustomerService outbox 重试失败 error=%s", exc)
            return
        delivered = sum(outcome.status == "indexed" for outcome in outcomes)
        if delivered:
            logger.info("CustomerService outbox 已补投 %s 份报告", delivered)

    def _load_state(self) -> dict[str, str]:
        if not self.state_path.exists():
            return {}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_state(self, state: dict[str, str]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.state_path)


# 兼容既有导入；新代码优先使用 ResearchTask* 名称。
TopicManager = ResearchTaskManager
TopicScheduler = ResearchTaskScheduler


def _validate_time(value: str) -> None:
    try:
        datetime.strptime(value, "%H:%M")
    except ValueError as exc:
        raise ValueError("时间必须使用 HH:MM 格式，例如 10:00") from exc


def _validate_date(value: str) -> None:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("单次任务日期必须使用 YYYY-MM-DD 格式") from exc
