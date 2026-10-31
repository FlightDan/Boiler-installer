"""Explicit, per-report consent for Boiler's desktop maintenance interface.

The failure path accepts controlled machine codes only. It has no exception,
target, receipt, environment, or file attachment parameter. Selected logs are
available only through the separate manual path and use a fixed error code.
"""
from __future__ import annotations

import json
from pathlib import Path
from concurrent.futures import TimeoutError

from .reporter import PreparedReport, Reporter, ReportResult


ENDPOINT = 'https://feedback.devdan.cc/api/v1/reports'
FAILURE_CODES = frozenset({'MAINTENANCE_OPERATION_FAILED', 'MODEL_VERIFICATION_FAILED'})
LOG_FEEDBACK_CODE = 'MAINTENANCE_LOG_FEEDBACK'
LOG_FEEDBACK_TITLE = 'Maintenance log feedback'
LOG_FEEDBACK_MESSAGE = 'A maintenance log was selected for manual feedback.'
MAX_UI_WAIT = 6


def version():
    try:
        value = json.loads(Path(__file__).with_name('release.json').read_text(encoding='utf-8'))['version']
        # The reporter performs the same validation before constructing a body.
        import re
        if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:+-]{1,64}', value):
            return value
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return '0.1.0-preview'


class FeedbackClient:
    def __init__(self, reporter=None):
        self.reporter = reporter if reporter is not None else Reporter(
            ENDPOINT, source='steamos-maintenance', app_version=version(),
            os='steamos', component='maintenance', enabled=True)

    def failure(self, code):
        if code not in FAILURE_CODES:
            raise ValueError('invalid_failure_code')
        return self.reporter.prepare_automatic(code, step='model-maintenance')

    def manual(self, title, description):
        return self.reporter.prepare_feedback(title, description)

    def manual_log(self, logs):
        # The service accepts logs only on its detailed manual-error shape. Keep
        # the code and required title/message fixed; no path accompanies the text.
        return self.reporter.prepare_manual_error(
            LOG_FEEDBACK_CODE, LOG_FEEDBACK_TITLE, LOG_FEEDBACK_MESSAGE, logs)

    @staticmethod
    def preview(report):
        if not isinstance(report, PreparedReport):
            raise ValueError('invalid_report')
        return json.dumps(json.loads(report._body), ensure_ascii=False, indent=2)

    def send(self, report):
        # Only UI code that obtained consent for these exact prepared bytes calls
        # this method. There are no retries, queues, or persistent opt-in state.
        future = self.reporter.send_background(report)
        try:
            return future.result(timeout=MAX_UI_WAIT)
        except TimeoutError:
            # A transport may still finish after this bounded UI wait. Do not
            # claim rejection or retry an unconfirmed report automatically.
            return ReportResult('error', report.report_id, 'response_timeout')
