"""Opt-in, dependency-free Python 3.10+ client for the feedback Worker.

No endpoint is bundled and no installer/maintenance hook is installed. Applications
must explicitly enable reporting after obtaining consent. Automatic reports take
only machine-code constants from the caller's controlled enumerations: never pass
usernames, hostnames, paths, command output or exception messages as codes/steps.
Exception objects contribute only type(error).__name__, never str(error) or a stack.

Automatic reports never accept logs, files, or environment data. A separate,
user-initiated maintenance log report may include only text explicitly selected in
the desktop UI; it is redacted and capped before HTTP, but regex redaction cannot
guarantee that arbitrary logs are anonymous. User-initiated feedback text is cleaned
before it reaches HTTP; the optional, explicitly supplied contact email is preserved
for replies. Do not obtain that address from system/account settings.

Prepare once and reuse the immutable PreparedReport for an explicit retry: its UUID4
and exact JSON bytes remain stable. There are no automatic retries or disk queues.
Use send_background for an installer hook: it returns immediately, permits only one
in-flight background request per Reporter, and uses a daemon thread. send is intended
for an already separate worker or a manual UI action. HTTP has a five-second socket
timeout, redirects and environment proxies are disabled, and responses are not logged.
"""

from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass, field
import json
import re
import threading
from typing import Iterable, Mapping
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import uuid4


MAX_BODY_BYTES = 12 * 1024
MAX_LOG_BYTES = 4096
MAX_LOG_INPUT_BYTES = 96 * 1024
MAX_EVENTS = 20
HTTP_TIMEOUT = 5
_CODE = re.compile(r"[A-Za-z0-9_.:+-]+\Z")
_EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}\Z")
_SOURCES = frozenset({"windows-installer", "steamos-maintenance", "component", "other"})
_SYSTEMS = frozenset({"windows", "steamos", "linux", "other"})
_EVENT_KEYS = frozenset({"level", "code", "step", "error_type", "exit_code"})


def _code(value: str, maximum: int = 64, *, required: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or (value and not _CODE.fullmatch(value)) or (required and not value):
        raise ValueError("invalid_machine_code")
    return value


def _redact(text: str) -> str:
    """Best-effort defense for deliberate feedback, never a free-log sanitizer."""
    substitutions = (
        (r"\x1b\][\s\S]*?(?:\x07|\x1b\\)", "", 0),
        (r"\x1b\[[0-?]*[ -/]*[@-~]", "", 0),
        (r"-----BEGIN [^-\r\n]*PRIVATE KEY-----[\s\S]*?(?:-----END [^-\r\n]*PRIVATE KEY-----|$)", "[private-key]", re.I),
        (r"\b(?:authorization|proxy-authorization|cookie|set-cookie)\s*:[^\r\n]*", "[credential-header]", re.I),
        (r'''(["']?(?:[\w.-]*(?:password|passwd|secret|token|api[_-]?key|private[_-]?key|serial|partuuid|machine[_-]?id|username|hostname)[\w.-]*|pwd|ssid|bssid|mac)["']?\s*[:=：]\s*)[^\r\n]*''', r"\1[redacted]", re.I),
        # Consume the entire value line, including spaces, rather than exposing
        # the remainder of a multiword password or recovery phrase. Chinese
        # prose commonly introduces a value with 是/为 as well as punctuation.
        (r'''(["']?(?:密[码碼]|口令|令牌|密[钥鑰]|私[钥鑰]|用[户戶]名|主[机機]名|序列[号號]|[机機]器(?:[码碼]|[标標][识識])|[邮郵]箱)["']?\s*(?:[:=：]|[为為是])\s*)[^\r\n]*''', r"\1[redacted]", 0),
        (r"--?(?:password|passwd|pwd|token|secret|api[_-]?key)\s+[^\r\n]*", "[credential-argument]", re.I),
        (r"\b(?:Bearer|Basic)\s+[^\s,\"'}]+", "[credential]", re.I),
        (r"\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|AKIA[A-Z0-9]{16})\b", "[token]", 0),
        (r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b", "[token]", 0),
        (r"\b[a-z][a-z0-9+.-]*://[^\s<>\"']+", "[url]", re.I),
        (r"\b[A-Z]:[\\/][^\r\n\"'<>]*", "[local-path]", re.I),
        (r"\\\\[^\r\n\"'<>]+", "[local-path]", 0),
        (r"(?<![\w:])/(?:[^\s/\"'<>]+/)*[^\s\"'<>]*", "[local-path]", 0),
        (r"[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9.-]+\.[A-Z]{2,}", "[email]", re.I),
        (r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "[ip]", 0),
        (r"(?<![\w:])(?:[a-f0-9]{0,4}:){2,}[a-f0-9:.%]*(?!\w)", "[ip]", re.I),
        (r"\b(?:[a-f0-9]{2}[:-]){5}[a-f0-9]{2}\b", "[mac]", re.I),
        (r"[\x00-\x08\x0b-\x1f\x7f-\x9f]", "", 0),
    )
    for pattern, replacement, flags in substitutions:
        text = re.sub(pattern, replacement, text, flags=flags)
    return text.strip()


def _text(value: str, maximum: int) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) > maximum:
        raise ValueError("invalid_feedback_text")
    cleaned = _redact(value)
    if not cleaned or len(cleaned.encode("utf-8")) > maximum:
        raise ValueError("invalid_feedback_text")
    return cleaned


def _log_text(value: str) -> str:
    """Redact an explicitly selected log tail and retain at most 4 KiB of UTF-8."""
    if not isinstance(value, str):
        raise ValueError("invalid_feedback_logs")
    try:
        raw = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("invalid_feedback_logs") from error
    # The UI reads at most 64 KiB from disk. UTF-16 text can expand when decoded
    # to UTF-8, so allow that bounded representation plus room for supplementary
    # code points without accepting arbitrary-sized values here.
    if len(raw) > MAX_LOG_INPUT_BYTES:
        raise ValueError("invalid_feedback_logs")
    cleaned = _redact(value)
    try:
        encoded = cleaned.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("invalid_feedback_logs") from error
    if len(encoded) > MAX_LOG_BYTES:
        encoded = encoded[-MAX_LOG_BYTES:]
        # Truncation starts at an arbitrary byte boundary. Drop only an
        # incomplete leading UTF-8 character; the end remains a full boundary.
        while encoded:
            try:
                cleaned = encoded.decode("utf-8")
                break
            except UnicodeDecodeError as error:
                if error.start != 0:
                    raise ValueError("invalid_feedback_logs") from error
                encoded = encoded[max(error.end, 1):]
        else:
            cleaned = ""
        cleaned = cleaned.strip()
    if not cleaned or len(cleaned.encode("utf-8")) > MAX_LOG_BYTES:
        raise ValueError("invalid_feedback_logs")
    return cleaned


def _events(values: Iterable[Mapping]) -> list[dict]:
    # Require a finite list/tuple so an accidental stream cannot hang preparation.
    if not isinstance(values, (list, tuple)) or len(values) > 1000:
        raise ValueError("invalid_events")
    result, seen = [], set()
    for event in values:
        if not isinstance(event, dict) or set(event) - _EVENT_KEYS or event.get("level") not in {"warning", "error"}:
            raise ValueError("invalid_event")
        item = {"level": event["level"], "code": _code(event.get("code"), required=True)}
        for key in ("step", "error_type"):
            if key in event:
                item[key] = _code(event[key], 96 if key == "error_type" else 64)
        if "exit_code" in event:
            exit_code = event["exit_code"]
            if type(exit_code) is not int or not -2147483648 <= exit_code <= 4294967295:
                raise ValueError("invalid_exit_code")
            item["exit_code"] = exit_code
        identity = json.dumps(item, sort_keys=True, separators=(",", ":"))
        if identity not in seen:
            seen.add(identity)
            if len(result) < MAX_EVENTS:
                result.append(item)
    return result


@dataclass(frozen=True)
class PreparedReport:
    report_id: str
    _body: bytes = field(repr=False)
    _owner: object = field(repr=False, compare=False)


@dataclass(frozen=True)
class ReportResult:
    status: str  # sent, disabled, or error
    report_id: str
    code: str = ""  # Fixed machine codes only, never a response/exception message.


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Reporter:
    def __init__(self, endpoint: str, *, source: str, app_version: str, os: str,
                 component: str = "", enabled: bool = False):
        if not isinstance(endpoint, str) or not re.fullmatch(r"[\x21-\x7e]+", endpoint) or any(c in endpoint for c in "?#\\"):
            raise ValueError("invalid_endpoint")
        try:
            url = urlsplit(endpoint)
            valid = (url.scheme == "https" and url.hostname and url.username is None and url.password is None
                     and url.path == "/api/v1/reports" and not url.query and not url.fragment
                     and re.fullmatch(r"(?:[A-Za-z0-9.-]+|\[[A-Fa-f0-9:]+\])(?::[0-9]+)?", url.netloc)
                     and (url.port is None or 1 <= url.port <= 65535))
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("invalid_endpoint")
        if source not in _SOURCES or os not in _SYSTEMS or type(enabled) is not bool:
            raise ValueError("invalid_configuration")
        self.enabled = enabled
        self._endpoint = endpoint
        self._metadata = dict(source=source, app_version=_code(app_version, required=True), os=os,
                              component=_code(component))
        self._owner = object()
        self._background_lock = threading.Lock()

    def _prepare(self, **fields) -> PreparedReport:
        report_id = str(uuid4())
        payload = dict(schema_version=1, report_id=report_id, consent=True, **self._metadata, **fields)
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(body) > MAX_BODY_BYTES:
            raise ValueError("report_too_large")
        return PreparedReport(report_id, body, self._owner)

    def prepare_automatic(self, error_code: str, *, events: list | tuple = (),
                          error: BaseException | None = None, step: str = "") -> PreparedReport:
        error_code = _code(error_code, required=True)
        items = _events(events)
        if error is not None:
            if not isinstance(error, BaseException):
                raise ValueError("invalid_exception")
            event = {"level": "error", "code": error_code,
                     "error_type": _code(type(error).__name__, 96, required=True)}
            if step:
                event["step"] = _code(step)
            # Retain the primary failure even when callers provided twenty events.
            items = _events([event] + items)
        elif step:
            items = _events([{"level": "error", "code": error_code, "step": _code(step)}] + items)
        return self._prepare(kind="error", mode="automatic", error_code=error_code, events=items)

    def prepare_feedback(self, title: str, message: str, *, contact_email: str = "") -> PreparedReport:
        if not isinstance(contact_email, str) or len(contact_email) > 254 or (contact_email and not _EMAIL.fullmatch(contact_email)):
            raise ValueError("invalid_contact_email")
        fields = dict(kind="feedback", mode="manual", title=_text(title, 240), message=_text(message, 2048))
        if contact_email:
            fields["contact_email"] = contact_email
        return self._prepare(**fields)

    def prepare_manual_error(self, error_code: str, title: str, message: str,
                             logs: str) -> PreparedReport:
        """Prepare a manual error report with an explicitly selected log tail."""
        fields = dict(kind="error", mode="manual", error_code=_code(error_code, required=True),
                      title=_text(title, 240), message=_text(message, 2048), logs=_log_text(logs))
        return self._prepare(**fields)

    def send(self, report: PreparedReport) -> ReportResult:
        if not isinstance(report, PreparedReport) or report._owner is not self._owner:
            return ReportResult("error", "", "invalid_report")
        if self.enabled is not True:
            return ReportResult("disabled", report.report_id)
        try:
            request = Request(self._endpoint, data=report._body, method="POST", headers={
                "Content-Type": "application/json", "Accept": "application/json",
                "User-Agent": "steamosmod-feedback/1",
            })
            opener = build_opener(ProxyHandler({}), _NoRedirect())
            with opener.open(request, timeout=HTTP_TIMEOUT) as response:
                if response.status not in (200, 201):
                    return ReportResult("error", report.report_id, "http_error")
            return ReportResult("sent", report.report_id)
        except HTTPError as error:
            # Do not read or propagate an error response or its reason/URL.
            status = error.code
            try:
                error.close()
            except Exception:
                pass
            return ReportResult("error", report.report_id, "redirect_denied" if 300 <= status < 400 else "http_error")
        except Exception:
            return ReportResult("error", report.report_id, "network_error")

    def send_background(self, report: PreparedReport) -> Future:
        """Immediately return a Future[ReportResult], with no retries or backlog."""
        future = Future()
        if not isinstance(report, PreparedReport) or report._owner is not self._owner:
            future.set_result(ReportResult("error", "", "invalid_report"))
            return future
        if self.enabled is not True:
            future.set_result(ReportResult("disabled", report.report_id))
            return future
        if not self._background_lock.acquire(blocking=False):
            future.set_result(ReportResult("error", report.report_id, "busy"))
            return future

        def run():
            try:
                # Cancellation before startup also avoids sending the report.
                if future.set_running_or_notify_cancel():
                    future.set_result(self.send(report))
            finally:
                self._background_lock.release()

        try:
            threading.Thread(target=run, name="feedback-report", daemon=True).start()
        except Exception:
            self._background_lock.release()
            future.set_result(ReportResult("error", report.report_id, "background_unavailable"))
        return future
