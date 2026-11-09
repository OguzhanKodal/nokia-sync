"""Nokia'nın USB modem kapısına AT komutu gönderen küçük yardımcı."""
import glob
import os
import select
import termios
import time


class Modem:
    def __init__(self, path=None):
        path = path or (sorted(glob.glob("/dev/cu.usbmodem*")) or [None])[0]
        if not path:
            raise RuntimeError("Modem kapısı yok (telefon Nokia Suite modunda bağlı olmalı).")
        self.path = path
        self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        attrs = termios.tcgetattr(self.fd)
        attrs[0] = 0                     # iflag
        attrs[1] = 0                     # oflag
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        attrs[3] = 0                     # lflag (raw)
        attrs[4] = attrs[5] = termios.B115200
        termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
        termios.tcflush(self.fd, termios.TCIOFLUSH)

    def close(self):
        os.close(self.fd)

    def cmd(self, line, timeout=5.0):
        """Komutu gönderir, OK/ERROR gelene kadar cevabı okur (str)."""
        os.write(self.fd, (line + "\r").encode("latin-1"))
        buf = b""
        end = time.time() + timeout
        while time.time() < end:
            r, _, _ = select.select([self.fd], [], [], 0.2)
            if r:
                try:
                    buf += os.read(self.fd, 4096)
                except BlockingIOError:
                    pass
                txt = buf.decode("latin-1")
                lines = [x.strip() for x in txt.splitlines() if x.strip()]
                if lines and (lines[-1] == "OK" or "ERROR" in lines[-1]):
                    break
        return buf.decode("latin-1", "replace")
