import json
import os
import logging
from datetime import datetime, timezone
from time import time as time_fnc
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm


_logger = logging.getLogger("pump.utils")


def read_json(file_name: str):
    """
        Read data from file as json.
        @param file_name: file name
        @return: data as json
    """
    if not os.path.exists(file_name):
        raise FileNotFoundError(f"File [{file_name}] does not exist.")
    with open(file_name, mode='r', encoding='utf-8') as f:
        return json.load(f)


def to_dict(arr: list):
    return {int(k): v for k, v in enumerate(arr)}


def ts() -> str:
    return str(datetime.now(timezone.utc))


def time_method(func):
    """
        Timer decorator will store execution time of a function into
        the class which it uses. The time will be stored in
        instance.timed
    """

    def _enclose(self, *args, **kw):
        """ Enclose every function with this one. """
        start = time_fnc()
        res = func(self, *args, **kw)
        took = time_fnc() - start
        if took > 10.:
            _logger.info(f"Method [{func.__name__}] took [{round(took, 2)}] seconds.")
        return res

    return _enclose


def time_function(d):
    """
        Timer decorator will store execution time of a function into
        the specified dict to timed entry.
    """

    def wrap(func):
        """ Simple wrapper around time decorator. """

        def _enclose(*args, **kw):
            """ Simple wrapper around wrap :). """
            start = time_fnc()
            res = func(*args, **kw)
            took = time_fnc() - start
            _logger.debug(f"Function [{func.__name__}] took [{round(took, 2)}] seconds.")
            return res

        return _enclose

    return wrap


def serial_d(data):
    """
        Unify serialisation data.
    """
    return {
        "data": data,
        "timestamp": ts(),
    }


def serialize(file_str: str, data, sorted=True):
    """
        Serialize data into json file.
    """
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, dict):
                keys = list(v.keys())
                if len(keys) > 0:
                    if isinstance(keys[0], int):
                        _logger.critical(
                            f"Serializing dictionary with integer keys [{file_str}] !!!")

    os.makedirs(os.path.dirname(file_str), exist_ok=True)
    with open(file_str, encoding="utf-8", mode="w") as fout:
        json.dump(serial_d(data), fout, indent=None, sort_keys=sorted)


def deserialize(file_str: str):
    with open(file_str, encoding="utf-8", mode="r") as fin:
        js = json.load(fin)
    return js["data"]


IMPORT_LIMIT = None
if os.environ.get("IMPORT_LIMIT", "0") != "0":
    IMPORT_LIMIT = int(os.environ["IMPORT_LIMIT"])
    _logger.critical(f"Using import limit [{IMPORT_LIMIT}]")


def progress_bar(arr, desc: str = None, total: int = None):
    arr_len = None
    try:
        arr_len = len(arr)
    except Exception:
        arr_len = total

    if arr_len is not None and arr_len < 2:
        return iter(arr)

    mininterval = 5 if (arr_len is not None and arr_len < 500) else 10
    kwargs = {
        "mininterval": mininterval,
        "maxinterval": 2 * mininterval,
    }
    if desc is not None:
        kwargs["desc"] = desc
    if total is not None:
        kwargs["total"] = total

    return tqdm(arr, **kwargs)


def log_before_import(msg: str, expected: int):
    _logger.info("=====")
    _logger.info(f"Importing   [{expected: >4d}] {msg}")


def log_after_import(msg: str, expected: int, imported: int):
    is_ok = expected == imported
    status = "OK" if is_ok else "WARN"
    counts = f"expected:[{expected:>8}], imported:[{imported:>8}]"
    _logger.info(f"{status:<12} {counts}  {msg}")


def run_tasks(tasks, worker, workers: int = 1, desc: str = None, on_result=None):
    tasks = list(tasks or [])
    workers = max(1, int(workers or 1))

    if workers == 1 or len(tasks) < 2:
        iterator = progress_bar(tasks, desc=desc)
        for task in iterator:
            try:
                result = worker(task)
                if on_result is not None:
                    on_result(task, result, None, iterator)
                yield task, result, None
            except Exception as e:
                if on_result is not None:
                    on_result(task, None, e, iterator)
                yield task, None, e
        return

    futures = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for task in tasks:
            futures[executor.submit(worker, task)] = task

        iterator = tqdm(
            as_completed(futures),
            total=len(futures),
            desc=desc or f"workers:{workers}",
            mininterval=5,
        )

        for future in iterator:
            task = futures[future]
            try:
                result = future.result()
                if on_result is not None:
                    on_result(task, result, None, iterator)
                yield task, result, None
            except Exception as e:
                if on_result is not None:
                    on_result(task, None, e, iterator)
                yield task, None, e
