"""Bounded in-process model/tool adapter; no SDK, router, or approval capability.

Provider contract: provider(model=<fixed string>, messages=<JSON messages>) returns
{"text": str, "tool_calls": [{"name": str, "arguments": dict}, ...]}.
The trusted provider adapter must enforce its own network request timeout. This
synchronous runner bounds steps, retries, calls, and bytes, not wall-clock time.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import uuid

from .service import AgentGateway, DomainError
from .database import in_transaction


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


@dataclass(frozen=True)
class ModelConfig:
    model: str
    max_steps: int = 4
    max_attempts: int = 2
    max_tool_calls: int = 4
    max_output_bytes: int = 262144
    max_context_bytes: int = 1048576
    max_tool_result_bytes: int = 524288

    def __post_init__(self):
        if not isinstance(self.model, str) or not self.model.strip() or len(self.model) > 200:
            raise ValueError("Use a fixed nonempty model name")
        for key, cap in (("max_steps", 16), ("max_attempts", 2), ("max_tool_calls", 8),
                         ("max_output_bytes", 2097152), ("max_context_bytes", 4194304),
                         ("max_tool_result_bytes", 2097152)):
            value = getattr(self, key)
            if type(value) is not int or not 1 <= value <= cap:
                raise ValueError(f"{key} must be an integer in 1..{cap}")


class _Failure(Exception):
    def __init__(self, code, message, error_type=None):
        self.error = {"code": code, "message": message}
        if error_type:
            self.error["error_type"] = error_type
        super().__init__(code)


class ModelRuntime:
    """Host constructs this object; never expose its service/provider to a model.

    Logs store hashes and configuration, not prompt/response bodies. On a tool
    failure execution stops, preserving any earlier draft with its normal status.
    A killed process may leave a run as running; automatic crash recovery is not
    provided by this small adapter.
    """
    def __init__(self, service, actor, provider, *, config: ModelConfig, capture_tool_trace=False,
                 allowed_tools=None):
        if not isinstance(config, ModelConfig) or not callable(provider):
            raise ValueError("A ModelConfig and callable provider are required")
        if allowed_tools is None:
            allowed_tools = tuple(AgentGateway.ALLOWED)
        if (not isinstance(allowed_tools, (list, tuple, set, frozenset))
                or not allowed_tools
                or any(not isinstance(name, str) or not name for name in allowed_tools)
                or len(set(allowed_tools)) != len(allowed_tools)
                or not set(allowed_tools) <= AgentGateway.ALLOWED):
            raise ValueError("allowed_tools must be a nonempty collection of unique exposed tool names")
        if in_transaction(service.db):
            raise ValueError("Runtime must not be created inside a transaction")
        self._service, self._actor, self._provider = service, actor, provider
        self.config = config
        self.capture_tool_trace = bool(capture_tool_trace)
        self._gateway = AgentGateway(service, actor, allowed_tools=allowed_tools)

    def _bounded_json(self, value, limit, code):
        try:
            encoded = _json(value)
        except (ValueError, TypeError, RecursionError):
            raise _Failure(code, "Expected finite JSON-compatible data") from None
        if len(encoded.encode()) > limit:
            raise _Failure(code, "Configured byte limit exceeded")
        return encoded

    def _messages(self, messages):
        encoded = self._bounded_json(messages, self.config.max_context_bytes, "INVALID_MESSAGES")
        if not isinstance(messages, list) or not 1 <= len(messages) <= 100:
            raise _Failure("INVALID_MESSAGES", "Provide 1..100 system/user/assistant messages")
        for message in messages:
            if not isinstance(message, dict) or set(message) != {"role", "content"}:
                raise _Failure("INVALID_MESSAGES", "Each initial message needs only role and content")
            if message["role"] not in ("system", "user", "assistant") or not isinstance(message["content"], str):
                raise _Failure("INVALID_MESSAGES", "Invalid role or content")
        return json.loads(encoded)

    def _model_response(self, response):
        encoded = self._bounded_json(response, self.config.max_output_bytes, "INVALID_MODEL_RESPONSE")
        if not isinstance(response, dict) or set(response) != {"text", "tool_calls"}:
            raise _Failure("INVALID_MODEL_RESPONSE", "Provider must return text and tool_calls only")
        if not isinstance(response["text"], str) or not isinstance(response["tool_calls"], list):
            raise _Failure("INVALID_MODEL_RESPONSE", "Invalid text or tool_calls type")
        calls = response["tool_calls"]
        if len(calls) > self.config.max_tool_calls:
            raise _Failure("TOOL_CALL_LIMIT", "Too many tool calls in one model response")
        if not calls and not response["text"].strip():
            raise _Failure("EMPTY_MODEL_RESPONSE", "A final response must contain text")
        # Validate the whole batch before executing any draft mutation.
        for call in calls:
            if not isinstance(call, dict) or set(call) != {"name", "arguments"}:
                raise _Failure("INVALID_TOOL_CALL", "Each call needs name and arguments only")
            if not isinstance(call["name"], str) or call["name"] not in self._gateway.allowed_tools:
                raise _Failure("TOOL_NOT_ALLOWED", "Tool is not exposed to this Agent role")
            arguments = call["arguments"]
            if not isinstance(arguments, dict) or "actor" in arguments or "principals" in arguments:
                raise _Failure("INVALID_TOOL_ARGUMENTS", "Identity is bound by the host")
            if call["name"] == "prepare_draft":
                key = arguments.get("request_key")
                if not isinstance(key, str) or not 1 <= len(key) <= 200:
                    raise _Failure("REQUEST_KEY_REQUIRED", "Draft mutations need a stable request_key")
        return json.loads(encoded)

    def _tool_response(self, response):
        encoded = self._bounded_json(response, self.config.max_tool_result_bytes, "TOOL_RESULT_LIMIT")
        if not isinstance(response, dict) or not isinstance(response.get("ok"), bool):
            raise _Failure("INVALID_TOOL_RESPONSE", "Expected the common tool envelope")
        if not response["ok"]:
            errors = response.get("errors") or [{"code": "TOOL_FAILED"}]
            raise _Failure(errors[0].get("code", "TOOL_FAILED"), "Common tool reported failure")
        return json.loads(encoded)

    @staticmethod
    def _tool_ids(value):
        if not isinstance(value, dict):
            return []
        ids = [value["run_id"]] if isinstance(value.get("run_id"), str) else []
        for warning in value.get("warnings", []):
            if isinstance(warning, dict) and isinstance(warning.get("original_run_id"), str):
                ids.append(warning["original_run_id"])
        return sorted(set(ids))

    def _attempt(self, runtime_id, step, kind, request, invoke, validate, tool_name=None):
        db = self._service.db
        for attempt in range(1, self.config.max_attempts + 1):
            sid, now = str(uuid.uuid4()), self._service.now()
            db.execute("INSERT INTO runtime_steps(id,runtime_id,step,kind,tool_name,attempt,status,input_hash,output_hash,tool_run_ids,started_at,finished_at,error) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                       (sid, runtime_id, step, kind, tool_name, attempt, "running", _hash(request), None, "[]", now, None, None))
            response = None
            try:
                # There is deliberately no open database transaction around callbacks.
                if in_transaction(db):
                    raise _Failure("TRANSACTION_BOUNDARY", "Callback cannot run inside a transaction")
                response = invoke()
                normalized = validate(response)
                db.execute("UPDATE runtime_steps SET status=%s,output_hash=%s,tool_run_ids=%s,finished_at=%s WHERE id=%s",
                           ("completed", _hash(normalized), _json(self._tool_ids(response) if kind == "tool" else []), self._service.now(), sid))
                return normalized
            except (TimeoutError, ConnectionError) as exc:
                error = {"code": "TRANSIENT_" + kind.upper() + "_ERROR", "error_type": type(exc).__name__}
                db.execute("UPDATE runtime_steps SET status=%s,finished_at=%s,error=%s WHERE id=%s",
                           ("failed", self._service.now(), _json(error), sid))
                if attempt == self.config.max_attempts:
                    raise _Failure(error["code"], "Transient retry budget exhausted", type(exc).__name__) from None
            except Exception as exc:
                failure = (exc if isinstance(exc, _Failure) else
                           _Failure(exc.code, "Provider or common tool reported failure") if isinstance(exc, DomainError) else
                           _Failure(kind.upper() + "_ERROR", "Callback failed", type(exc).__name__))
                db.execute("UPDATE runtime_steps SET status=%s,tool_run_ids=%s,finished_at=%s,error=%s WHERE id=%s",
                           ("failed", _json(self._tool_ids(response) if kind == "tool" else []), self._service.now(), _json(failure.error), sid))
                raise failure from None

    def run(self, messages):
        """Run finite model/tool steps; final text is returned but not persisted."""
        if in_transaction(self._service.db):
            raise ValueError("Runtime cannot run inside a transaction")
        run_id, db = str(uuid.uuid4()), self._service.db
        config = asdict(self.config)
        config['allowed_tools'] = sorted(self._gateway.allowed_tools)
        if callable(getattr(self._provider, 'public_config', None)):
            config['provider'] = self._provider.public_config()
        db.execute("INSERT INTO runtime_runs VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                   (run_id, self._actor, self.config.model, _json(config), "running", self._service.now(), None, None, None, None))
        status, text, error, steps = "failed", "", None, 0
        trace = []
        try:
            self._service._require(self._actor, "read")
            history = self._messages(messages)
            db.execute("UPDATE runtime_runs SET prompt_hash=%s WHERE id=%s", (_hash(history), run_id))
            for step in range(1, self.config.max_steps + 1):
                steps = step
                self._bounded_json(history, self.config.max_context_bytes, "CONTEXT_LIMIT")
                request = {"model": self.config.model, "messages": history}
                response = self._attempt(run_id, step, "model", request,
                    lambda: self._provider(model=self.config.model, messages=json.loads(_json(history))), self._model_response)
                if not response["tool_calls"]:
                    status, text = "completed", response["text"]
                    break
                history.append({"role": "assistant", "content": response["text"], "tool_calls": response["tool_calls"]})
                for index, call in enumerate(response["tool_calls"]):
                    args = json.loads(_json(call["arguments"]))
                    def invoke(name=call['name'], args=args):
                        result = self._gateway.call(name, json.loads(_json(args)))
                        if self.capture_tool_trace:
                            trace.append({'name': name, 'arguments': json.loads(_json(args)), 'result': result})
                        return result
                    result = self._attempt(run_id, step, "tool", call,
                        invoke,
                        self._tool_response, call["name"])
                    history.append({"role": "tool", "name": call["name"], "call_index": index, "content": _json(result)})
            else:
                status, error = "step_limit", {"code": "STEP_LIMIT", "message": "Configured model step limit reached"}
        except _Failure as exc:
            error = exc.error
        except DomainError as exc:
            error = {"code": exc.code, "message": "Bound identity lacks permission"}
        except Exception as exc:
            error = {"code": "RUNTIME_ERROR", "message": "Runtime failed", "error_type": type(exc).__name__}
        db.execute("UPDATE runtime_runs SET status=%s,finished_at=%s,output_hash=%s,error=%s WHERE id=%s",
                   (status, self._service.now(), _hash(text) if status == "completed" else None, _json(error) if error else None, run_id))
        tool_ids = sorted({tid for row in db.execute("SELECT tool_run_ids FROM runtime_steps WHERE runtime_id=%s", (run_id,)) for tid in json.loads(row['tool_run_ids'])})
        result = {"ok": status == "completed", "run_id": run_id, "status": status, "text": text,
                "model": self.config.model, "steps": steps, "tool_run_ids": tool_ids, "error": error,
                "output_hash": _hash(text) if status == "completed" else None}
        if self.capture_tool_trace:
            result['tool_trace'] = trace
        return result
