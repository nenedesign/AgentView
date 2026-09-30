"""Public decorators: @observe_agent, @observe_tool, @observe_model, @observe_validation.

Each decorator wraps a function so that entering it opens a span, exiting it
closes the span, and any exception is recorded as an execution error. The
outermost `@observe_agent` also opens and closes a Run.

Both sync and async callables are supported. Detection is via
`asyncio.iscoroutinefunction`.
"""

from __future__ import annotations

import asyncio
import functools
import traceback
from typing import Any, Callable

from agentview.context import (
    RunHandle,
    _new_id,
    _now_iso,
    _reset_run,
    _reset_span,
    _set_run,
    _set_span,
    current_run,
    current_span_id,
)
from agentview.events import ValidationResult
from agentview.storage import default_trace_path, write_event


def _tool_result_status(value: Any) -> str:
    """Default TOOL result heuristic: empty containers -> EMPTY, else VALID."""
    if value is None:
        return "EMPTY"
    if isinstance(value, (list, tuple, set, dict, str)) and len(value) == 0:
        return "EMPTY"
    return "VALID"


def _tool_attributes(args: tuple, kwargs: dict, result: Any) -> dict[str, Any]:
    """Structured summary of a tool call. No values, only shapes."""
    arg_keys = sorted(kwargs.keys())
    attrs: dict[str, Any] = {
        "arg_keys": arg_keys,
        "arg_types": {k: type(v).__name__ for k, v in kwargs.items()},
        "return_type": type(result).__name__,
    }
    if hasattr(result, "__len__"):
        try:
            attrs["return_length"] = len(result)
        except TypeError:
            pass
    return attrs


def _open_run_if_needed(
    name: str, trace_path: str | None
) -> tuple[RunHandle | None, Any | None, str | None]:
    """If no run is active, start one. Returns (run, token, effective_path)."""
    existing = current_run()
    if existing is not None:
        return existing, None, existing.trace_path
    run_id = _new_id("run")
    path = trace_path or str(default_trace_path(run_id))
    run = RunHandle(
        id=run_id, name=name, started_at=_now_iso(), trace_path=path
    )
    token = _set_run(run)
    write_event(
        path,
        "run_start",
        run.id,
        {
            "id": run.id,
            "name": run.name,
            "started_at": run.started_at,
            "execution_status": "OK",
            "metadata": run.metadata,
        },
    )
    return run, token, path


def _close_run(run: RunHandle, token: Any, execution_status: str) -> None:
    """Write run_end (with final metadata snapshot) and clear the ContextVar."""
    assert run.trace_path is not None
    write_event(
        run.trace_path,
        "run_end",
        run.id,
        {
            "id": run.id,
            "ended_at": _now_iso(),
            "execution_status": execution_status,
            "metadata": run.metadata,
        },
    )
    _reset_run(token)


def _write_span(
    trace_path: str,
    span_id: str,
    run_id: str,
    parent_id: str | None,
    kind: str,
    name: str,
    label: str,
    started_at: str,
    ended_at: str,
    execution_status: str,
    result_status: str | None,
    attributes: dict[str, Any],
    error: dict[str, Any] | None,
    validation_result: dict[str, Any] | None,
    validates: str | None,
) -> None:
    payload = {
        "id": span_id,
        "run_id": run_id,
        "parent_id": parent_id,
        "kind": kind,
        "name": name,
        "label": label,
        "started_at": started_at,
        "ended_at": ended_at,
        "execution_status": execution_status,
        "result_status": result_status,
        "attributes": attributes,
        "events": [],
        "error": error,
    }
    if validation_result is not None:
        payload["validation_result"] = validation_result
    if validates is not None:
        payload["validates"] = validates
    write_event(trace_path, "span_end", run_id, payload)


def _make_span_wrapper(
    func: Callable,
    kind: str,
    name: str | None,
    label: str | None,
    is_run_root: bool,
    trace_path_arg: str | None,
    is_async: bool,
) -> Callable:
    span_name = name or func.__name__
    span_label = label or span_name

    def _prelude() -> tuple[RunHandle | None, Any | None, str | None, str, str, Any, str | None]:
        run, run_token, path = _open_run_if_needed(span_name, trace_path_arg)
        span_id = _new_id("span")
        parent = current_span_id()
        started = _now_iso()
        span_token = _set_span(span_id)
        # For validation spans, auto-link to the most recently closed
        # validatable sibling under the same parent.
        validates: str | None = None
        if kind == "VALIDATION" and run is not None:
            validates = run.last_validatable_under(parent)
        return run, run_token, path, span_id, started, (parent, span_token), validates

    def _finalize(
        run: RunHandle,
        run_token: Any | None,
        path: str,
        span_id: str,
        started: str,
        parent_and_token: tuple[str | None, Any],
        result: Any,
        exc: BaseException | None,
        validates: str | None,
    ) -> None:
        parent, span_token = parent_and_token
        ended = _now_iso()
        execution_status = "OK" if exc is None else "ERROR"
        result_status: str | None = None
        attributes: dict[str, Any] = {}
        error_info: dict[str, Any] | None = None

        if kind == "TOOL":
            if exc is None:
                attributes = _tool_attributes((), {}, result)
                result_status = _tool_result_status(result)
            else:
                result_status = "INVALID"
        elif kind == "VALIDATION":
            if isinstance(result, ValidationResult):
                result_status = result.status
                validation_payload = result.model_dump()
            elif exc is not None:
                result_status = "INVALID"
                validation_payload = None
            else:
                result_status = "UNKNOWN"
                validation_payload = None

        validation_payload_out: dict[str, Any] | None = None
        if kind == "VALIDATION" and exc is None and isinstance(result, ValidationResult):
            validation_payload_out = result.model_dump()

        if exc is not None:
            error_info = {
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": "".join(traceback.format_exception(exc)),
            }

        _write_span(
            trace_path=path,
            span_id=span_id,
            run_id=run.id,
            parent_id=parent,
            kind=kind,
            name=span_name,
            label=span_label,
            started_at=started,
            ended_at=ended,
            execution_status=execution_status,
            result_status=result_status,
            attributes=attributes,
            error=error_info,
            validation_result=validation_payload_out,
            validates=validates,
        )
        _reset_span(span_token)
        run.note_closed_span(parent, kind, span_id)

        if is_run_root and run_token is not None:
            _close_run(run, run_token, execution_status)

    if is_async:

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            run, run_token, path, span_id, started, parent_and_token, validates = _prelude()
            assert run is not None and path is not None
            try:
                result = await func(*args, **kwargs)
            except BaseException as exc:
                _finalize(run, run_token, path, span_id, started, parent_and_token, None, exc, validates)
                raise
            _finalize(run, run_token, path, span_id, started, parent_and_token, result, None, validates)
            return result

        return async_wrapper

    @functools.wraps(func)
    def sync_wrapper(*args, **kwargs):
        run, run_token, path, span_id, started, parent_and_token, validates = _prelude()
        assert run is not None and path is not None
        try:
            result = func(*args, **kwargs)
        except BaseException as exc:
            _finalize(run, run_token, path, span_id, started, parent_and_token, None, exc, validates)
            raise
        _finalize(run, run_token, path, span_id, started, parent_and_token, result, None, validates)
        return result

    return sync_wrapper


def _decorator_factory(kind: str, is_run_root: bool):
    def factory(
        _fn: Callable | None = None,
        *,
        name: str | None = None,
        label: str | None = None,
        trace_path: str | None = None,
    ):
        def wrap(func: Callable) -> Callable:
            is_async = asyncio.iscoroutinefunction(func)
            return _make_span_wrapper(
                func, kind, name, label, is_run_root, trace_path, is_async
            )

        if _fn is not None and callable(_fn):
            return wrap(_fn)
        return wrap

    return factory


observe_agent = _decorator_factory("AGENT", is_run_root=True)
observe_tool = _decorator_factory("TOOL", is_run_root=False)
observe_model = _decorator_factory("MODEL", is_run_root=False)
observe_validation = _decorator_factory("VALIDATION", is_run_root=False)
