"""booktran：书籍翻译工具。"""
try:  # 单一来源：包元数据（避免两处各写一遍导致漂移）
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _v
    try:
        __version__ = _v("booktran")
    except PackageNotFoundError:
        __version__ = "0.9.0"
except ImportError:  # pragma: no cover
    __version__ = "0.9.0"

__all__ = ["__version__"]
