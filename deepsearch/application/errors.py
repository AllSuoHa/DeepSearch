"""可由界面和 CLI 转换成明确提示的应用层错误。"""


class DeepSearchError(RuntimeError):
    """所有可向 CLI 或 Web 安全展示的应用错误基类。"""


class SearchUnavailableError(DeepSearchError):
    """在线提供器没有返回任何可用真实结果。"""


class ResearchModelRequiredError(DeepSearchError):
    """研究流程需要模型，但当前运行环境没有可用凭据。"""


class ReportQualityError(DeepSearchError):
    """模型输出在一次定向修订后仍未通过确定性质量门。"""
