"""Exercise supervision using harmless child processes, without providers or servers."""
import json
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

from scripts.start_service import ROOT, supervise


class ServiceStartupTests(unittest.TestCase):
    def run_children(self, commands, stopping):
        children = []
        real_popen = subprocess.Popen

        def launch(*args, **kwargs):
            child = real_popen(*args, **kwargs)
            children.append(child)
            return child

        with patch('scripts.start_service.subprocess.Popen', side_effect=launch):
            code = supervise(commands, stopping)
        self.assertTrue(all(child.poll() is not None for child in children))
        return code, children

    def test_either_child_exit_stops_sibling_and_requests_restart(self):
        for exiting in (0, 1):
            with self.subTest(exiting=exiting):
                commands = [(str(index), [sys.executable, '-c',
                    'raise SystemExit(0)' if index == exiting else 'import time; time.sleep(30)'])
                    for index in (0, 1)]
                code, children = self.run_children(commands, threading.Event())
                self.assertEqual(code, 1)
                self.assertEqual(len(children), 2)

    def test_requested_shutdown_reaps_both_children(self):
        stopping = threading.Event()
        timer = threading.Timer(0.4, stopping.set)
        timer.start()
        self.addCleanup(timer.cancel)
        code, children = self.run_children([
            (str(index), [sys.executable, '-c', 'import time; time.sleep(30)'])
            for index in (0, 1)
        ], stopping)
        self.assertEqual(code, 0)
        self.assertEqual(len(children), 2)

    def test_failed_second_launch_cleans_up_first_child(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                                 start_new_session=sys.platform != 'win32')
        with patch('scripts.start_service.subprocess.Popen', side_effect=[child, OSError('launch failed')]):
            with self.assertRaises(OSError):
                supervise([('worker', []), ('web', [])], threading.Event())
        self.assertIsNotNone(child.poll())

    def test_railway_config_selects_supervised_startup_and_migrations(self):
        config = json.loads((ROOT / 'railway.json').read_text())['deploy']
        self.assertEqual(config['startCommand'], 'python scripts/start_service.py')
        self.assertIn('python -m flask --app app db-upgrade', config['preDeployCommand'])
        self.assertEqual(config['restartPolicyType'], 'ON_FAILURE')
