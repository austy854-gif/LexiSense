"""Per-task database handle for Celery workers.

Each task gets its own short-lived Motor client (a Motor client binds to the
event loop it is first used on, and every task runs its own loop). The handle
is stored in a ContextVar set by the task_prerun signal and reset by
task_postrun, instead of a module global, so it is correct under any worker
pool (prefork, threads, solo): concurrent tasks in threads never see each
other's client. ``asyncio`` copies the current context into the loop the task
runs, so coroutines read the same value.
"""
from contextvars import ContextVar

current_db: ContextVar = ContextVar("lexisense_task_db", default=None)
