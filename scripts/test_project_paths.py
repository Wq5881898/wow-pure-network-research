"""Offline checks for Windows/WSL launch paths containing spaces."""
import shlex
import unittest
from project_paths import ROOT, wsl_path


class ProjectPathTests(unittest.TestCase):
    def test_windows_drive_and_spaces(self):
        self.assertEqual(wsl_path(r'C:\APP\opencode\project\wow pure network'),
                         '/mnt/c/APP/opencode/project/wow pure network')

    def test_linux_path_unchanged(self):
        self.assertEqual(wsl_path('/mnt/c/project with spaces'), '/mnt/c/project with spaces')

    def test_shell_roundtrip(self):
        path = wsl_path(ROOT)
        self.assertEqual(shlex.split('cd ' + shlex.quote(path)), ['cd', path])

    def test_unc_is_rejected(self):
        with self.assertRaises(ValueError):
            wsl_path(r'\\server\share\project')


if __name__ == '__main__':
    unittest.main()
