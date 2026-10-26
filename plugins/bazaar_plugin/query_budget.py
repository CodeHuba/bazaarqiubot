"""Shared, cooperative resource budgets for web queries and snapshot refreshes.

SQLite is interrupted through its progress handler; Python loops must call
checkpoint (or use budgeted_rows). This deliberately does not kill threads.
"""
from contextvars import ContextVar
from collections import Counter, OrderedDict
from concurrent.futures import Future
from functools import wraps
import os
from pathlib import Path
import threading
import time

_current = ContextVar('qiubot_query_budget', default=None)


class BoundedTTLCache(OrderedDict):
    """Existing (value, epoch-expiry) cache interface, with bounded retention."""
    def __init__(self, limit=64):
        super().__init__()
        self.limit = limit
        self.lock = threading.RLock()

    def get(self, key, default=None):
        with self.lock:
            value = super().get(key)
            if value is None or value[1] <= time.time():
                self.pop(key, None)
                return default
            self.move_to_end(key)
            return value

    def __setitem__(self, key, value):
        with self.lock:
            now = time.time()
            for stale in [k for k, v in self.items() if v[1] <= now]:
                self.pop(stale, None)
            super().__setitem__(key, value)
            self.move_to_end(key)
            while len(self) > self.limit:
                self.popitem(last=False)

    def clear(self):
        with self.lock:
            super().clear()


def available_memory_mb():
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith('MemAvailable:'):
                return int(line.split()[1]) / 1024
    except (OSError, ValueError):
        pass
    return None  # Non-Linux development hosts have no /proc.


class BudgetExceeded(RuntimeError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__('查询耗时过长，请缩小筛选范围后重试' if reason == 'timeout'
                         else '查询繁忙，请稍后重试')


class Budget:
    def __init__(self, controller, seconds, label):
        self.controller, self.label = controller, label
        self.deadline = time.monotonic() + seconds
        self.reason = None
        self.connections = []
        self.token = None
        self.closed = False
        self.next_memory_check = 0

    def __enter__(self):
        self.token = _current.set(self)
        return self

    def check(self):
        now = time.monotonic()
        if self.reason is None and now >= self.deadline:
            self.reason = 'timeout'
        if self.reason is None and now >= self.next_memory_check:
            self.next_memory_check = now + .5
            available = self.controller.memory()
            if (available is not None and self.controller.min_memory_mb > 0
                    and available < self.controller.min_memory_mb / 2):
                self.reason = 'memory'
        if self.reason:
            raise BudgetExceeded(self.reason)

    def sqlite_progress(self):
        try:
            self.check()
        except BudgetExceeded:
            return 1
        return 0

    def register(self, conn):
        if not any(existing is conn for existing in self.connections):
            self.connections.append(conn)
            conn.set_progress_handler(self.sqlite_progress, 2000)
            conn.execute('PRAGMA busy_timeout=1000')

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            for conn in self.connections:
                conn.close()  # SQLite close is idempotent, including explicit route closes.
        finally:
            try:
                if self.token is not None:
                    _current.reset(self.token)
            finally:
                self.controller.release(self.reason)
                self.connections.clear()

    def __exit__(self, *exc):
        self.close()


class QueryController:
    def __init__(self, limit=2, seconds=20, min_memory_mb=384, memory=available_memory_mb):
        self.limit = max(1, int(limit))
        self.seconds = max(.01, float(seconds))
        self.min_memory_mb = max(0, float(min_memory_mb))
        self.memory = memory
        self.lock = threading.Lock()
        self.active = 0
        self.peak = 0
        self.rejections = Counter()
        self.interruptions = Counter()

    def acquire(self, *, seconds=None, label='query'):
        available = self.memory()
        with self.lock:
            reason = ('memory' if available is not None and available < self.min_memory_mb
                      else 'concurrency' if self.active >= self.limit else None)
            if reason:
                self.rejections[reason] += 1
                raise BudgetExceeded(reason)
            self.active += 1
            self.peak = max(self.peak, self.active)
        return Budget(self, self.seconds if seconds is None else seconds, label)

    def release(self, reason):
        with self.lock:
            self.active -= 1
            if reason:
                self.interruptions[reason] += 1

    def status(self):
        with self.lock:
            return dict(active=self.active, peak=self.peak, limit=self.limit,
                        timeout_seconds=self.seconds, min_available_mb=self.min_memory_mb,
                        rejected=dict(self.rejections), interrupted=dict(self.interruptions))


controller = QueryController(
    limit=int(os.getenv('QUERY_MAX_CONCURRENCY', '2')),
    seconds=float(os.getenv('QUERY_TIMEOUT_SECONDS', '20')),
    min_memory_mb=float(os.getenv('QUERY_MIN_AVAILABLE_MB', '384')),
)


def current_budget():
    return _current.get()


def checkpoint():
    budget = _current.get()
    if budget is not None:
        budget.check()


def budgeted_rows(rows):
    for index, row in enumerate(rows):
        if index % 64 == 0:
            checkpoint()
        yield row
    checkpoint()


def attach_connection(conn):
    budget = _current.get()
    if budget is not None:
        budget.register(conn)


def singleflight(method):
    """Concurrent identical ranking requests share work; failures are retryable."""
    pending, lock = {}, threading.Lock()
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        key = (str(Path(self.db_path).resolve()), args, tuple(sorted(kwargs.items())))
        with lock:
            future = pending.get(key)
            leader = future is None
            if leader:
                pending[key] = future = Future()
        try:
            if leader:
                try:
                    result = method(self, *args, **kwargs)
                    future.set_result(result)
                    return result
                except BaseException as exc:
                    future.set_exception(exc)
                    raise
            while not future.done():
                checkpoint()
                try:
                    return future.result(timeout=.1)
                except TimeoutError:
                    if future.done():
                        return future.result()
            return future.result()
        except BudgetExceeded as exc:
            budget = current_budget()
            if budget is not None:
                budget.reason = exc.reason
            raise
        finally:
            if leader:
                with lock:
                    pending.pop(key, None)
    return wrapped
