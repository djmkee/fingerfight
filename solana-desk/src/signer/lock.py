"""One signer per desk database. Two would race each other for the same orders and wallet."""

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class SignerBusy(RuntimeError):
    """Another signer already serves this desk database."""


@contextmanager
def single_signer(db_path: Path) -> Iterator[None]:
    """Hold an exclusive lock next to the database until the signer stops. The OS frees it on exit."""
    path = db_path.with_name(db_path.name + ".signer.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        try:
            if sys.platform == "win32":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise SignerBusy(f"another signer is already running for {db_path}; stop it first") from None
        yield
