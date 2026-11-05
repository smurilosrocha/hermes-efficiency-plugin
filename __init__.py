"""eficiencia: local usage telemetry for Hermes Agent.

Observes every model call through the official hooks ``pre_api_request`` / ``post_api_request`` /
``api_request_error`` and writes ONE JSON line per call with:

  - HMAC-SHA256 fingerprints (local random key, 96 effective bits) of the whole request
    (model + system prompt + messages), of the system prompt and of the last message;
  - model, provider, profile, platform, session, task, message count, size in bytes;
  - token counters (numbers only), duration, finish reason.

It never stores prompt, message, tool or response text, and never changes the request or the response:
the hooks only read. Any error here is swallowed, so the model call proceeds unchanged.

Output: <hermes root>/eficiencia/calls/<YYYY-MM-DD>.jsonl (0600). Key: <hermes root>/eficiencia/hmac.key (0600).
Disable: ``hermes -p <profile> plugins disable eficiencia`` or EFICIENCIA_OFF=1 in the environment.

Second schema version (also numbers and names only):

  - per call: bytes of the system prompt and of the skills index, bytes of messages by role, bytes of injected
    memory context, house-rule copies, number of tools, background flag;
  - ``post_tool_call`` -> <hermes root>/eficiencia/tools/<day>.jsonl: tool, ms, result size, status and the
    skill name for skill_view / skill_manage / skills_list;
  - ``post_auxiliary_call`` -> <hermes root>/eficiencia/aux/<day>.jsonl: auxiliary task, model, provider,
    tokens and duration.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Optional, Tuple

_LOCK = threading.Lock()
_PENDING: "OrderedDict[str, dict]" = OrderedDict()
_MAX_PENDING = 256
_KEYS: dict = {}


def _off() -> bool:
    return os.environ.get("EFICIENCIA_OFF", "").strip() not in ("", "0", "false", "no")


def _where() -> Tuple[Path, str]:
    """(Hermes root, profile name). Resolved on every call: one gateway can serve several profiles."""
    override = os.environ.get("EFICIENCIA_ROOT")
    try:
        from hermes_constants import get_hermes_home
        home = Path(get_hermes_home())
    except Exception:  # noqa: BLE001
        home = Path.home() / ".hermes"
    profile = home.name if home.parent.name == "profiles" else "default"
    root = home.parent.parent if home.parent.name == "profiles" else home
    return (Path(override) if override else root), profile


def _key(root: Path) -> bytes:
    k = _KEYS.get(root)
    if k:
        return k
    d = root / "eficiencia"
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = d / "hmac.key"
    if not path.exists():  # atomic create: write a temporary file and link it; whoever loses the race uses the other one
        tmp = d / f".hmac.key.{os.getpid()}.{threading.get_ident()}"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, os.urandom(32))
        finally:
            os.close(fd)
        try:
            os.link(tmp, path)
        except FileExistsError:
            pass
        finally:
            tmp.unlink(missing_ok=True)
    k = path.read_bytes()[:32]
    if len(k) != 32:
        raise ValueError("hmac.key inválida")
    _KEYS[root] = k
    return k


def _digest(key: bytes, obj: Any) -> Tuple[str, int]:
    if isinstance(obj, str):
        raw = obj.encode("utf-8", "surrogatepass")
    else:
        raw = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                         default=str).encode("utf-8", "surrogatepass")
    return hmac.new(key, raw, hashlib.sha256).hexdigest()[:24], len(raw)


def _num(v: Any) -> Optional[float]:
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _write(root: Path, rec: dict, sub: str = "calls") -> None:
    day = time.strftime("%Y-%m-%d", time.localtime(rec.get("ts") or time.time()))
    d = root / "eficiencia" / sub
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    line = (json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    # O_NOFOLLOW: a symlink at the day file cannot redirect the append to another file.
    fd = os.open(d / f"{day}.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.fchmod(fd, 0o600)  # the open mode only applies at creation; this fixes a pre-existing file
        os.write(fd, line)  # one O_APPEND write per line, so parallel processes do not interleave
    finally:
        os.close(fd)


_SKILLS_RE = None
_REVIEW = (("Review the conversation above and update two things", "ambos"),
           ("Review the conversation above and update the skill library", "skills"),
           ("Review the conversation above and consider saving to memory", "memoria"))
_RULES_MARK = "REGRAS DA CASA (operator)"
_MEMCTX = ("<memory-context>", "</memory-context>")


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") if isinstance(p, dict) and isinstance(p.get("text"), str) else ""
                       for p in content)
    return ""


def _breakdown(system: str, msgs: list) -> dict:
    """Só tamanhos e contagens; o texto não sai desta função."""
    global _SKILLS_RE
    import re
    if _SKILLS_RE is None:
        _SKILLS_RE = re.compile(r"<available_skills>.*?</available_skills>", re.S)
    sysb = len(system.encode("utf-8", "surrogatepass")) if system else 0
    sk = _SKILLS_RE.search(system) if system else None
    roles = {"user": 0, "assistant": 0, "tool": 0, "system": 0}
    memctx = rules = 0
    bg = ""
    last_user = ""
    for m in msgs:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or m.get("type") or "")
        if role in ("function_call_output",):
            role = "tool"
        elif role in ("function_call", "reasoning"):
            role = "assistant"
        try:
            size = len(json.dumps(m, ensure_ascii=False, default=str).encode("utf-8", "surrogatepass"))
        except Exception:  # noqa: BLE001
            size = 0
        content = m.get("content")
        if role == "user" and isinstance(content, list):
            # Anthropic: tool results arrive as tool_result blocks inside role=user
            tr = 0
            for part in content:
                if isinstance(part, dict) and part.get("type") == "tool_result":
                    try:
                        tr += len(json.dumps(part, ensure_ascii=False, default=str).encode("utf-8", "surrogatepass"))
                    except Exception:  # noqa: BLE001
                        pass
            roles["tool"] += tr
            size -= min(tr, size)
        roles[role if role in roles else "system"] += size
        if role == "user":
            t = _text(m.get("content"))
            last_user = t
            rules += t.count(_RULES_MARK)
            i = t.find(_MEMCTX[0])
            while i >= 0:
                j = t.find(_MEMCTX[1], i)
                if j < 0:
                    break
                memctx += len(t[i:j].encode("utf-8", "surrogatepass")) + len(_MEMCTX[1])
                i = t.find(_MEMCTX[0], j)
    for prefix, kind in _REVIEW:
        if last_user.startswith(prefix):
            bg = kind
            break
    return {"sys_b": sysb, "skills_b": len(sk.group(0).encode("utf-8", "surrogatepass")) if sk else 0,
            "m_user": roles["user"], "m_asst": roles["assistant"], "m_tool": roles["tool"], "m_sys": roles["system"],
            "memctx_b": memctx, "rules_n": rules, "bg": bg}


def _pre(**kw: Any) -> None:
    if _off():
        return
    try:
        root, profile = _where()
        key = _key(root)
        msgs = kw.get("request_messages") or []
        if not isinstance(msgs, list):
            msgs = []
        system = kw.get("system_prompt") or ""
        model = str(kw.get("model") or "")
        full, nbytes = _digest(key, [model, system, msgs])
        rec = {
            "v": 1, "profile": profile, "session": str(kw.get("session_id") or ""),
            "task": str(kw.get("task_id") or ""), "platform": str(kw.get("platform") or ""),
            "model": model, "provider": str(kw.get("provider") or ""), "api_mode": str(kw.get("api_mode") or ""),
            "call": _num(kw.get("api_call_count")), "retry": _num(kw.get("retry_count")) or 0,
            "n_msgs": len(msgs), "bytes": nbytes, "h_full": full,
            "h_sys": _digest(key, system)[0] if system else "",
            "h_last": _digest(key, msgs[-1])[0] if msgs else "",
            "t0": _num(kw.get("started_at")),
            "v2": 1, "tools_n": _num(kw.get("tool_count")), "approx_tok": _num(kw.get("approx_input_tokens")),
            "req_chars": _num(kw.get("request_char_count")),
        }
        try:
            rec.update(_breakdown(system if isinstance(system, str) else _text(system), msgs))
        except Exception:  # noqa: BLE001
            pass
        rid = str(kw.get("api_request_id") or "")
        if not rid:
            rec["ts"] = time.time()
            _write(root, rec)
            return
        rec["_root"] = str(root)
        with _LOCK:
            _PENDING[rid] = rec
            while len(_PENDING) > _MAX_PENDING:
                _, perdido = _PENDING.popitem(last=False)
                raiz = Path(perdido.pop("_root", str(root)))
                # without the response half this record would never close: write the request marked as
                # incomplete instead of dropping the call without a trace.
                perdido["incomplete"] = 1
                _write(raiz, perdido)
    except Exception:  # noqa: BLE001 - observer: never disturbs the call
        pass


def _finish(rid: str, extra: dict) -> None:
    with _LOCK:
        rec = _PENDING.pop(rid, None)
    if rec is None:  # plugin loaded mid-call: without the request half there is nothing to write
        return
    root = Path(rec.pop("_root"))
    rec.update(extra)
    _write(root, rec)


def _post(**kw: Any) -> None:
    if _off():
        return
    try:
        usage = kw.get("usage") or {}
        u = {k: v for k, v in usage.items() if _num(v) is not None} if isinstance(usage, dict) else {}
        started, first = _num(kw.get("started_at")), _num(kw.get("first_chunk_at"))
        _finish(str(kw.get("api_request_id") or ""), {
            "ts": _num(kw.get("ended_at")) or time.time(),
            "dur": round(_num(kw.get("api_duration")) or 0.0, 3),
            "ttfb": round(first - started, 3) if started and first else None,
            "finish": str(kw.get("finish_reason") or ""), "resp_model": str(kw.get("response_model") or ""),
            "out_chars": _num(kw.get("assistant_content_chars")),
            "tool_calls": _num(kw.get("assistant_tool_call_count")),
            "usage": u, "err": 0,
        })
    except Exception:  # noqa: BLE001
        pass


def _error(**kw: Any) -> None:
    if _off():
        return
    try:
        err = kw.get("error")
        # the hook passes error={"type": ..., "message": ...} and no error_type, so reading only the
        # keyword recorded every failure as "dict" and lost the classification.
        tipo = kw.get("error_type") or (err.get("type") if isinstance(err, dict) else None)
        _finish(str(kw.get("api_request_id") or ""), {
            "ts": time.time(), "err": 1,
            "err_type": str(tipo or (type(err).__name__ if err is not None else "")),
            "status": _num(kw.get("status_code")),
        })
    except Exception:  # noqa: BLE001
        pass


_SKILL_TOOLS = {"skill_view", "skill_manage", "skills_list"}


def _tool(**kw: Any) -> None:
    if _off():
        return
    try:
        root, profile = _where()
        name = str(kw.get("tool_name") or "")
        res = kw.get("result")
        size = len(res) if isinstance(res, str) else len(json.dumps(res, ensure_ascii=False, default=str)) \
            if res is not None else 0
        rec = {"ts": time.time(), "profile": profile, "session": str(kw.get("session_id") or ""),
               "task": str(kw.get("task_id") or ""), "tool": name[:80], "ms": _num(kw.get("duration_ms")),
               "chars": size, "status": str(kw.get("status") or "")[:20]}
        if name in _SKILL_TOOLS:
            args = kw.get("args") or {}
            if isinstance(args, dict):
                sk = args.get("name") or args.get("skill") or ""
                if isinstance(sk, str):
                    rec["skill"] = sk[:80]
                ops = args.get("operations")
                if not sk and isinstance(ops, list):
                    rec["skill"] = ",".join(sorted({str(o.get("name"))[:60] for o in ops if isinstance(o, dict)}))[:160]
        _write(root, rec, "tools")
    except Exception:  # noqa: BLE001
        pass


def _aux(**kw: Any) -> None:
    if _off():
        return
    try:
        root, profile = _where()
        usage = kw.get("usage") or {}
        u = {k: v for k, v in usage.items() if _num(v) is not None} if isinstance(usage, dict) else {}
        _write(root, {"ts": _num(kw.get("ended_at")) or time.time(), "profile": profile,
                      "session": str(kw.get("session_id") or ""), "task": str(kw.get("task_id") or ""),
                      "aux_task": str(kw.get("aux_task") or "")[:40], "model": str(kw.get("model") or "")[:80],
                      "provider": str(kw.get("provider") or "")[:40], "dur": _num(kw.get("api_duration")),
                      "usage": u, "err": 1 if kw.get("error") else 0}, "aux")
    except Exception:  # noqa: BLE001
        pass


def register(ctx) -> None:
    ctx.register_hook("pre_api_request", _pre)
    ctx.register_hook("post_api_request", _post)
    ctx.register_hook("api_request_error", _error)
    ctx.register_hook("post_tool_call", _tool)
    ctx.register_hook("post_auxiliary_call", _aux)
