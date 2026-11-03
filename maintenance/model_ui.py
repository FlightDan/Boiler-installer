"""Desktop-user KDE front end for reviewed model maintenance.

Only the fixed maintenance CLI crosses the privilege boundary. Dialog results
are data, never shell commands; the backend revalidates every reviewed plan.
"""
from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

from .feedback import FeedbackClient


KDIALOG = '/usr/bin/kdialog'
MAINTENANCE = '/usr/local/bin/boiler-maintenance'
PKEXEC = '/usr/bin/pkexec'
MAX_SELECTED_LOG_READ = 64 * 1024


class BackendError(RuntimeError):
    pass


def language(value=None):
    value = value or os.environ.get('LC_ALL') or os.environ.get('LC_MESSAGES') or os.environ.get('LANG', '')
    return 'zh-CN' if value.lower().startswith('zh') else 'en-US'


class KDialog:
    """Small native-dialog adapter; injected runners keep interaction testable."""

    def __init__(self, lang, runner=subprocess.run):
        self.lang, self.runner = lang, runner

    def text(self, zh, en):
        return zh if self.lang == 'zh-CN' else en

    def _run(self, *args):
        result = self.runner([KDIALOG, '--title', 'Boiler ' + self.text('维护工具', 'Maintenance'),
                              '--ok-label', self.text('确定', 'OK'), '--cancel-label', self.text('取消', 'Cancel'),
                              *args], capture_output=True, text=True, check=False)
        if result.returncode not in (0, 1):
            raise OSError(result.stderr.strip() or 'KDialog could not display the interface')
        return result

    @staticmethod
    def _rich(value):
        return '<qt><p>' + html.escape(str(value)).replace('\n', '<br/>') + '</p></qt>'

    def menu(self, text, rows):
        args = ['--menu', self._rich(text)]
        for identity, label in rows:
            args.extend([identity, label])
        result = self._run(*args)
        if result.returncode:
            return None
        value = result.stdout.strip()
        if value not in {row[0] for row in rows}:
            raise ValueError('Unexpected dialog selection')
        return value

    def checklist(self, text, rows):
        args = ['--separate-output', '--checklist', self._rich(text)]
        for identity, label, selected in rows:
            args.extend([identity, label, 'on' if selected else 'off'])
        result = self._run(*args)
        if result.returncode:
            return None
        values = result.stdout.splitlines()
        if len(values) != len(set(values)) or not set(values) <= {row[0] for row in rows}:
            raise ValueError('Unexpected dialog selection')
        return set(values)

    def confirm(self, text):
        # kdialog has no --defaultno option. A native menu supports an explicit
        # default cancellation, and only the exact Continue choice grants consent.
        result = self._run('--default', 'cancel', '--menu', self._rich(text),
                           'cancel', self.text('取消', 'Cancel'), 'continue', self.text('继续', 'Continue'))
        return result.returncode == 0 and result.stdout.strip() == 'continue'

    def message(self, text, *, error=False):
        self._run('--error' if error else '--msgbox', self._rich(text))

    def input(self, text, *, multiline=False):
        result = self._run('--textinputbox' if multiline else '--inputbox', self._rich(text), '')
        return result.stdout.rstrip('\n') if result.returncode == 0 else None

    def open_file(self):
        result = self._run('--getopenfilename')
        return result.stdout.rstrip('\n') if result.returncode == 0 else None

    def details(self, text):
        # QTextEdit.append auto-detects rich text, so escape all source content.
        with tempfile.TemporaryDirectory(prefix='boiler-dialog-') as directory:
            os.chmod(directory, 0o700)
            path = Path(directory) / 'details.txt'
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as output:
                for line in text.splitlines():
                    output.write('<p style="white-space:pre-wrap;margin:0">' + html.escape(line) + '</p>\n')
            return self._run('--textbox', str(path), '800', '540').returncode == 0


def _read_limited(fd, maximum):
    chunks = []
    remaining = maximum
    while remaining:
        chunk = os.read(fd, min(8192, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b''.join(chunks)


def _decode_log_window(raw, offset, encoding):
    if encoding == 'utf-8':
        if offset == 0 and raw.startswith(b'\xef\xbb\xbf'):
            raw = raw[3:]
        skipped = 0
        while True:
            try:
                return raw.decode('utf-8')
            except UnicodeDecodeError as error:
                # A bounded tail can begin in the middle of a UTF-8 character.
                if offset == 0 or error.start != 0 or skipped >= 3:
                    raise ValueError('invalid_log_encoding') from error
                discarded = max(error.end, 1)
                raw = raw[discarded:]
                offset += discarded
                skipped += discarded
    if offset == 0:
        raw = raw[2:]
    elif offset % 2:
        raw = raw[1:]
        offset += 1
    skipped = 0
    while True:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError as error:
            # A UTF-16 window can start on the low half of a surrogate pair.
            if offset == 0 or error.start != 0 or skipped >= 2:
                raise ValueError('invalid_log_encoding') from error
            raw = raw[2:]
            offset += 2
            skipped += 2


def read_selected_log(path):
    """Read only a bounded tail from a user-selected ordinary UTF text file."""
    if not isinstance(path, (str, os.PathLike)) or not os.fspath(path):
        raise ValueError('invalid_log_file')
    path = os.fspath(path)
    if not stat.S_ISREG(os.lstat(path).st_mode):
        raise ValueError('invalid_log_file')
    flags = (os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NONBLOCK', 0)
             | getattr(os, 'O_NOFOLLOW', 0))
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size <= 0:
            raise ValueError('invalid_log_file')
        size = info.st_size
        head = _read_limited(fd, min(4, size))
        if size <= MAX_SELECTED_LOG_READ:
            raw = head + _read_limited(fd, size - len(head))
            offset = 0
        else:
            # Include the four-byte encoding probe within the 64 KiB read cap.
            tail_size = MAX_SELECTED_LOG_READ - len(head)
            offset = size - tail_size
            os.lseek(fd, offset, os.SEEK_SET)
            raw = _read_limited(fd, tail_size)
        if head.startswith(b'\xef\xbb\xbf'):
            encoding = 'utf-8'
        elif head.startswith(b'\xff\xfe'):
            encoding = 'utf-16-le'
        elif head.startswith(b'\xfe\xff'):
            encoding = 'utf-16-be'
        else:
            encoding = 'utf-8'
        text = _decode_log_window(raw, offset, encoding)
        if '\x00' in text or not text.strip():
            raise ValueError('invalid_log_text')
        return text
    finally:
        os.close(fd)


class Backend:
    def __init__(self, runner=subprocess.run):
        self.runner = runner

    def call(self, *args):
        command = [MAINTENANCE, '--root', '/', 'models', *args]
        if args[0] != 'list':
            command.insert(0, PKEXEC)
        return self._run(command)

    def ssd(self, *args):
        return self._run([PKEXEC, MAINTENANCE, '--root', '/', 'ssd-boot', *args])

    def _run(self, command):
        result = self.runner(command, capture_output=True, text=True, check=False)
        try:
            envelope = json.loads(result.stdout)
        except (TypeError, ValueError) as error:
            raise BackendError(result.stderr.strip() or result.stdout.strip() or
                               'Maintenance did not return a JSON result.') from error
        if result.returncode or envelope.get('ok') is not True:
            detail = envelope.get('error', envelope)
            if set(envelope) - {'ok', 'error'}:
                detail = envelope
            raise BackendError(detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False))
        if not isinstance(envelope.get('result'), dict):
            raise BackendError('Maintenance returned an invalid result.')
        return envelope['result']


class ModelUI:
    def __init__(self, dialog, backend, *, lang='en-US', offline=False, feedback=None):
        self.dialog, self.backend = dialog, backend
        self.lang, self.offline = lang, offline
        self.trusted = False
        self.feedback = feedback if feedback is not None else FeedbackClient()

    def tr(self, zh, en):
        return zh if self.lang == 'zh-CN' else en

    def compatibility_notice(self):
        return self.tr('所选机型包可能与目标设备不兼容；由您决定是否继续维护。',
                       'This device package may be incompatible with the target device. You can choose whether to continue maintenance.')

    def label(self, value):
        if isinstance(value, dict):
            return str(value.get('zh' if self.lang == 'zh-CN' else 'en', value.get('en', '')))
        return str(value or '')

    def guide(self, model):
        lines = [self.label(model['name']), model.get('guide_url', ''), '',
                 self.tr('以下为适配指南，不能证明本机运行验收通过。',
                         'This adaptation guide does not establish runtime validation on this device.')]
        for item in model.get('components', []):
            lines.extend(['', self.label(item['name']) + ' [' + item['id'] + ']',
                          self.label(item.get('notes', ''))])
        self.dialog.details('\n'.join(lines))

    def run(self):
        try:
            catalog = self.backend.call('list', *(['--offline'] if self.offline else []))
            models = {model['id']: model for model in catalog['models']}
            while True:
                source = catalog.get('source', {})
                text = self.tr('选择机型。适配指南与可执行测试包分别显示。',
                               'Choose a model. Guides and executable test packages are shown separately.')
                text += '\n' + self.tr('来源：', 'Source: ') + str(source.get('repository', ''))
                text += '\n' + self.tr('固定提交：', 'Pinned commit: ') + str(source.get('commit', ''))
                if catalog.get('offline'):
                    text += '\n' + self.tr('离线：使用已固定的本地指南。', 'Offline: using the pinned local guides.')
                rows = [(key, self.label(model['name'])) for key, model in models.items()]
                rows.append(('ssd', self.tr('可选 SSD 修复', 'Optional SSD repair')))
                rows.append(('feedback', self.tr('手动反馈', 'Send feedback')))
                identity = self.dialog.menu(text, rows)
                if identity is None:
                    return 0
                if identity == 'feedback':
                    self.manual_feedback()
                    continue
                if identity == 'ssd':
                    self.ssd_actions()
                    continue
                self.model(models[identity], catalog)
        except (BackendError, OSError, ValueError, KeyError, TypeError) as error:
            detail = str(error)
            if 'replacement_requires_restore' in detail:
                detail = self.tr('更改现有适配前，必须先使用“恢复已安装的适配”完整恢复当前适配，再重新安装。\n',
                                 'Before changing this adaptation, use Restore installed adaptations to fully restore the current adaptation, then install again.\n') + detail
            self.dialog.message(self.tr('操作未完成：\n', 'The operation did not complete:\n') + detail, error=True)
            self.offer_failure('MAINTENANCE_OPERATION_FAILED')
            self.failure_feedback()
            return 1

    def ssd_actions(self):
        status = self.backend.ssd('inspect')
        if not status.get('available'):
            self.dialog.message(self.tr('当前 SSD 不适用此修复。', 'This repair is unavailable for the current SSD.'))
            return
        pending, installed = status.get('pending'), status.get('installed')
        actions = []
        if pending:
            actions.append(('recover', self.tr('恢复中断的事务', 'Recover interrupted transaction')))
        else:
            actions.append(('apply', self.tr('选择并安装 SSD 修复', 'Select and install SSD repair')))
            if installed:
                actions.extend([('verify', self.tr('验证 SSD 修复', 'Verify SSD repair')),
                                ('restore', self.tr('恢复原始配置', 'Restore original configuration'))])
        actions.append(('back', self.tr('返回', 'Back')))
        action = self.dialog.menu(self.tr(
            '此可选修复仅针对 Predator SSD GM7 1TB。它会修改当前槽的 EFI 启动配置和系统持久化配置。',
            'This optional repair is only for the Predator SSD GM7 1TB. It changes the current slot EFI boot configuration and its persistence drop-in.'), actions)
        if action in (None, 'back'):
            return
        plan = self.backend.ssd('plan', action)
        digest = plan.get('review_sha256')
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('Maintenance returned an invalid SSD review hash')
        target = plan.get('target', {})
        lines = [self.tr('确认此次 SSD 操作', 'Review this SSD operation'), dict(actions)[action],
                 self.tr('活动槽：', 'Slot: ') + str(target.get('slot', '')),
                 self.tr('根分区 PARTUUID：', 'Root PARTUUID: ') + str(target.get('root_partuuid', '')),
                 self.tr('EFI 分区 PARTUUID：', 'EFI PARTUUID: ') + str(target.get('esp_partuuid', '')),
                 self.tr('涉及文件：', 'Files: ') + 'efi/EFI/steamos/grub.cfg, etc/default/grub.d/oxp3-nvme.cfg',
                 self.tr('实机效果尚未验证。安装或恢复后需重启才能应用启动参数。',
                         'Physical-device behavior has not been validated. Restart after installation or restoration to apply boot parameters.'),
                 self.tr('审阅哈希：', 'Review hash: ') + digest]
        if self.dialog.details('\n'.join(lines)) is False:
            return
        if not self.dialog.confirm(self.tr('执行刚刚审阅的 SSD 操作？', 'Execute the SSD operation just reviewed?')):
            return
        with tempfile.TemporaryDirectory(prefix='boiler-ssd-plan-') as directory:
            os.chmod(directory, 0o700)
            path = Path(directory)/'plan.json'
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as output:
                json.dump(plan, output, ensure_ascii=False, indent=2)
                output.flush()
                os.fsync(output.fileno())
            result = self.backend.ssd('execute', str(path), '--reviewed-sha256', digest)
        if result.get('ok') is not True:
            raise BackendError('SSD repair did not complete')
        statuses = {
            'configured': self.tr('已配置启动参数。', 'Boot configuration updated.'),
            'already_configured': self.tr('启动参数已经配置；保留原始恢复记录。', 'Boot configuration was already set; the original recovery record is preserved.'),
            'verified': self.tr('配置文件验证通过。', 'Configuration files verified.'),
            'restored': self.tr('已恢复原始配置。', 'Original configuration restored.'),
            'recovered': self.tr('已恢复中断事务。', 'Interrupted transaction recovered.'),
        }
        states = {
            'active': self.tr('当前内核 NVMe 参数已启用。', 'The running kernel NVMe parameter is enabled.'),
            'inactive': self.tr('当前内核 NVMe 参数未启用；可能需要重启。', 'The running kernel NVMe parameter is disabled; a restart may be required.'),
            'unknown': self.tr('无法确定当前内核 NVMe 参数；可能需要重启。', 'The running kernel NVMe parameter is unknown; a restart may be required.'),
            'first_boot_required': self.tr('需首次启动后才能观察内核参数。', 'The kernel parameter can be observed after the first boot.'),
        }
        status = statuses.get(result.get('status'), self.tr('SSD 操作完成。', 'SSD operation completed.'))
        activation = states.get(result.get('activation'), states['unknown'])
        self.dialog.message(status + '\n' + activation + '\n' + self.tr(
            '此结果不能证明设备上的休眠或恢复效果。', 'This result does not establish suspend or resume behavior on the device.'))

    def failure_feedback(self):
        # Available even when the catalog could not be loaded. Reporting never
        # causes another maintenance request or changes the failure exit code.
        try:
            choice = self.dialog.menu(self.tr('维护操作未完成，也可以手动填写反馈。',
                                               'Maintenance did not complete. You can also write feedback.'), [
                ('feedback', self.tr('手动反馈', 'Send feedback')),
                ('close', self.tr('关闭', 'Close'))])
            if choice == 'feedback':
                self.manual_feedback()
        except Exception:
            pass

    def report_result(self, result):
        if result.code == 'response_timeout':
            self.dialog.message(self.tr('等待发送结果超时，发送结果尚未确认；不会自动重试。此次维护操作的结果未改变。',
                                        'Timed out waiting for confirmation. Sending is unconfirmed; no automatic retry will occur. '
                                        'The maintenance result is unchanged.'), error=True)
            return
        sent = result.status == 'sent'
        self.dialog.message(self.tr('反馈已发送。', 'Report sent.') if sent else self.tr(
            '反馈未发送。可稍后通过“手动反馈”重试；此次维护操作的结果未改变。',
            'Report was not sent. Try Send feedback later; the maintenance result is unchanged.'), error=not sent)

    def offer_failure(self, code):
        try:
            report = self.feedback.failure(code)
            if not self.dialog.confirm(self.tr(
                    '是否发送本次操作失败的错误报告？每次发送都需要您同意。\n'
                    '仅发送固定错误码、维护器版本、来源、SteamOS 平台标识和随机报告编号；不发送原始错误、日志、路径或设备信息。\n'
                    '接收方：Boiler 反馈服务（Cloudflare）。发送等待最多 6 秒。',
                    'Send a report for this failed operation? Each report requires your consent.\n'
                    'Only fixed error codes, maintenance version, source, SteamOS platform label and a random report ID are sent; '
                    'original errors, logs, paths and device information are excluded.\n'
                    'Recipient: Boiler feedback service (Cloudflare). Sending waits at most 6 seconds.')):
                return
            self.report_result(self.feedback.send(report))
        except Exception:
            # The original failure was already displayed. Reporting must never
            # replace it, change its return code, or trigger another report.
            self.reporting_unavailable()

    def reporting_unavailable(self):
        try:
            self.dialog.message(self.tr('反馈暂时不可用。此次维护操作的结果未改变。',
                                        'Reporting is unavailable. The maintenance result is unchanged.'), error=True)
        except Exception:
            pass

    def manual_feedback(self):
        try:
            choice = self.dialog.menu(self.tr('选择反馈方式', 'Choose a feedback method'), [
                ('log', self.tr('选择日志文件', 'Select a log file')),
                ('write', self.tr('填写反馈', 'Write feedback'))])
            if choice not in ('log', 'write'):
                return
            has_logs = choice == 'log'
            if has_logs:
                self.dialog.message(self.tr(
                    '只读取您选择文件的末尾最多 64 KiB；仅把脱敏后的末尾最多 4 KiB 放入反馈。文件路径不会上传，发送前会预览实际内容。',
                    'Only up to the final 64 KiB of the selected file is read; only its redacted final 4 KiB is included. '
                    'The file path is not uploaded, and the exact report is previewed before sending.'))
                path = self.dialog.open_file()
                if path is None:
                    return
                try:
                    logs = read_selected_log(path)
                    report = self.feedback.manual_log(logs)
                except (OSError, UnicodeError, ValueError):
                    self.dialog.message(self.tr(
                        '无法读取此日志。请选择非空的 UTF-8 或带 BOM 的 UTF-16 普通文本文件。',
                        'This log could not be read. Select a nonempty regular UTF-8 or BOM-marked UTF-16 text file.'),
                        error=True)
                    return
            else:
                title = self.dialog.input(self.tr('反馈标题（最多 240 UTF-8 字节）',
                                                 'Feedback title (up to 240 UTF-8 bytes)'))
                if title is None:
                    return
                description = self.dialog.input(self.tr('请描述问题、复现步骤或建议（最多 2048 UTF-8 字节）。',
                                                        'Describe the problem, steps or suggestion (up to 2048 UTF-8 bytes).'),
                                                multiline=True)
                if description is None:
                    return
                try:
                    report = self.feedback.manual(title, description)
                except ValueError:
                    self.dialog.message(self.tr('标题和描述不能为空或超出限制，请重新填写。',
                                                'Provide a nonempty title and description within the limits.'), error=True)
                    return
            if self.dialog.details(self.tr(
                    ('以下为脱敏后的实际发送内容。日志字段只包含脱敏后末尾最多 4 KiB；请检查是否仍有敏感信息。'
                     '文件路径不会上传；取消可放弃发送。\n\n') if has_logs else
                    ('以下为脱敏后的实际发送内容。请检查是否仍包含敏感信息；取消可返回重新填写。\n'
                     '不会附加日志或文件。\n\n'),
                    ('These are the exact redacted contents to send. The logs field contains only the redacted final 4 KiB. '
                     'Check for remaining sensitive information; the file path is not uploaded. Cancel to discard.\n\n') if has_logs else
                    ('These are the exact redacted contents to send. Check for remaining sensitive information; '
                     'cancel to return and rewrite.\nNo logs or files are attached.\n\n')) +
                    self.feedback.preview(report)) is False:
                return
            if not self.dialog.confirm(self.tr('将刚刚预览的反馈发送给 Boiler 反馈服务（Cloudflare）？发送等待最多 6 秒。',
                                                'Send the feedback just previewed to the Boiler feedback service (Cloudflare)? '
                                                'Sending waits at most 6 seconds.')):
                return
            self.report_result(self.feedback.send(report))
        except Exception:
            self.reporting_unavailable()

    def model(self, model, catalog):
        # Reading private installed journals and target evidence also needs root.
        bundle = next((item for item in catalog.get('bundled_packages', []) if item['id'] == model['id']), None)
        while True:
            choice = self.dialog.menu(self.label(model['name']) + '\n' + self.compatibility_notice(), [
                ('options', self.tr('查看选项与维护操作', 'Options and maintenance actions')),
                ('shared', self.tr('机型附加修复', 'Additional device repairs')),
                *([('keyboard', self.tr('屏幕键盘配置与恢复', 'Keyboard configuration and recovery'))] if model['id']=='device:x2-mini' else []),
                ('guide', self.tr('查看完整适配指南', 'Read the full adaptation guide')),
                ('back', self.tr('返回机型列表', 'Back to models'))])
            if choice in (None, 'back'):
                return
            if choice == 'guide':
                self.guide(model)
                continue
            needs_trust = choice in ('shared','keyboard') or bundle is not None or model['id'] == 'device:oxp3'
            if needs_trust and not self.trusted:
                if not self.dialog.confirm(self.tr(
                        '此包为本地测试包，尚未公开发布，也不代表实机运行验收通过。\n仅在本次会话信任该包并读取本机维护状态？',
                        'This is an unpublished local test package. It does not establish runtime validation.\nTrust this package for this session and read this device’s maintenance state?')
                        + '\n' + self.label(model['name'])
                        + ('\n' + str(bundle.get('version', '')) if bundle else '')):
                    return
                self.trusted = True
            flags = ['--trust-local-test'] if needs_trust and self.trusted else []
            options = self.backend.call({'shared':'shared-options','keyboard':'keyboard-options'}.get(choice,'options'), model['id'], *flags)
            self.actions(model, options)

    def unavailable(self, options, model):
        rows = [item for item in options['capabilities'] if not item.get('available')]
        capability_ids = {item['id'] for item in options['capabilities']}
        # A guide entry must remain visible even if it has no payload mapping.
        for item in model.get('components', []):
            if options.get('dynamic_adapter') == 'keyboard' and item['id'] != 'x2-mini:keyboard': continue
            if item['id'] not in capability_ids:
                rows.append(dict(item, reason=self.tr('仅指南项目，没有可执行选项。',
                                                      'Guide only; no executable option.') +
                                 '\n' + self.label(item.get('notes', ''))))
        if rows:
            return self.dialog.details(self.tr('不可选的适配项目（保留指南项目）：\n\n',
                                               'Unavailable adaptation items (guide entries are retained):\n\n') +
                                       '\n\n'.join(self.label(item['name']) + ' [' + item['id'] + ']\n' +
                                                    self.label(item.get('reason', '')) for item in rows))
        return True

    def actions(self, model, options):
        if self.unavailable(options, model) is False:
            return
        if not options.get('executable'):
            self.dialog.message(self.tr('此机型仅提供适配指南，无可执行维护包。',
                                        'This model has an adaptation guide and no executable maintenance package.') +
                                '\n' + '\n'.join(self.label(value) for value in options.get('reasons', [])))
            return
        current, pending = options.get('current'), options.get('pending')
        legacy_current = options.get('current_source') == 'legacy-installer'
        owned_current = current is not None and not legacy_current
        actions = []
        if not pending and options.get('install_available', True):
            actions.append(('apply', self.tr('安装 / 更改所选项目', 'Install / change selected items')))
        if owned_current and not pending:
            actions.append(('verify', self.tr('验证已安装项目', 'Verify installed items')))
        if pending and options.get('dynamic_adapter') == 'keyboard':
            if pending.get('recover_available') is True:
                actions.append(('recover',self.tr('恢复中断的键盘事务','Recover interrupted keyboard transaction')))
        elif owned_current or pending:
            actions.append(('restore', self.tr('恢复已安装的适配', 'Restore installed adaptations')))
        actions.extend([('guide', self.tr('查看适配指南', 'Read adaptation guide')),
                        ('back', self.tr('返回', 'Back'))])
        text = self.label(model['name']) + '\n' + self.tr(
            '安装作用于当前活动系统槽；共享 /home 中的插件和配置可能同时影响另一系统槽。',
            'Installation targets the active OS slot; plugins and configuration in shared /home may also affect the other OS slot.')
        text += '\n' + self.compatibility_notice()
        if pending:
            text += '\n' + (self.label(pending.get('reason','')) if options.get('dynamic_adapter')=='keyboard' else self.tr('有中断的维护操作。请先选择恢复。', 'Maintenance was interrupted. Choose Restore first.'))
        elif legacy_current:
            text += '\n' + self.tr('安装器已有配置，安装后才纳入 Boiler 维护。',
                                    'Installer configuration exists. Install it to bring it under Boiler maintenance.')
        elif current is None:
            text += '\n' + self.tr('尚无已安装状态；安装后可验证或恢复。',
                                    'No installed state yet. Verify and Restore become available after installation.')
        elif options.get('replacement_requires_restore'):
            text += '\n' + self.tr('更改现有适配前，必须先完整恢复当前适配。',
                                    'Changing this adaptation requires fully restoring the current adaptation first.')
        action = self.dialog.menu(text, actions)
        if action in (None, 'back'):
            return
        if action == 'guide':
            self.guide(model)
            return
        selection = self.choose(options) if action == 'apply' else current
        if action == 'apply' and selection is None:
            return
        shared = options.get('shared_adaptations') is True
        if action == 'apply' and not shared:
            consent = self.dialog.checklist(self.tr(
                '兼容性确认：安装旧版适配可能需要保持 SteamOS 系统更新暂停。取消勾选即不继续。',
                'Compatibility consent: this legacy adaptation may require keeping SteamOS updates on hold. Leave unchecked to stop.'),
                [('update-hold', self.tr('我同意保持系统更新暂停，并已阅读适配限制。',
                                        'I agree to keep system updates on hold and have read the adaptation limits.'), False)])
            if consent != {'update-hold'}:
                return
        if shared and action in ('verify','restore'):
            installed = list((current or {}).get('repairs', []))
            interrupted = (pending or {}).get('component') if (pending or {}).get('source') == 'ssd-provider' else None
            if interrupted:
                selected_shared = [interrupted]
            elif len(installed) > 1:
                names = {item['id']: self.label(item['name']) for item in options['capabilities']}
                chosen = self.dialog.menu(self.tr('选择要维护的已安装项目', 'Choose the installed item to maintain'),
                                          [(identity, names.get(identity, identity)) for identity in installed])
                if chosen is None: return
                if chosen not in installed: raise ValueError('Unknown installed repair selection')
                selected_shared = [chosen]
            else:
                selected_shared = installed
        dynamic = options.get('dynamic_adapter') == 'keyboard'
        args = ['keyboard-plan' if dynamic else 'shared-plan' if shared else 'plan', model['id'], '--action', action, '--trust-local-test']
        if not shared:
            args.extend(['--installer-language', self.lang, '--kde-language', 'system'])
        if action == 'apply' and not shared:
            args.append('--acknowledge-update-hold')
        if action == 'apply':
            for category, flag in (('repairs', '--repair'), ('plugins', '--plugin')):
                for identity in sorted(selection[category]):
                    args.extend([flag, identity])
        if shared and action in ('verify','restore'):
            for identity in selected_shared: args.extend(['--repair', identity])
        plan = self.backend.call(*args)
        digest = plan.get('review_sha256')
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('Maintenance returned an invalid review hash')
        review = self.review(model, options, plan, dict(actions)[action])
        # Scrollable details precede a default-Cancel confirmation.
        if self.dialog.details(review) is False:
            return
        if not self.dialog.confirm(self.tr(
                '执行刚刚审阅的计划？\n当前活动系统槽与共享 /home 的影响范围以计划为准。\n本地测试包不代表实机运行验收通过。',
                'Execute the plan just reviewed?\nThe plan specifies effects on the active OS slot and shared /home.\nA local test package does not establish runtime validation.')):
            return
        with tempfile.TemporaryDirectory(prefix='boiler-reviewed-plan-') as directory:
            os.chmod(directory, 0o700)
            path = Path(directory) / 'plan.json'
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as output:
                json.dump(plan, output, ensure_ascii=False, indent=2)
                output.flush()
                os.fsync(output.fileno())
            receipt = self.backend.call('keyboard-execute' if dynamic else 'shared-execute' if shared else 'execute', str(path), '--reviewed-sha256', digest, '--trust-local-test')
        self.receipt(receipt, action)

    def receipt(self, receipt, action):
        result = receipt.get('result', {})
        result = result if isinstance(result, dict) else {}
        failed = action == 'verify' and (receipt.get('status') == 'verification-failed'
                                         or result.get('verified') is not True)
        text = self.tr('验证未通过。已安装文件存在更改或未通过检查。',
                       'Verification failed. Installed files changed or did not pass inspection.') if failed else self.tr(
                           '操作完成。', 'Operation completed.')
        text += '\n' + self.tr('回执：', 'Receipt: ') + str(receipt.get('receipt_path', ''))
        log = receipt.get('log', result.get('log', result.get('log_path', '')))
        if log:
            text += '\n' + self.tr('日志：', 'Log: ') + str(log)
        if failed:
            modified = result.get('modified', result.get('modified_paths', []))
            if modified:
                text += '\n\n' + self.tr('已更改的文件：\n', 'Changed files:\n')
                paths = modified if isinstance(modified, list) else [modified]
                text += '\n'.join(str(item.get('path', item)) if isinstance(item, dict) else str(item) for item in paths)
            reason = result.get('reason')
            if reason:
                text += '\n' + self.label(reason)
        if any(row.get('user_edits_preserved') for row in result.get('components',[]) if isinstance(row,dict)):
            text += '\n' + self.tr('已保留用户修改的完整配置文件；键盘设置可能仍保留，未宣称配置已恢复。',
                'The edited configuration was preserved in full. Keyboard settings may remain; configuration restoration is not claimed.')
        if receipt.get('restart_required') is True:
            text += '\n' + self.tr('请重启以应用更改。', 'Restart to apply the changes.')
        elif receipt.get('restart_required') is False:
            text += '\n' + self.tr('此次操作无需重启。', 'No restart is required for this operation.')
        self.dialog.message(text, error=failed)
        if failed:
            self.offer_failure('MODEL_VERIFICATION_FAILED')

    def review(self, model, options, plan, action_label):
        lines = [self.tr('确认此次操作', 'Review this operation'), self.label(model['name']), action_label]
        version = plan.get('package', {}).get('version')
        if version:
            lines.append(self.tr('测试包版本：', 'Test package version: ') + str(version))
        scope = plan.get('scope')
        lines.append(self.tr('范围：当前活动系统槽；共享 /home 的插件和配置也可能影响另一系统槽。',
                             'Scope: active OS slot; plugins and configuration in shared /home may also affect the other slot.')
                     if scope == 'active-root-and-shared-home' else self.tr('范围：', 'Scope: ') + self.label(scope))
        summary = plan.get('summary', {})
        summary = summary if isinstance(summary, dict) else {'status': summary}
        target = plan.get('target', {})
        slot = target.get('label') or target.get('slot') or target.get('id') or summary.get('slot')
        if slot is not None:
            lines.append(self.tr('活动槽：', 'Active slot: ') + str(slot))
        if target.get('partuuid'):
            lines.append(self.tr('分区 PARTUUID：', 'Partition PARTUUID: ') + str(target['partuuid']))
        if 'file_count' not in summary and 'files' in summary:
            summary = dict(summary, file_count=summary['files'])
        capabilities = {item['id']: item for item in options['capabilities']}
        def names(values):
            if isinstance(values, dict):
                values = list(values.get('repairs', [])) + list(values.get('plugins', []))
            if not isinstance(values, (list, tuple, set)):
                return self.label(values)
            return ', '.join(self.label(capabilities[value]['name']) if value in capabilities else str(value)
                             for value in values)
        selection = plan.get('selection')
        lines.extend(['', self.tr('项目（包括必要依赖）：', 'Items (including required dependencies):'),
                      names(selection) if selection else self.tr('恢复中断操作记录中的项目。',
                                                                 'Recover items recorded by the interrupted operation.')])
        for field, zh, en in (('added', '新增：', 'Add: '), ('removed', '移除：', 'Remove: '),
                              ('file_count', '涉及文件数：', 'Files affected: '),
                              ('reason', '说明：', 'Details: '), ('status', '状态：', 'Status: ')):
            if field in summary:
                lines.append(self.tr(zh, en) + names(summary[field]))
        for warning in plan.get('warnings', []):
            lines.append(self.tr('注意：', 'Note: ') + self.label(warning))
        return '\n'.join(lines)

    def choose(self, options):
        capabilities = {item['id']: item for item in options['capabilities']}
        current = options.get('current')
        if current is not None and not isinstance(current, dict):
            raise ValueError('Maintenance returned an invalid current selection')
        selected = set()
        for category in ('repairs', 'plugins'):
            rows = [item for item in capabilities.values() if item['category'] == category and item.get('available')]
            if not rows:
                continue
            defaults = set(current.get(category, [])) if current is not None else {
                item['id'] for item in rows if item.get('default')}
            result = self.dialog.checklist(self.tr('选择修复项目', 'Choose repairs') if category == 'repairs'
                                           else self.tr('选择插件', 'Choose plugins'),
                                           [(item['id'], self.label(item['name']), item['id'] in defaults) for item in rows])
            if result is None:
                return None
            allowed = {item['id'] for item in rows}
            if not result <= allowed:
                raise ValueError('Unavailable option was selected')
            selected.update(result)
        pending = list(selected)
        while pending:
            identity = pending.pop()
            for dependency in capabilities[identity].get('dependencies', []):
                if dependency not in capabilities or not capabilities[dependency].get('available'):
                    raise ValueError('A required dependency is unavailable: ' + dependency)
                if dependency not in selected:
                    selected.add(dependency)
                    pending.append(dependency)
        return {category: {identity for identity in selected if capabilities[identity]['category'] == category}
                for category in ('repairs', 'plugins')}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Boiler native KDE model maintenance')
    parser.add_argument('--language', choices=('zh-CN', 'en-US'))
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--feedback', action='store_true', help='Open manual feedback without loading the model catalog')
    args = parser.parse_args(argv)
    lang = language(args.language)
    if not Path(KDIALOG).is_file():
        print('未找到 /usr/bin/kdialog。请在 KDE 桌面使用维护工具，或在终端运行：\n'
              'Missing /usr/bin/kdialog. Use a KDE desktop, or run in a terminal:\n'
              '/usr/local/bin/boiler-maintenance --root / models list --offline', file=sys.stderr)
        return 1
    if hasattr(os, 'geteuid') and os.geteuid() == 0:
        print('请以桌面用户运行 / Run this interface as the desktop user.', file=sys.stderr)
        return 1
    ui = ModelUI(KDialog(lang), Backend(), lang=lang, offline=args.offline)
    if args.feedback:
        ui.manual_feedback()
        return 0
    return ui.run()


if __name__ == '__main__':
    sys.exit(main())
