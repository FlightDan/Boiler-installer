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
import subprocess
import sys
import tempfile


KDIALOG = '/usr/bin/kdialog'
MAINTENANCE = '/usr/local/bin/boiler-maintenance'
PKEXEC = '/usr/bin/pkexec'


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


class Backend:
    def __init__(self, runner=subprocess.run):
        self.runner = runner

    def call(self, *args):
        command = [MAINTENANCE, '--root', '/', 'models', *args]
        if args[0] != 'list':
            command.insert(0, PKEXEC)
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
    def __init__(self, dialog, backend, *, lang='en-US', offline=False):
        self.dialog, self.backend = dialog, backend
        self.lang, self.offline = lang, offline
        self.trusted = False

    def tr(self, zh, en):
        return zh if self.lang == 'zh-CN' else en

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
                identity = self.dialog.menu(text, [(key, self.label(model['name'])) for key, model in models.items()])
                if identity is None:
                    return 0
                self.model(models[identity], catalog)
        except (BackendError, OSError, ValueError, KeyError, TypeError) as error:
            detail = str(error)
            if 'replacement_requires_restore' in detail:
                detail = self.tr('更改现有适配前，必须先使用“恢复已安装的适配”完整恢复当前适配，再重新安装。\n',
                                 'Before changing this adaptation, use Restore installed adaptations to fully restore the current adaptation, then install again.\n') + detail
            self.dialog.message(self.tr('操作未完成：\n', 'The operation did not complete:\n') + detail, error=True)
            return 1

    def model(self, model, catalog):
        # Reading private installed journals and target evidence also needs root.
        bundle = next((item for item in catalog.get('bundled_packages', []) if item['id'] == model['id']), None)
        while True:
            choice = self.dialog.menu(self.label(model['name']), [
                ('options', self.tr('查看选项与维护操作', 'Options and maintenance actions')),
                ('shared', self.tr('Battery 0.2 / 麦克风修复', 'Battery 0.2 / microphone repairs')),
                ('guide', self.tr('查看完整适配指南', 'Read the full adaptation guide')),
                ('back', self.tr('返回机型列表', 'Back to models'))])
            if choice in (None, 'back'):
                return
            if choice == 'guide':
                self.guide(model)
                continue
            needs_trust = choice == 'shared' or bundle is not None or model['id'] == 'device:oxp3'
            if needs_trust and not self.trusted:
                if not self.dialog.confirm(self.tr(
                        '此包为本地测试包，尚未公开发布，也不代表实机运行验收通过。\n仅在本次会话信任该包并读取本机维护状态？',
                        'This is an unpublished local test package. It does not establish runtime validation.\nTrust this package for this session and read this device’s maintenance state?')
                        + '\n' + self.label(model['name'])
                        + ('\n' + str(bundle.get('version', '')) if bundle else '')):
                    return
                self.trusted = True
            flags = ['--trust-local-test'] if needs_trust and self.trusted else []
            options = self.backend.call('shared-options' if choice == 'shared' else 'options', model['id'], *flags)
            self.actions(model, options)

    def unavailable(self, options, model):
        rows = [item for item in options['capabilities'] if not item.get('available')]
        capability_ids = {item['id'] for item in options['capabilities']}
        # A guide entry must remain visible even if it has no payload mapping.
        for item in model.get('components', []):
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
        if owned_current or pending:
            actions.append(('restore', self.tr('恢复已安装的适配', 'Restore installed adaptations')))
        actions.extend([('guide', self.tr('查看适配指南', 'Read adaptation guide')),
                        ('back', self.tr('返回', 'Back'))])
        text = self.label(model['name']) + '\n' + self.tr(
            '安装作用于当前活动系统槽；共享 /home 中的插件和配置可能同时影响另一系统槽。',
            'Installation targets the active OS slot; plugins and configuration in shared /home may also affect the other OS slot.')
        if pending:
            text += '\n' + self.tr('有中断的维护操作。请先选择恢复。', 'Maintenance was interrupted. Choose Restore first.')
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
        args = ['shared-plan' if shared else 'plan', model['id'], '--action', action, '--trust-local-test']
        if not shared:
            args.extend(['--installer-language', self.lang, '--kde-language', 'system'])
        if action == 'apply' and not shared:
            args.append('--acknowledge-update-hold')
        if action == 'apply':
            for category, flag in (('repairs', '--repair'), ('plugins', '--plugin')):
                for identity in sorted(selection[category]):
                    args.extend([flag, identity])
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
            receipt = self.backend.call('shared-execute' if shared else 'execute', str(path), '--reviewed-sha256', digest, '--trust-local-test')
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
        if receipt.get('restart_required') is True:
            text += '\n' + self.tr('请重启以应用更改。', 'Restart to apply the changes.')
        elif receipt.get('restart_required') is False:
            text += '\n' + self.tr('此次操作无需重启。', 'No restart is required for this operation.')
        self.dialog.message(text, error=failed)

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
    return ModelUI(KDialog(lang), Backend(), lang=lang, offline=args.offline).run()


if __name__ == '__main__':
    sys.exit(main())
