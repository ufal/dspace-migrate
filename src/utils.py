import os
import logging
import re
import sys
_logger = logging.getLogger(__name__)


_ANSI_RESET = "\x1b[0m"
_ANSI_GREEN = "\x1b[32m"
_ANSI_RED = "\x1b[31m"


def _ansi_orange() -> str:
    term = (os.environ.get("TERM") or "").lower()
    colorterm = (os.environ.get("COLORTERM") or "").lower()
    if "256" in term or "truecolor" in colorterm or os.environ.get("WT_SESSION"):
        return "\x1b[38;5;214m"
    return "\x1b[33m"


def _supports_ansi_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if os.environ.get("PYCHARM_HOSTED"):
        return True

    stream = getattr(sys, "stderr", None)
    if stream is None or not hasattr(stream, "isatty"):
        return False
    if not stream.isatty():
        return False

    if os.name != "nt":
        return True

    return any([
        bool(os.environ.get("WT_SESSION")),
        bool(os.environ.get("ANSICON")),
        os.environ.get("ConEmuANSI", "").upper() == "ON",
        os.environ.get("TERM_PROGRAM", "").lower() == "vscode",
        bool(os.environ.get("TERM")),
    ])


class _ConsoleColorFormatter(logging.Formatter):
    def __init__(self, fmt: str, use_color: bool):
        super().__init__(fmt)
        self._use_color = use_color

    def format(self, record):
        msg = super().format(record)
        if not self._use_color:
            return msg
        if re.search(r"Test \[.+\]:\s+OK\b", msg):
            return f"{_ANSI_GREEN}{msg}{_ANSI_RESET}"
        if "Test [" in msg and not re.search(r"Test \[.+\]:\s+OK\b", msg):
            return f"{_ANSI_RED}{msg}{_ANSI_RESET}"
        if re.search(r"\bCRIT\b\s+(expected:\[|v5:\[)", msg):
            return f"{_ANSI_RED}{msg}{_ANSI_RESET}"
        if re.search(r"\bWARN\b\s+(expected:\[|v5:\[)", msg):
            return f"{_ansi_orange()}{msg}{_ANSI_RESET}"
        if re.search(r"\bOK\b\s+(expected:\[|v5:\[)", msg):
            return f"{_ANSI_GREEN}{msg}{_ANSI_RESET}"
        return msg


def _enable_windows_ansi_if_possible():
    if os.name != "nt":
        return
    try:
        import colorama
        colorama.just_fix_windows_console()
    except Exception:
        pass


def load_env(file_str: str):
    if not os.path.exists(file_str):
        _logger.debug(f"File [{file_str}] not found.")
        return False
    with open(file_str, "r", encoding="utf-8") as fin:
        for k, v in [x.strip().split("=") for x in fin.readlines() if len(x.strip()) > 0]:
            # win would use uppercase anyway
            os.environ[k.upper()] = v
    return True


def init_logging(
    logger,
    log_file: str,
    memory_log_file: str = None,
    console_level=logging.INFO,
    file_level=logging.INFO,
        format: str = '%(asctime)s:%(levelname).4s: %(message)s'):
    """
        Simple basic file/console logging.
    """
    base_log_dir = os.path.dirname(log_file)
    os.makedirs(base_log_dir, exist_ok=True)

    _enable_windows_ansi_if_possible()

    formatter = logging.Formatter(format)
    console_formatter = _ConsoleColorFormatter(format, _supports_ansi_color())
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(file_level)
    logger.addHandler(file_handler)

    found_stream = None
    for h in logger.handlers:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
            found_stream = h
            break
    if found_stream is None:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(console_level)
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)
    else:
        found_stream.setLevel(console_level)
        found_stream.setFormatter(console_formatter)

    logger.setLevel(logging.INFO)

    if memory_log_file:
        memory_log_dir = os.path.dirname(memory_log_file)
        os.makedirs(memory_log_dir, exist_ok=True)

        mem_logger = logging.getLogger("memory")
        mem_logger.setLevel(logging.INFO)
        mem_logger.propagate = False

        if not any(
            isinstance(h, logging.FileHandler) and getattr(
                h, "baseFilename", None) == os.path.abspath(memory_log_file)
            for h in mem_logger.handlers
        ):
            mem_handler = logging.FileHandler(memory_log_file, encoding="utf-8")
            mem_handler.setFormatter(formatter)
            mem_handler.setLevel(logging.INFO)
            mem_logger.addHandler(mem_handler)


def update_settings(main_env: dict, update_with: dict) -> dict:
    """
        Update `main_env` with `update_with`,
        if `update_with` value is a dict, update only keys which are in `main_env`
    """
    env = main_env.copy()
    for k, v in update_with.items():
        if isinstance(v, dict) and k in env:
            env[k].update(v)
            continue
        env[k] = v
    return env


def exists_key(special_format_key_str, dict_inst, return_val=False):
    """ Checks whether a recursive key exists defined in dot format."""
    parts = special_format_key_str.split(".")
    d = dict_inst
    for part in parts:
        if part is None or part not in d:
            return (False, None) if return_val else False
        d = d[part]
    return (True, d) if return_val else True


def set_key(special_format_key_str, value, dict_inst):
    """ Checks whether a recursive key exists defined in dot format."""
    parts = special_format_key_str.split(".")
    d = dict_inst
    for i, part in enumerate(parts):
        if part is None or part not in d:
            return False
        if i != len(parts) - 1:
            d = d[part]
        else:
            d[part] = value
    return True
