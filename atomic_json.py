"""Atomic JSON persistence shared by the local review and HTTP threads.

Locks coordinate only this process. External Windows readers can still deny a
replace briefly, so only the final disk commit receives bounded retries. Failed
commits leave the old target and a uniquely named recovery file intact.
"""
from contextlib import contextmanager
import errno
import json
import os
from pathlib import Path
import tempfile
from threading import Lock, RLock
import time


_registry_guard = Lock()
_path_locks = {}
_COMMIT_RETRY_DELAYS = (0.025, 0.05, 0.1, 0.2, 0.4)


@contextmanager
def path_lock(path):
    """Serialize users of one resolved path, without blocking other paths."""
    key = os.path.normcase(str(Path(path).resolve()))
    with _registry_guard:
        entry = _path_locks.setdefault(key, [RLock(), 0])
        entry[1] += 1  # Include waiting users, so their lock cannot be replaced.
    try:
        with entry[0]:
            yield
    finally:
        with _registry_guard:
            entry[1] -= 1
            if entry[1] == 0:
                del _path_locks[key]


class AtomicJSONWriteError(OSError):
    """A disk failure with the target and retained recovery path available."""
    def __init__(self, target, temporary, error, *, phase, commit_attempts):
        self.target_path = Path(target)
        self.temporary_path = Path(temporary)
        self.phase = phase
        self.commit_attempts = commit_attempts
        detail = (f'JSON 原子保存失败（{phase}，磁盘提交 {commit_attempts} 次）；'
                  f'原目标未被截断，临时文件保留于 {temporary}；原因：{error}')
        super().__init__(error.errno, detail, str(target))
        if getattr(error, 'winerror', None) is not None:
            self.winerror = error.winerror


def _retryable_commit_error(error):
    code = getattr(error, 'winerror', None)
    if code is not None:
        return code in {5, 32, 33}  # Access denied / sharing / lock violation.
    return os.name == 'nt' and error.errno in {errno.EACCES, errno.EPERM}


def write_json(path, data):
    """Write a complete sibling file, then replace; never delete the target."""
    path = Path(path).resolve()
    with path_lock(path):
        serialized = json.dumps(data, ensure_ascii=False, indent=2)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n',
                    prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent,
                    delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as error:
            if temporary is None:
                raise
            raise AtomicJSONWriteError(path, temporary, error,
                                       phase='write', commit_attempts=0) from error
        for index in range(len(_COMMIT_RETRY_DELAYS) + 1):
            try:
                os.replace(temporary, path)
                return
            except OSError as error:
                if (not _retryable_commit_error(error)
                        or index == len(_COMMIT_RETRY_DELAYS)):
                    raise AtomicJSONWriteError(path, temporary, error,
                        phase='replace', commit_attempts=index + 1) from error
                time.sleep(_COMMIT_RETRY_DELAYS[index])


def read_json(path, *, encoding='utf-8'):
    """Close the reader before a writer can attempt its Windows replace."""
    with path_lock(path):
        with Path(path).open('r', encoding=encoding) as stream:
            return json.load(stream)


atomic_write_json = write_json
