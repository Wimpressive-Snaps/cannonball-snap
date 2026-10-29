import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zipfile

WRAPPER = Path(__file__).resolve().parent / 'cannonball-wrapper'
if not WRAPPER.exists():
    WRAPPER = Path(__file__).resolve().parents[1] / 'snap/local/cannonball-wrapper'


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.snap = self.root / 'snap'
        self.common = self.root / 'common'
        self.bin = self.root / 'bin'
        self.common.mkdir()
        self.bin.mkdir()
        app = self.snap / 'opt/cannonball'
        (app / 'res').mkdir(parents=True)
        (app / 'patches').mkdir()
        (app / 'res/config.xml').write_text('<up>273</up><fix_samples>0</fix_samples>')
        self.script(app / 'cannonball', 'printf "%s\\n" "$@" > "$SNAP_USER_COMMON/launched"')
        self.script(self.bin / 'wget', '''if [ "${FAIL_DOWNLOAD:-}" = 1 ]; then exit 8; fi
while [ "$1" != -O ]; do shift; done
cp "$FIXTURE" "$2"''')
        self.script(self.bin / 'bspatch', 'test "${FAIL_PATCH:-}" != 1 || exit 1; cp "$1" "$2"')
        self.fixture = self.root / 'roms.zip'
        with zipfile.ZipFile(self.fixture, 'w') as z:
            z.writestr('fixture/epr-10381b.132', b'synthetic test data')
            z.writestr('fixture/opr-10188.71', b'synthetic audio data')
        self.env = dict(os.environ, SNAP=str(self.snap), SNAP_USER_COMMON=str(self.common),
                        FIXTURE=str(self.fixture), PATH=str(self.bin) + ':' + os.environ['PATH'])

    def script(self, path, body):
        path.write_text('#!/bin/sh\nset -eu\n' + body + '\n')
        path.chmod(0o755)

    def run_launcher(self, **extra):
        return subprocess.run(['bash', str(WRAPPER), '--test-argument'], env=dict(self.env, **extra), capture_output=True, text=True)

    def test_failed_download_does_not_launch_or_cache_partial_data(self):
        archive = self.common / 'master.zip'
        archive.write_bytes(b'old broken download')
        r = self.run_launcher(FAIL_DOWNLOAD='1')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('download failed', r.stderr)
        self.assertFalse((self.common / 'launched').exists())
        self.assertEqual(archive.read_bytes(), b'old broken download')
        self.assertFalse(list(self.common.glob('.setup.*')))

    def test_retry_repairs_corrupt_archive_and_preserves_user_settings(self):
        (self.common / 'master.zip').write_bytes(b'broken')
        (self.common / 'config.xml').write_text('<up>999</up><fix_samples>0</fix_samples>')
        r = self.run_launcher()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('<up>999</up>', (self.common / 'config.xml').read_text())
        self.assertIn('<fix_samples>1', (self.common / 'config.xml').read_text())
        self.assertEqual((self.common / 'launched').read_text(), '--test-argument\n')
        self.assertTrue(zipfile.is_zipfile(self.common / 'master.zip'))
        # Reopening with a failed network still works from installed data.
        self.assertEqual(self.run_launcher(FAIL_DOWNLOAD='1').returncode, 0)

    def test_invalid_download_stops_before_installing_data(self):
        self.fixture.write_bytes(b'HTTP error page')
        r = self.run_launcher()
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((self.common / 'master.zip').exists())
        self.assertFalse((self.common / 'launched').exists())

    def test_audio_failure_is_retryable(self):
        r = self.run_launcher(FAIL_PATCH='1')
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((self.common / 'roms/opr-10188.71f').exists())
        self.assertFalse((self.common / 'launched').exists())
        self.assertEqual(self.run_launcher(FAIL_DOWNLOAD='1').returncode, 0)


if __name__ == '__main__':
    unittest.main()
