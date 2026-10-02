"""Per-file replacement for application state, without truncating the old save."""

from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Iterator, TextIO
import os

from .logging import get_logger


logger = get_logger("utils.persistence")


@contextmanager
def atomic_text_writer(path: Path) -> Iterator[TextIO]:
    """Stage UTF-8 text beside its destination, then replace after a clean close.

    Failures before replacement leave the previous file intact. This is not a
    concurrent-writer lock, a multi-file transaction, or power-loss durability.
    Existing symlinks are followed, matching ordinary ``open(path, 'w')``.
    """
    destination = Path(path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination.parent,
            prefix=f".{destination.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            yield stream
        # Close (including its flush) before replacement, also required on Windows.
        os.replace(temporary_path, destination)
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                # Do not mask the original serialization/replacement error.
                logger.warning("Unable to remove temporary state file %s", temporary_path, exc_info=True)
