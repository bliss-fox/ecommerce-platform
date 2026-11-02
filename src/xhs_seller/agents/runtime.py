"""Bounded daemon workers, monotonic deadlines, no database/UI in background."""

import json
import math
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from xhs_seller.agents.snapshot import canonical
from xhs_seller.agents.tools import SPECIALISTS, RoleTools, execute_role, validate_result
from xhs_seller.services.drafts import clean_text

FLOW_VERSION = "sku-review-v1"


@dataclass(frozen=True)
class Event:
    sequence: int
    role: str
    state: str
    at: str
    detail: str


class WorkerPool:
    """A per-session ceiling, including cancelled workers that have not returned."""

    def __init__(self):
        self.lock = threading.RLock()
        self._active = 0

    @property
    def active(self):
        with self.lock:
            return self._active

    def submit(self, function):
        with self.lock:
            if self._active >= 3:
                raise ValueError("旧任务资源尚未释放，请等待后重试")
            self._active += 1

        def work():
            try:
                function()
            finally:
                with self.lock:
                    self._active -= 1

        thread = threading.Thread(target=work, daemon=True, name="sku-review-worker")
        try:
            thread.start()
        except Exception:
            with self.lock:
                self._active -= 1
            raise


class Run:
    def __init__(
        self,
        snapshot,
        *,
        pool=None,
        worker=execute_role,
        clock=time.monotonic,
        step_timeout=30,
        total_timeout=120,
        goal="",
    ):
        if any(not math.isfinite(v) or v <= 0 for v in (step_timeout, total_timeout)):
            raise ValueError("超时必须是有限正数")
        goal = clean_text(goal)
        if len(goal) > 500:
            raise ValueError("目标说明最多 500 字")
        self.snapshot, self.goal = snapshot, goal
        self.run_id = str(uuid4())
        self.pool = pool or WorkerPool()
        self._worker, self._clock = worker, clock
        self._step_timeout, self._total_timeout = step_timeout, total_timeout
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._started = clock()
        self._status = "running"
        self._states, self._results, self._errors, self._starts = {}, {}, {}, {}
        self._events = []
        with self._lock, self.pool.lock:
            if self.pool.active:
                raise ValueError("旧任务资源尚未释放，请等待后重试")
            self._event("main", "dispatched", "建立 SKU 范围并派发三个离线角色")
            for role in SPECIALISTS:
                self._dispatch(role, {})
        self._controller = threading.Thread(
            target=self._control, daemon=True, name="sku-review-control"
        )
        self._controller.start()

    def _event(self, role, state, detail):
        self._events.append(
            Event(len(self._events) + 1, role, state, datetime.now(UTC).isoformat(), detail)
        )

    def _tool_event(self, role, name):
        with self._lock:
            if self._status != "running" or self._states.get(role) != "running":
                raise ValueError("RUN_STOPPED")
            self._event(role, "tool", name)

    def _dispatch(self, role, inputs):
        self._states[role] = "running"
        self._starts[role] = self._clock()
        self._event(role, "started", "离线规则处理")

        # Each callable receives only a narrow tool object and immutable prior results.
        def work():
            try:
                tools = RoleTools(self.snapshot, role, lambda name: self._tool_event(role, name))
                result = self._worker(role, tools, dict(inputs))
                result = validate_result(result, RoleTools(self.snapshot, role), dict(inputs))
                error = None
            except PermissionError:
                result, error = None, "TOOL_DENIED"
            except ValueError:
                result, error = None, "INVALID_OUTPUT_OR_SOURCE"
            except Exception:  # noqa: BLE001 - worker boundary must redact unexpected errors
                result, error = None, "ROLE_FAILED"
            with self._lock:
                if self._status != "running" or self._states.get(role) != "running":
                    return
                now = self._clock()
                if now - self._started >= self._total_timeout:
                    self._finish("timed_out")
                    return
                if now - self._starts[role] >= self._step_timeout:
                    self._fail(role, "STEP_TIMEOUT")
                elif error:
                    self._fail(role, error)
                else:
                    self._results[role] = result
                    self._states[role] = "completed"
                    self._event(role, "completed", "产物与确定性工具输出校验一致")
            self._wake.set()

        try:
            self.pool.submit(work)
        except (ValueError, RuntimeError):
            self._fail(role, "RESOURCE_UNAVAILABLE")

    def _fail(self, role, error):
        self._states[role] = "failed"
        self._errors[role] = error
        self._event(role, "failed", error)

    def _finish(self, status):
        self._status = status
        for role, state in list(self._states.items()):
            if state == "running":
                self._states[role] = status
                self._event(role, status, "停止接收该步骤的迟到结果")
        self._event("main", status, "运行结束；经营判断见角色产物")
        self._wake.set()

    def _control(self):
        while True:
            with self._lock:
                if self._status != "running":
                    return
                now = self._clock()
                if now - self._started >= self._total_timeout:
                    self._finish("timed_out")
                    return
                for role, state in list(self._states.items()):
                    if state == "running" and now - self._starts[role] >= self._step_timeout:
                        self._fail(role, "STEP_TIMEOUT")
                if all(self._states[r] != "running" for r in SPECIALISTS):
                    if "review" not in self._states and self.pool.active < 3:
                        self._dispatch("review", dict(self._results))
                    elif "review" in self._states and self._states["review"] != "running":
                        if "review" not in self._results:
                            self._finish("partial")
                            return
                        if "summary" not in self._states and self.pool.active < 3:
                            self._dispatch("summary", {"review": self._results["review"]})
                        elif "summary" in self._states and self._states["summary"] != "running":
                            self._finish("partial" if self._errors else "completed")
                            return
            self._wake.wait(0.02)
            self._wake.clear()

    def cancel(self):
        with self._lock:
            if self._status == "running":
                self._finish("cancelled")

    def view(self):
        with self._lock:
            return {
                "run_id": self.run_id,
                "status": self._status,
                "states": dict(self._states),
                "results": dict(self._results),
                "errors": dict(self._errors),
                "events": tuple(self._events),
            }


class RunSession:
    """Owned by one Streamlit session; repeated clicks do not repeat dispatch."""

    def __init__(self):
        self.pool = WorkerPool()
        self.run = None
        self.space = None

    def switch(self, space):
        if self.space != space:
            if self.run:
                self.run.cancel()
            self.run = None
            self.space = space

    def start(self, snapshot, *, rerun=False, **kwargs):
        self.switch(snapshot.space)
        if self.run and (self.run.view()["status"] == "running" or not rerun):
            return self.run
        self.run = Run(snapshot, pool=self.pool, **kwargs)
        return self.run


def serialized_view(view):
    return canonical(
        {
            **view,
            "results": {
                k: {"role": r.role, "payload": json.loads(r.payload), "refs": r.refs}
                for k, r in view["results"].items()
            },
            "events": [e.__dict__ for e in view["events"]],
        }
    )
