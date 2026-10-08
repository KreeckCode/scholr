"""The partners app must never talk to Moodle directly; online access goes through shop orders."""

import ast
from pathlib import Path

from django.test import SimpleTestCase

FORBIDDEN = ('integration.moodle_client', 'integration.services', 'integration.tasks', 'integration.views')
APP_ROOT = Path(__file__).resolve().parent.parent


class MoodleBoundaryTests(SimpleTestCase):
    def test_no_module_imports_moodle_code(self):
        offenders = []
        for path in APP_ROOT.rglob('*.py'):
            if 'migrations' in path.parts or 'tests' in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module] + [f'{node.module}.{alias.name}' for alias in node.names]
                for name in names:
                    if name.startswith(FORBIDDEN):
                        offenders.append(f'{path.relative_to(APP_ROOT)}:{node.lineno} imports {name}')
        self.assertEqual(offenders, [], 'partners must not import Moodle sync code:\n' + '\n'.join(offenders))
