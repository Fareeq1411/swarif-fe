import functools
import os
import sys
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv


load_dotenv(Path(__file__).with_name(".env"))

LOG_PATH = Path(os.getenv("LOG_PATH", "log.txt"))
if not LOG_PATH.is_absolute():
    LOG_PATH = Path(__file__).parent / LOG_PATH

_LOG_LOCK = threading.RLock()
_HOOKS_INSTALLED = False
MAX_LOG_BYTES = 1024 * 1024


def _trim_log_file():
    """Discard the oldest bytes while keeping the log at or below 1 MB."""
    try:
        if LOG_PATH.stat().st_size <= MAX_LOG_BYTES:
            return
        data = LOG_PATH.read_bytes()
    except FileNotFoundError:
        return

    retained = data[-MAX_LOG_BYTES:]
    first_newline = retained.find(b"\n")
    if 0 <= first_newline < len(retained) - 1:
        retained = retained[first_newline + 1:]
    LOG_PATH.write_bytes(retained)


def log(message):
    """Append a timestamped message or exception to the universal log file."""
    if isinstance(message, BaseException) and getattr(message, "_swarif_logged", False):
        return
    timestamp = datetime.now(timezone.utc).isoformat()
    if isinstance(message, BaseException):
        if message.__traceback__ is not None:
            content = "".join(
                traceback.format_exception(type(message), message, message.__traceback__)
            ).rstrip()
        else:
            content = f"{type(message).__name__}: {message}"
    else:
        content = str(message)

    with _LOG_LOCK:
        with LOG_PATH.open("a", encoding="utf-8") as log_file:
            log_file.write(f"[{timestamp}] {content}\n")
        _trim_log_file()


def log_exceptions(function):
    """Log exceptions escaping a function, then preserve normal propagation."""
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exception as error:
            if not getattr(error, "_swarif_logged", False):
                log(error)
                try:
                    error._swarif_logged = True
                except Exception:
                    pass
            raise

    return wrapped


def log_static_methods(cls):
    """Apply exception logging to every static method on a class."""
    for name, descriptor in list(vars(cls).items()):
        if isinstance(descriptor, staticmethod):
            setattr(cls, name, staticmethod(log_exceptions(descriptor.__func__)))
    return cls


def install_exception_hooks():
    """Log otherwise uncaught exceptions from main and worker threads."""
    global _HOOKS_INSTALLED
    if _HOOKS_INSTALLED:
        return
    _HOOKS_INSTALLED = True

    original_sys_hook = sys.excepthook
    original_thread_hook = threading.excepthook

    def sys_hook(exception_type, exception, traceback_object):
        if not getattr(exception, "_swarif_logged", False):
            log(exception.with_traceback(traceback_object))
        original_sys_hook(exception_type, exception, traceback_object)

    def thread_hook(args):
        if not getattr(args.exc_value, "_swarif_logged", False):
            log(args.exc_value.with_traceback(args.exc_traceback))
        original_thread_hook(args)

    sys.excepthook = sys_hook
    threading.excepthook = thread_hook
