"""Regression for cleanup after a failed prepared-image re-protection."""
import ast
from pathlib import Path
import sys
import unittest


class IntegrationCleanupTests(unittest.TestCase):
    def test_protection_failure_still_attempts_every_unmount(self):
        source = Path(__file__).resolve().parents[1]/'guest/integration-image.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        loop = next(node for node in tree.body if isinstance(node, ast.For)
                    and isinstance(node.target, ast.Name) and node.target.id == 'slot'
                    and any(isinstance(item, ast.Try) and item.finalbody for item in node.body))
        final = next(item.finalbody for item in loop.body if isinstance(item, ast.Try) and item.finalbody)
        code = compile(ast.Module(body=final, type_ignores=[]), str(source), 'exec')
        root = Path('/mnt/synthetic'); mounts = [root, root/'var', root/'home']
        calls = []
        class FakeSubprocess:
            @staticmethod
            def run(args, check):
                calls.append(args)
                if args[0] == 'btrfs' or args == ['umount', str(root/'home')]:
                    raise OSError('injected cleanup failure')
        with self.assertRaisesRegex(RuntimeError, 'cleanup incomplete'):
            exec(code, dict(sys=sys, subprocess=FakeSubprocess, root=root, mounts=mounts))
        self.assertEqual([args[1] for args in calls if args[0] == 'umount'],
                         [str(root/'home'), str(root/'var'), str(root)])


if __name__ == '__main__':
    unittest.main()
