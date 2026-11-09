"""Nokia'nın USB üzerindeki OBEX kanalıyla (Nokia Suite modu) konuşan küçük istemci."""
import struct

import usb.backend.libusb1 as libusb1
import usb.core
import usb.util

NOKIA = 0x0421
LIBUSB = "/opt/homebrew/lib/libusb-1.0.dylib"

H_CONNID, H_TARGET, H_TYPE, H_LENGTH, H_BODY, H_EOB, H_WHO = 0xCB, 0x46, 0x42, 0xC3, 0x48, 0x49, 0x4A
OK, CONTINUE = 0xA0, 0x90


class ObexError(Exception):
    pass


def _hdr(hid, value):
    kind = hid & 0xC0
    if kind == 0xC0:
        return bytes([hid]) + struct.pack(">I", value)
    if kind == 0x80:
        return bytes([hid, value])
    return bytes([hid]) + struct.pack(">H", len(value) + 3) + value


def parse_headers(data):
    out, i = [], 0
    while i < len(data):
        hid = data[i]
        kind = hid & 0xC0
        if kind == 0xC0:
            out.append((hid, struct.unpack(">I", data[i + 1:i + 5])[0]))
            i += 5
        elif kind == 0x80:
            out.append((hid, data[i + 1]))
            i += 2
        else:
            ln = struct.unpack(">H", data[i + 1:i + 3])[0]
            out.append((hid, data[i + 3:i + ln]))
            i += ln
    return out


class NokiaObex:
    def __init__(self, target=b"SYNCML-SYNC", interface_name="SYNCML-SYNC", timeout=60000):
        be = libusb1.get_backend(find_library=lambda x: LIBUSB)
        self.dev = None
        for d in usb.core.find(find_all=True, idVendor=NOKIA, backend=be):
            self.dev = d
            break
        if self.dev is None:
            raise ObexError("Nokia USB'de bulunamadı (Nokia Suite modunda bağlı olmalı).")
        self.timeout = timeout
        cfg = self.dev.get_active_configuration()
        ctrl = data = None
        # İstenen OBEX kontrol arayüzünü (class 2 / subclass 11) adından bul; veri arayüzü hemen sonraki.
        obex_ctrls = [i for i in cfg if i.bInterfaceClass == 2 and i.bInterfaceSubClass == 11 and i.bAlternateSetting == 0]
        for i in obex_ctrls:
            try:
                name = usb.util.get_string(self.dev, i.iInterface) if i.iInterface else ""
            except Exception:
                name = ""
            if name == interface_name:
                ctrl = i
        if ctrl is None:
            raise ObexError(f"{interface_name} arayüzü yok")
        dnum = ctrl.bInterfaceNumber + 1
        data = [i for i in cfg if i.bInterfaceNumber == dnum and i.bAlternateSetting == 1][0]
        self.ctrl_num, self.data_num = ctrl.bInterfaceNumber, dnum
        for n in (self.ctrl_num, self.data_num):
            try:
                if self.dev.is_kernel_driver_active(n):
                    self.dev.detach_kernel_driver(n)
            except Exception:
                pass
            usb.util.claim_interface(self.dev, n)
        self.dev.set_interface_altsetting(interface=dnum, alternate_setting=1)
        self.ep_in = [e for e in data if e.bEndpointAddress & 0x80][0]
        self.ep_out = [e for e in data if not e.bEndpointAddress & 0x80][0]
        for ep in (self.ep_in, self.ep_out):
            try:
                self.dev.clear_halt(ep)
            except Exception:
                pass
        # önceki yarım oturumdan kalmış veriyi boşalt
        try:
            while self.ep_in.read(0x10000, timeout=200):
                pass
        except Exception:
            pass
        self.target = target
        self.conn_id = None
        self.max_pkt = 0x2000

    def close(self):
        try:
            self.dev.set_interface_altsetting(interface=self.data_num, alternate_setting=0)
        except Exception:
            pass
        for n in (self.ctrl_num, self.data_num):
            try:
                usb.util.release_interface(self.dev, n)
            except Exception:
                pass
        usb.util.dispose_resources(self.dev)

    # ---------------------------------------------------------------- ham paketler
    def _xfer(self, packet):
        self.ep_out.write(packet, timeout=self.timeout)
        if len(packet) % self.ep_out.wMaxPacketSize == 0:
            self.ep_out.write(b"", timeout=self.timeout)  # USB: tam katlı aktarımı boş paketle bitir
        buf = bytes(self.ep_in.read(0x10000, timeout=self.timeout))
        while len(buf) < 3 or len(buf) < struct.unpack(">H", buf[1:3])[0]:
            buf += bytes(self.ep_in.read(0x10000, timeout=self.timeout))
        return buf

    def _req(self, opcode, headers, extra=b""):
        body = extra + b"".join(headers)
        pkt = bytes([opcode]) + struct.pack(">H", 3 + len(body)) + body
        resp = self._xfer(pkt)
        return resp[0], resp[3:]

    # ---------------------------------------------------------------- OBEX işlemleri
    def connect(self):
        code, rest = self._req(0x80, [_hdr(H_TARGET, self.target)], extra=bytes([0x10, 0x00]) + struct.pack(">H", 0xFFFF))
        if code != OK:
            raise ObexError(f"CONNECT reddedildi: 0x{code:02x}")
        self.max_pkt = min(struct.unpack(">H", rest[2:4])[0], 0xFFFF)
        for hid, val in parse_headers(rest[4:]):
            if hid == H_CONNID:
                self.conn_id = val
        return rest

    def _cid(self):
        return [_hdr(H_CONNID, self.conn_id)] if self.conn_id is not None else []

    def put(self, mime, data):
        first = True
        pos = 0
        while True:
            hdrs = list(self._cid()) if first else []
            if first:
                hdrs += [_hdr(H_TYPE, mime.encode() + b"\x00"), _hdr(H_LENGTH, len(data))]
            used = 3 + sum(len(h) for h in hdrs) + 3
            room = self.max_pkt - used
            chunk = data[pos:pos + room]
            pos += len(chunk)
            last = pos >= len(data)
            hdrs.append(_hdr(H_EOB if last else H_BODY, chunk))
            code, _ = self._req(0x82 if last else 0x02, hdrs)
            first = False
            if last:
                if code != OK:
                    raise ObexError(f"PUT reddedildi: 0x{code:02x}")
                return
            if code != CONTINUE:
                raise ObexError(f"PUT kesildi: 0x{code:02x}")

    def get(self, mime):
        hdrs = self._cid() + [_hdr(H_TYPE, mime.encode() + b"\x00")]
        out = b""
        while True:
            code, rest = self._req(0x83, hdrs)
            for hid, val in parse_headers(rest):
                if hid in (H_BODY, H_EOB):
                    out += val
            if code == OK:
                return out
            if code != CONTINUE:
                raise ObexError(f"GET reddedildi: 0x{code:02x}")
            hdrs = self._cid()

    def disconnect(self):
        try:
            self._req(0x81, self._cid())
        except Exception:
            pass


H_NAME = 0x01

FTP_TARGET = bytes.fromhex("F9EC7BC4953C11D2984E525400DC9E09")


def _name(s):
    return s.encode("utf-16-be") + b"\x00\x00"


def setpath(self, name=None, up=False, create=False):
    """Klasör değiştir. name=None ve up=False → köke dön."""
    flags = (0x01 if up else 0x00) | (0x00 if create else 0x02)
    hdrs = self._cid()
    if name is not None:
        hdrs = hdrs + [_hdr(H_NAME, _name(name))]
    elif not up:
        hdrs = hdrs + [bytes([H_NAME, 0x00, 0x03])]
    code, _ = self._req(0x85, hdrs, extra=bytes([flags, 0x00]))
    if code != OK:
        raise ObexError(f"SETPATH {name!r} reddedildi: 0x{code:02x}")


def put_file(self, name, data):
    """Bulunulan klasöre dosya yazar."""
    first, pos = True, 0
    while True:
        hdrs = list(self._cid()) if first else []
        if first:
            hdrs += [_hdr(H_NAME, _name(name)), _hdr(H_LENGTH, len(data))]
        room = min(self.max_pkt, 0x8000) - 3 - sum(len(h) for h in hdrs) - 3
        chunk = data[pos:pos + room]
        pos += len(chunk)
        last = pos >= len(data)
        hdrs.append(_hdr(H_EOB if last else H_BODY, chunk))
        code, _ = self._req(0x82 if last else 0x02, hdrs)
        first = False
        if last:
            if code != OK:
                raise ObexError(f"{name} yazılamadı: 0x{code:02x}")
            return
        if code != CONTINUE:
            raise ObexError(f"{name} yazımı kesildi: 0x{code:02x}")


def get_file(self, name):
    hdrs = self._cid() + [_hdr(H_NAME, _name(name))]
    out = b""
    while True:
        code, rest = self._req(0x83, hdrs)
        for hid, val in parse_headers(rest):
            if hid in (H_BODY, H_EOB):
                out += val
        if code == OK:
            return out
        if code != CONTINUE:
            raise ObexError(f"{name} okunamadı: 0x{code:02x}")
        hdrs = self._cid()


def listing(self):
    return self.get("x-obex/folder-listing").decode("utf-8", "replace")


NokiaObex.setpath = setpath
NokiaObex.put_file = put_file
NokiaObex.get_file = get_file
NokiaObex.listing = listing


def ftp():
    """Nokia Suite modunda telefonun dosya sistemine bağlanır."""
    o = NokiaObex(target=FTP_TARGET, interface_name="PC Suite Services", timeout=30000)
    o.connect()
    return o
