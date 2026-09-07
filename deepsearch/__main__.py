"""允许使用 ``python -m deepsearch`` 启动命令行界面。"""

from .presentation.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
