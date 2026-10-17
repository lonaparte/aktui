"""Real curses startup/exit under a pseudo-terminal."""
import fcntl
import os
import pty
import select
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unittest


class TerminalTests(unittest.TestCase):
    def test_start_and_quit(self):
        with tempfile.TemporaryDirectory() as root:
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 30, 100, 0, 0))
            process = subprocess.Popen([sys.executable, '-m', 'aktui', '--data-dir', root],
                                       stdin=slave, stdout=slave, stderr=slave,
                                       env=dict(os.environ, TERM='xterm-256color', LC_ALL='C.UTF-8'))
            os.close(slave)
            output = b''
            try:
                deadline = time.monotonic() + 10
                while b'Open deck' not in output and time.monotonic() < deadline:
                    if select.select([master], [], [], .1)[0]:
                        output += os.read(master, 65536)
                self.assertIn(b'Open deck', output)
                os.write(master, b'q')
                self.assertEqual(process.wait(timeout=10), 0)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                os.close(master)


if __name__ == '__main__':
    unittest.main()
