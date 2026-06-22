"""FileWatcher 统一接口 — FileChangeEvent 数据类 + FileWatcher ABC."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Optional, Literal


@dataclass(frozen=True)
class FileChangeEvent:
    """文件变更事件（不可变值对象）."""
    path: str                              # 文件绝对路径
    change_type: Literal["created", "modified", "deleted", "moved"]
    src_path: Optional[str] = None         # 移动操作的源路径


class FileWatcher(ABC):
    """文件监听器抽象基类."""

    @abstractmethod
    def start(self, root_dir: str, callback: Callable[[FileChangeEvent], None]) -> None:
        """启动监听。

        Args:
            root_dir: 监听的根目录路径
            callback: 文件变更时的回调函数
        """
        ...

    @abstractmethod
    def stop(self) -> None:
        """停止监听，阻塞等待退出."""
        ...

    @abstractmethod
    def is_running(self) -> bool:
        """返回当前是否正在监听."""
        ...
