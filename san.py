"""SyncML DS 1.2 Server Alerted Notification (paket 0) üretir: telefona 'senkron başlat' der."""
import base64
import hashlib
import struct

SYNC_TYPES = {"two-way": 6, "one-way-client": 7, "refresh-client": 8, "one-way-server": 9, "refresh-server": 10}
VCARD21 = 0x07  # OMNA MIME kodu: text/x-vcard


def _b64md5(b):
    return base64.b64encode(hashlib.md5(b).digest())


def build_san(server_id="PC Suite", server_uri="contacts", sync="two-way", session_id=1,
              password="", nonce=b"", ui_mode=0):
    sid = server_id.encode()
    uri = server_uri.encode()
    bits = (12 << 30) | ((ui_mode & 3) << 28) | (1 << 27)  # sürüm 1.2, kullanıcı arayüzü, sunucu başlattı
    hdr = bits.to_bytes(5, "big") + struct.pack(">H", session_id) + bytes([len(sid)]) + sid
    body = bytes([1 << 4])  # 1 senkron
    body += bytes([SYNC_TYPES[sync] << 4]) + VCARD21.to_bytes(3, "big") + bytes([len(uri)]) + uri
    trigger = hdr + body
    digest = hashlib.md5(_b64md5(sid + b":" + password.encode()) + b":" + nonce + b":" + _b64md5(trigger)).digest()
    return digest + trigger
