"""Run a command so that a Stop reaches it (DST-44).

A job is cancelled between steps, and each step used to run its ffmpeg to the end first: on the QA
film a Stop pressed during a shot or a look took about 21 s to land. `run` is `subprocess.run` for the
commands of a render. While a job is following it (`shared/progress_hook.py`) it watches the job's
cancel flag every fraction of a second, and when the job has been told to stop it kills the command,
removes the file it was writing if that file did not exist before, and lets the cancel travel up
through the work. With nobody following (a test, the command line) it is `subprocess.run`, unchanged.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any, Optional, Sequence

from shared import progress_hook

POLL_S = 0.2
# What a command's last argument looks like when it is the file it writes.
_OUTPUT_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg",
                    ".png", ".jpg", ".gif", ".srt", ".vtt", ".txt"}


def _output_of(cmd: Sequence[Any]) -> Optional[Path]:
    """The file a command writes, when that is plain: its last argument, a media path."""
    if not cmd or not isinstance(cmd[-1], (str, Path)):
        return None
    last = str(cmd[-1])
    if last.startswith("-") or Path(last).suffix.lower() not in _OUTPUT_SUFFIXES:
        return None
    return Path(last)


def _kill(proc: subprocess.Popen) -> None:
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.communicate(timeout=5)             # reap it and close its pipes
    except Exception:  # noqa: BLE001
        pass


def run(cmd: Sequence[Any], *, capture_output: bool = False, check: bool = False, text: bool = False,
        timeout: Optional[float] = None, input: Any = None,  # noqa: A002 — the name subprocess.run uses
        **kwargs: Any) -> subprocess.CompletedProcess:
    """`subprocess.run(cmd, ...)`, stopping the command at once if the job following it is cancelled."""
    if not progress_hook.following():
        return subprocess.run(cmd, capture_output=capture_output, check=check, text=text,
                              timeout=timeout, input=input, **kwargs)
    if capture_output:
        kwargs["stdout"] = kwargs["stderr"] = subprocess.PIPE
    if input is not None:
        kwargs["stdin"] = subprocess.PIPE
    out_file = _output_of(cmd)
    existed = bool(out_file and out_file.exists())
    deadline = None if timeout is None else time.monotonic() + timeout
    proc = subprocess.Popen(cmd, text=text, **kwargs)
    pending: Any = input
    while True:
        try:
            stdout, stderr = proc.communicate(input=pending, timeout=POLL_S)
            break
        except subprocess.TimeoutExpired:
            pending = None                       # already handed over; a second call must not send it again
            if progress_hook.cancel_requested():
                _kill(proc)
                if out_file is not None and not existed:
                    out_file.unlink(missing_ok=True)      # half a file nobody asked for
                progress_hook.raise_if_cancelled()
                raise RuntimeError("cancelled")           # the check did not raise: still stop
            if deadline is not None and time.monotonic() >= deadline:
                _kill(proc)
                raise subprocess.TimeoutExpired(cmd, timeout)
        except BaseException:
            _kill(proc)                          # a KeyboardInterrupt or anything else: never leave it running
            raise
    code = proc.returncode
    if check and code:
        raise subprocess.CalledProcessError(code, cmd, output=stdout, stderr=stderr)
    return subprocess.CompletedProcess(cmd, code, stdout, stderr)

