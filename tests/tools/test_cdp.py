import contextlib
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import cdp


class BrowserCleanupTest(unittest.TestCase):
    def test_profile_cleanup_retries_only_after_waiting_for_browser(self):
        for failures, attempts, warns in [(2, 3, False), (20, 5, True)]:
            with self.subTest(failures=failures):
                browser = cdp.Browser()
                browser.process = Mock()
                browser.temp = Mock(name='temp profile')
                browser.temp.name = 'test-profile'

                def rmtree(path):
                    browser.process.wait.assert_called_once_with(timeout=5)
                    if cleanup.call_count <= failures:
                        raise PermissionError(32, 'locked', path)

                browser.temp.cleanup = lambda: cdp.shutil.rmtree(browser.temp.name)
                with (patch.object(cdp.shutil, 'rmtree', side_effect=rmtree) as cleanup,
                      patch.object(cdp.time, 'sleep') as pause,
                      contextlib.redirect_stderr(io.StringIO()) as stderr):
                    browser.__exit__(None, None, None)
                self.assertEqual(cleanup.call_count, attempts)
                self.assertEqual(pause.call_count, attempts - 1)
                self.assertEqual('경고:' in stderr.getvalue(), warns)
