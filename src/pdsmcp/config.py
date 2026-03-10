"""Package-level configuration — cache directory management."""

from pathlib import Path

_cache_dir: Path | None = None


def configure(cache_dir: str | Path | None = None) -> None:
    """Configure the pdsmcp package.

    Call once at startup to set the cache root directory. All runtime data
    (metadata cache, data file cache, validation records) lives under this root.

    Args:
        cache_dir: Root directory for all caches. Defaults to ~/.pdsmcp/.
    """
    global _cache_dir
    if cache_dir is not None:
        _cache_dir = Path(cache_dir)
    else:
        _cache_dir = None


def get_cache_root() -> Path:
    """Return the cache root directory.

    Resolution order:
    1. Value set by configure(cache_dir=...)
    2. PDSMCP_CACHE_DIR environment variable
    3. Default: ~/.pdsmcp/
    """
    if _cache_dir is not None:
        return _cache_dir
    import os
    custom = os.environ.get("PDSMCP_CACHE_DIR")
    if custom:
        return Path(custom)
    return Path.home() / ".pdsmcp"
