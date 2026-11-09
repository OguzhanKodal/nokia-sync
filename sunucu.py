#!/usr/bin/env python3
"""Nokia E60 için tek yönlü (iPhone/Mac Kişiler → Nokia) SyncML senkron sunucusu.

Kullanım:
    python3 sunucu.py                  # Mac Kişiler'den okur, 8080'de dinler
    python3 sunucu.py --onizle         # kaç kişi gideceğini gösterir, sunucu açmaz
    python3 sunucu.py --vcf kisiler.vcf  # Kişiler yerine iCloud'dan alınmış .vcf dosyası
"""
import argparse
import base64
import copy
import http.server
import json
import socket
import subprocess
import sys
import threading
import time
import traceback
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import vcard
import wbxml

HERE = Path(__file__).resolve().parent
METINF = set(wbxml.METINF_TAGS)
CHANGE_CMDS = ("Add", "Replace", "Delete", "Copy", "Move")
VCARD_TYPE = "text/x-vcard"


# ---------------------------------------------------------------- yardımcılar
def E(tag, text=None, children=()):
    e = ET.Element(tag)
    if text is not None:
        e.text = str(text)
    for c in children:
        if c is not None:
            e.append(c)
    return e


def loc(tag, uri):
    return E(tag, children=[E("LocURI", uri)])


def strip_ns(root):
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def to_xml(root, ns):
    def esc(t):
        return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    out = []

    def w(e, parent_space):
        if e.tag == "DevInf" or parent_space == "devinf":
            space = "devinf"
        elif e.tag in METINF:
            space = "metinf"
        else:
            space = "syncml"
        attr = ""
        if parent_space is None:
            attr = f' xmlns="{ns}"'
        elif space != parent_space:
            attr = {"devinf": ' xmlns="syncml:devinf"', "metinf": ' xmlns="syncml:metinf"'}.get(space, f' xmlns="{ns}"')
        if not e.text and not len(e):
            out.append(f"<{e.tag}{attr}/>")
        else:
            out.append(f"<{e.tag}{attr}>")
            if e.text:
                out.append(esc(e.text))
            for c in e:
                w(c, space)
                if c.tail:
                    out.append(esc(c.tail))
            out.append(f"</{e.tag}>")

    w(root, None)
    return ('<?xml version="1.0" encoding="UTF-8"?>' + "".join(out)).encode("utf-8")


def local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def say(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


# ---------------------------------------------------------------- kişiler
def load_contacts(vcf_path=None):
    """{mac_kimliği: vcard21_metni} döndürür. Numarasız kişiler atlanır."""
    if vcf_path:
        raw = vcard.read_vcf_file(vcf_path)
    else:
        exe = HERE / "kisiler"
        if not exe.exists():
            subprocess.run(["swiftc", "-O", str(HERE / "kisiler.swift"), "-o", str(exe)], check=True)
        p = subprocess.run([str(exe)], capture_output=True, timeout=120)
        if p.returncode != 0:
            raise RuntimeError(p.stderr.decode("utf-8", "replace").strip() or "kişiler okunamadı")
        raw = json.loads(p.stdout)
    return {c["id"]: vcard.to_vcard21(c) for c in raw if c.get("phones")}


# ---------------------------------------------------------------- kalıcı durum
class Store:
    def __init__(self, path):
        self.path = path
        self.data = json.loads(path.read_text()) if path.exists() else {}

    def get(self, key):
        return copy.deepcopy(self.data.get(key) or {
            "client_next": None, "server_next": "0", "next_guid": 1, "items": {}})

    def put(self, key, value):
        self.data[key] = value
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1))
        tmp.replace(self.path)


# ---------------------------------------------------------------- oturum
class Session:
    def __init__(self, device, sid):
        self.device, self.sid = device, sid
        self.out_msgid = 0
        self.phase = "init"
        self.sync = None
        self.state_key = None
        self.state = None
        self.queue = []
        self.sent = {}          # (msgid, cmdid) -> bilgi
        self.pending_add = {}   # guid -> (uid, hash)
        self.client_items = {}  # luid -> {'name','numbers'} (yavaş senkron)
        self.client_replaced, self.client_deleted = set(), set()
        self.max_msg = 8000
        self.authed = False
        self.stats = {"Add": 0, "Replace": 0, "Delete": 0}
        self.got_client_sync = False
        self.waits = 0


class SyncServer:
    def __init__(self, args):
        self.args = args
        self.store = Store(HERE / "durum.json")
        self.sessions = {}
        self.lock = threading.Lock()
        self.logdir = HERE / "kayitlar"
        self.logdir.mkdir(exist_ok=True)

    # --------------------------------------------------------- giriş noktası
    def handle(self, data, ctype):
        with self.lock:
            return self._handle(data, ctype)

    def _log(self, s, direction, root):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        name = f"{stamp}-{(s.sid if s else 'x')}-{direction}.xml"
        try:
            (self.logdir / name).write_bytes(to_xml(root, "SYNCML:SYNCML1.1"))
        except Exception:
            pass

    def _handle(self, data, ctype):
        if "wbxml" in ctype.lower() or (data[:1] in (b"\x01", b"\x02", b"\x03") and b"<" not in data[:5]):
            root, pubid = wbxml.decode(data)
            enc, wver = "wbxml", data[0]
        else:
            root = strip_ns(ET.fromstring(data))
            enc, wver, pubid = "xml", 2, None
        hdr, body = root.find("SyncHdr"), root.find("SyncBody")
        sid, msgid = hdr.findtext("SessionID"), hdr.findtext("MsgID")
        device = hdr.findtext("Source/LocURI")
        key = (device, sid)
        s = self.sessions.get(key)
        if s is None or msgid == "1":
            s = Session(device, sid)
            self.sessions[key] = s
            say(f"Yeni oturum: cihaz {device}, oturum {sid}")
        self._log(s, f"{msgid}-gelen", root)
        s.enc, s.wver = enc, wver
        s.verdtd, s.verproto = hdr.findtext("VerDTD") or "1.1", hdr.findtext("VerProto") or "SyncML/1.1"
        s.pubid = pubid or f"-//SYNCML//DTD SyncML {s.verdtd}//EN"
        s.server_uri = hdr.findtext("Target/LocURI")
        mm = hdr.findtext("Meta/MaxMsgSize")
        if mm and mm.strip().isdigit():
            s.max_msg = int(mm)
        s.out_msgid += 1

        statuses, cmds, track = [], [], {}
        code = self._auth(s, hdr)
        statuses.append(self._status(msgid, "0", "SyncHdr", code, target=s.server_uri, source=device))
        if code == 401:
            say("Kullanıcı adı/şifre yanlış. Nokia'daki senk. profilini kontrol et.")
            self.sessions.pop(key, None)
            return self._send(s, statuses, [], track, final=True)

        final = body.find("Final") is not None
        got_sync_alert = False
        for cmd in body:
            tag, cid = cmd.tag, cmd.findtext("CmdID")
            if tag == "Final":
                continue
            if tag == "Status":
                self._on_status(s, cmd)
                continue
            if tag == "Alert":
                got_sync_alert |= self._on_alert(s, msgid, cmd, statuses, cmds)
            elif tag == "Put":
                ct = cmd.find("Item/Data/DevInf/DataStore/CTCap") or cmd.find("Item/Data/DevInf/CTCap")
                if ct is not None:
                    s.client_ctcap = copy.deepcopy(ct)
                statuses.append(self._status(msgid, cid, "Put", 200, source=cmd.findtext("Item/Source/LocURI")))
            elif tag == "Get":
                self._on_get(s, msgid, cmd, statuses, cmds)
            elif tag == "Sync":
                self._on_sync(s, msgid, cmd, statuses)
            elif tag == "Map":
                self._on_map(s, msgid, cmd, statuses)
            else:
                statuses.append(self._status(msgid, cid, tag, 200))

        if got_sync_alert:
            if not final:
                cmds.append(self._alert222(s))
            return self._send(s, statuses, cmds, track, final=final)

        if s.phase == "client_sync":
            if not final:
                cmds.append(self._alert222(s))
                return self._send(s, statuses, cmds, track, final=False)
            if not s.got_client_sync and s.waits < 2:
                # Nokia önce yalnız onay mesajı yollayıp kendi listesini sonraki mesajda gönderiyor; sırasını bekle.
                s.waits += 1
                say("Telefon henüz kendi listesini göndermedi; sırası bekleniyor.")
                return self._send(s, statuses, cmds, track, final=True)
            self._prepare_changes(s)
            s.phase = "server_sync"

        if s.phase == "server_sync":
            return self._send_chunk(s, statuses, cmds, track)

        if s.phase == "map":
            if not final:
                cmds.append(self._alert222(s))
                return self._send(s, statuses, cmds, track, final=False)
            self._commit(s)
            return self._send(s, statuses, cmds, track, final=True)

        # bilinmeyen/biten oturum: sadece durumları dön
        return self._send(s, statuses, cmds, track, final=final)

    # --------------------------------------------------------- komutlar
    def _auth(self, s, hdr):
        cred = hdr.find("Cred")
        want_user, want_pass = self.args.kullanici, self.args.sifre
        if cred is None:
            if s.authed or not want_pass:
                return 200
            return 401
        user = pw = ""
        try:
            user, _, pw = base64.b64decode(cred.findtext("Data") or "").decode("utf-8", "replace").partition(":")
        except Exception:
            pass
        if want_pass and (pw != want_pass or (want_user and user != want_user)):
            return 401
        s.authed = True
        return 212

    def _status(self, msgref, cmdref, cmd, code, target=None, source=None, items=()):
        e = E("Status", children=[E("MsgRef", msgref), E("CmdRef", cmdref), E("Cmd", cmd)])
        if target:
            e.append(E("TargetRef", target))
        if source:
            e.append(E("SourceRef", source))
        e.append(E("Data", code))
        for i in items:
            e.append(i)
        return e

    def _alert222(self, s):
        return E("Alert", children=[E("Data", 222), E("Item", children=[
            loc("Target", s.sync["client_db"] if s.sync else s.device),
            loc("Source", s.sync["server_db"] if s.sync else s.server_uri)])])

    def _on_alert(self, s, msgid, cmd, statuses, cmds):
        cid = cmd.findtext("CmdID")
        code = int((cmd.findtext("Data") or "0").strip())
        if not (200 <= code <= 205 or code == 225):
            statuses.append(self._status(msgid, cid, "Alert", 200))
            return False
        item = cmd.find("Item")
        client_db = item.findtext("Source/LocURI")
        server_db = item.findtext("Target/LocURI")
        last = item.findtext("Meta/Anchor/Last")
        nxt = item.findtext("Meta/Anchor/Next")
        s.state_key = f"{s.device}|{client_db}"
        s.state = self.store.get(s.state_key)
        sync_code, scode = code, 200
        if code == 225:  # yarım kalan oturumu sürdür: desteklemiyoruz, baştan tam senkron iste
            sync_code, scode = 201, 508
        elif code in (200, 204) and (s.state["client_next"] is None or s.state["client_next"] != last):
            sync_code, scode = 201, 508
        s.sync = {"code": sync_code, "client_db": client_db, "server_db": server_db, "client_next": nxt}
        s.server_next = str(int(time.time()))
        names = {200: "iki yönlü (yalnız gönderim)", 201: "yavaş (tam karşılaştırma)", 204: "sunucudan tek yönlü",
                 205: "sunucudan tam yenileme", 202: "telefondan tek yönlü", 203: "telefondan yenileme"}
        say(f"Senkron türü: {names.get(sync_code, sync_code)}  (telefon '{client_db}' → sunucu '{server_db}')")
        anchor = E("Item", children=[E("Data", children=[E("Anchor", children=[E("Next", nxt)])])])
        statuses.append(self._status(msgid, cid, "Alert", scode, target=server_db, source=client_db, items=[anchor]))
        cmds.append(E("Alert", children=[E("Data", sync_code), E("Item", children=[
            loc("Target", client_db), loc("Source", server_db),
            E("Meta", children=[E("Anchor", children=[E("Last", s.state["server_next"]), E("Next", s.server_next)])])])]))
        s.phase = "client_sync"
        return True

    def _devinf(self, s):
        db = s.sync["server_db"] if s.sync else "contacts"
        ver = "1.2" if s.verdtd == "1.2" else "1.1"
        props = [("BEGIN", ["VCARD"]), ("END", ["VCARD"]), ("VERSION", ["2.1"]), ("N", []), ("FN", []),
                 ("TEL", None), ("EMAIL", None), ("ORG", [])]
        ctcap = E("CTCap", children=[E("CTType", VCARD_TYPE)])
        for name, vals in props:
            ctcap.append(E("PropName", name))
            for v in vals or []:
                ctcap.append(E("ValEnum", v))
            if name == "TEL":
                for p in ("CELL", "HOME", "WORK", "VOICE", "FAX", "PAGER"):
                    ctcap.append(E("ParamName", p))
            if name == "EMAIL":
                ctcap.append(E("ParamName", "INTERNET"))
        # DevInf 1.2'de CTCap DataStore içinde ve farklı yapıda; isteğe bağlı olduğu için 1.2'de gönderme.
        return E("DevInf", children=[
            E("VerDTD", ver), E("Man", "nokia-sync"), E("Mod", "Mac"), E("FwV"), E("SwV", "1.0"), E("HwV"),
            E("DevID", "nokia-sync-mac"), E("DevTyp", "server"),
            E("DataStore", children=[
                E("SourceRef", db), E("DisplayName", "iPhone Kisiler"), E("MaxGUIDSize", 16),
                E("Rx-Pref", children=[E("CTType", VCARD_TYPE), E("VerCT", "2.1")]),
                E("Tx-Pref", children=[E("CTType", VCARD_TYPE), E("VerCT", "2.1")]),
                copy.deepcopy(getattr(s, "client_ctcap", None) or self._ctcap12()) if ver == "1.2" else None,
                E("SyncCap", children=[E("SyncType", n) for n in (1, 2, 3, 4, 5, 6)])]),
            ctcap if ver == "1.1" else None])

    def _ctcap12(self):
        """DevInf 1.2 biçiminde (DataStore içinde) asgari vCard 2.1 alan listesi."""
        def prop(name, vals=(), params=()):
            p = E("Property", children=[E("PropName", name)] + [E("ValEnum", v) for v in vals])
            if params:
                p.append(E("PropParam", children=[E("ParamName", "TYPE")] + [E("ValEnum", v) for v in params]))
            return p
        return E("CTCap", children=[E("CTType", VCARD_TYPE), E("VerCT", "2.1"),
                                    prop("BEGIN", ["VCARD"]), prop("END", ["VCARD"]), prop("VERSION", ["2.1"]),
                                    prop("N"), prop("FN"), prop("TEL", params=["HOME", "WORK", "CELL", "VOICE", "FAX", "PAGER"]),
                                    prop("EMAIL", params=["INTERNET"]), prop("ORG")])

    def _on_get(self, s, msgid, cmd, statuses, cmds):
        cid = cmd.findtext("CmdID")
        target = cmd.findtext("Item/Target/LocURI") or ""
        if "devinf" not in target.lower():
            statuses.append(self._status(msgid, cid, "Get", 404, target=target))
            return
        statuses.append(self._status(msgid, cid, "Get", 200, target=target))
        mtype = cmd.findtext("Meta/Type") or (
            "application/vnd.syncml-devinf+wbxml" if s.enc == "wbxml" else "application/vnd.syncml-devinf+xml")
        res = E("Results", children=[E("MsgRef", msgid), E("CmdRef", cid), E("Meta", children=[E("Type", mtype)]),
                                     E("Item", children=[loc("Source", target), E("Data")])])
        res.find("Item/Data").append(self._devinf(s))
        s._devinf_results = res
        cmds.append(res)

    def _on_sync(self, s, msgid, cmd, statuses):
        s.got_client_sync = True
        statuses.append(self._status(msgid, cmd.findtext("CmdID"), "Sync", 200,
                                     target=cmd.findtext("Target/LocURI"), source=cmd.findtext("Source/LocURI")))
        slow = s.sync and s.sync["code"] == 201
        for sub in cmd:
            if sub.tag not in CHANGE_CMDS:
                continue
            luid = None
            for item in sub.findall("Item"):
                luid = item.findtext("Source/LocURI") or item.findtext("Target/LocURI")
                data = item.findtext("Data") or ""
                if slow and sub.tag in ("Add", "Replace"):
                    s.client_items[luid] = vcard.parse_vcard(data)
                elif sub.tag == "Replace":
                    s.client_replaced.add(luid)
                elif sub.tag == "Delete":
                    s.client_deleted.add(luid)
            code = 201 if sub.tag == "Add" else 200
            statuses.append(self._status(msgid, sub.findtext("CmdID"), sub.tag, code, source=luid))

    def _on_map(self, s, msgid, cmd, statuses):
        statuses.append(self._status(msgid, cmd.findtext("CmdID"), "Map", 200,
                                     target=cmd.findtext("Target/LocURI"), source=cmd.findtext("Source/LocURI")))
        for mi in cmd.findall("MapItem"):
            guid, luid = mi.findtext("Target/LocURI"), mi.findtext("Source/LocURI")
            if guid in s.pending_add and s.state:
                uid, h = s.pending_add.pop(guid)
                ent = s.state["items"].get(uid)
                if ent is not None:
                    ent["luid"], ent["hash"] = luid, h

    def _on_status(self, s, st):
        info = s.sent.get((st.findtext("MsgRef"), st.findtext("CmdRef")))
        if not info or not s.state:
            return
        kind, uid, h = info
        code = int((st.findtext("Data") or "0").strip() or 0)
        ent = s.state["items"].get(uid)
        if ent is None:
            return
        ok = 200 <= code < 300
        if kind == "Replace":
            if ok:
                ent["hash"] = h
            elif code in (404, 211):
                ent["luid"], ent["hash"] = None, None
        elif kind == "Delete":
            ent["luid"], ent["hash"] = None, None
        elif kind == "Add" and not ok:
            s.pending_add.pop(ent["guid"], None)
        if not ok:
            say(f"  Telefon bir komutu reddetti: {kind} → kod {code}")

    # --------------------------------------------------------- değişiklik hesabı
    def _ensure(self, s, uid):
        items = s.state["items"]
        if uid not in items:
            items[uid] = {"guid": f"g{s.state['next_guid']}", "luid": None, "hash": None}
            s.state["next_guid"] += 1
        return items[uid]

    def _prepare_changes(self, s):
        code = s.sync["code"]
        if code in (202, 203):
            say("Telefondan Mac'e yön desteklenmiyor; telefona değişiklik gönderilmedi.")
            return
        try:
            contacts = load_contacts(self.args.vcf)
        except Exception as e:
            say(f"HATA: Kişiler okunamadı ({e}). Bu turda telefona hiçbir şey gönderilmedi.")
            return
        items = s.state["items"]
        if code == 205:
            for v in items.values():
                v["luid"], v["hash"] = None, None
        elif code == 201:
            present = set(s.client_items)
            for v in items.values():
                if v.get("luid") and v["luid"] not in present:
                    v["luid"], v["hash"] = None, None
            mapped = {v["luid"] for v in items.values() if v.get("luid")}
            by_number = {}
            for uid, card in contacts.items():
                if not items.get(uid, {}).get("luid"):
                    for n in vcard.parse_vcard(card)["numbers"]:
                        by_number.setdefault(n, uid)
            matched = 0
            for luid, parsed in s.client_items.items():
                if luid in mapped:
                    continue
                uid = next((by_number[n] for n in sorted(parsed["numbers"]) if n in by_number), None)
                if uid and not items.get(uid, {}).get("luid"):
                    ent = self._ensure(s, uid)
                    ent["luid"], ent["hash"] = luid, None
                    mapped.add(luid)
                    matched += 1
            say(f"Telefonda {len(s.client_items)} kişi var; {matched} tanesi iPhone'dakiyle eşleşti "
                f"(üzerine iPhone'daki hali yazılacak). Eşleşmeyenlere dokunulmaz.")
        else:
            for v in items.values():
                if v.get("luid") in s.client_deleted:
                    v["luid"], v["hash"] = None, None
                elif v.get("luid") in s.client_replaced:
                    v["hash"] = None

        def item(target=None, source=None, data=None):
            return E("Item", children=[loc("Target", target) if target else None,
                                       loc("Source", source) if source else None,
                                       E("Data", data) if data is not None else None])

        meta = lambda: E("Meta", children=[E("Type", VCARD_TYPE)])
        for uid, card in contacts.items():
            ent = self._ensure(s, uid)
            h = vcard.card_hash(card)
            if ent.get("luid"):
                if ent.get("hash") != h:
                    el = E("Replace", children=[meta(), item(target=ent["luid"], source=ent["guid"], data=card)])
                    s.queue.append((("Replace", uid, h), el))
            else:
                el = E("Add", children=[meta(), item(source=ent["guid"], data=card)])
                s.pending_add[ent["guid"]] = (uid, h)
                s.queue.append((("Add", uid, h), el))
        if contacts:
            for uid, ent in list(items.items()):
                if uid not in contacts:
                    if ent.get("luid"):
                        el = E("Delete", children=[meta(), item(target=ent["luid"], source=ent["guid"])])
                        s.queue.append((("Delete", uid, None), el))
                    else:
                        del items[uid]
        for (kind, _, _), _ in s.queue:
            s.stats[kind] += 1
        st = s.stats
        say(f"Gönderilecek: {st['Add']} yeni, {st['Replace']} güncelleme, {st['Delete']} silme "
            f"(iPhone'da numaralı {len(contacts)} kişi)")

    # --------------------------------------------------------- gönderim
    def _send_chunk(self, s, statuses, cmds, track):
        sync_el = E("Sync", children=[loc("Target", s.sync["client_db"]), loc("Source", s.sync["server_db"])])
        cmds.append(sync_el)
        base = len(self._encode(s, self._assemble(s, statuses, cmds, {}, final=True, dry=True)))
        budget = int(s.max_msg * 0.9) - base - 64
        added = 0
        while s.queue:
            info, el = s.queue[0]
            size = len(to_xml(el, "x")) + 16
            if added and size > budget:
                break
            sync_el.append(el)
            track[id(el)] = info
            budget -= size
            s.queue.pop(0)
            added += 1
        if s.queue:
            return self._send(s, statuses, cmds, track, final=False)
        s.phase = "map"
        return self._send(s, statuses, cmds, track, final=True)

    def _assemble(self, s, statuses, cmds, track, final, dry=False):
        msgid = str(s.out_msgid)
        hdr = E("SyncHdr", children=[
            E("VerDTD", s.verdtd), E("VerProto", s.verproto), E("SessionID", s.sid), E("MsgID", msgid),
            loc("Target", s.device), loc("Source", s.server_uri),
            E("Meta", children=[E("MaxMsgSize", 60000)])])
        body = E("SyncBody")
        n = 0

        def number(el):
            nonlocal n
            n += 1
            el.insert(0, E("CmdID", n))
            if not dry and id(el) in track:
                s.sent[(msgid, str(n))] = track[id(el)]

        for el in statuses + cmds:
            el = copy.deepcopy(el) if dry else el
            number(el)
            if el.tag in ("Sync", "Atomic", "Sequence"):
                for c in el:
                    if c.tag in CHANGE_CMDS:
                        number(c)
            body.append(el)
        if final:
            body.append(E("Final"))
        return E("SyncML", children=[hdr, body])

    def _encode(self, s, root):
        if s.enc == "wbxml":
            return wbxml.encode(root, s.pubid, s.wver, opaque_text=True)
        return to_xml(root, f"SYNCML:SYNCML{s.verdtd}")

    def _send(self, s, statuses, cmds, track, final):
        root = self._assemble(s, statuses, cmds, track, final)
        self._log(s, f"{s.out_msgid}-giden", root)
        ctype = "application/vnd.syncml+wbxml" if s.enc == "wbxml" else "application/vnd.syncml+xml"
        return self._encode(s, root), ctype

    def _commit(self, s):
        if s.state_key and s.state is not None:
            if s.pending_add:
                say(f"Uyarı: {len(s.pending_add)} yeni kişi için telefon kimlik bildirmedi; sonraki senkronda yeniden denenecek.")
                for guid, (uid, _) in s.pending_add.items():
                    if uid in s.state["items"]:
                        s.state["items"][uid]["luid"] = None
            s.state["client_next"] = s.sync["client_next"]
            s.state["server_next"] = s.server_next
            self.store.put(s.state_key, s.state)
        st = s.stats
        say(f"Senkron tamamlandı ✓  {st['Add']} eklendi, {st['Replace']} güncellendi, {st['Delete']} silindi.")
        s.phase = "done"


# ---------------------------------------------------------------- HTTP
def make_handler(app):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            data = self.rfile.read(n)
            try:
                out, ctype = app.handle(data, self.headers.get("Content-Type", ""))
            except Exception:
                traceback.print_exc()
                (app.logdir / f"{datetime.now():%Y%m%d-%H%M%S}-hatali-istek.bin").write_bytes(data)
                self.send_response(500)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def do_GET(self):
            msg = "nokia-sync calisiyor. Nokia'dan senkron baslat.\n".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)

        def log_message(self, *a):
            pass

    return Handler


def main():
    ap = argparse.ArgumentParser(description="iPhone kişilerini Nokia E60'a senkronlayan sunucu")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--kullanici", default="nokia")
    ap.add_argument("--sifre", default="", help="boş bırakılırsa şifre kontrolü yapılmaz")
    ap.add_argument("--vcf", help="Kişiler yerine bu .vcf dosyasını kullan")
    ap.add_argument("--onizle", action="store_true", help="kişileri oku, örnek göster, çık")
    args = ap.parse_args()

    try:
        contacts = load_contacts(args.vcf)
    except Exception as e:
        print(f"Kişiler okunamadı: {e}")
        sys.exit(1)
    if args.onizle:
        print(f"Numaralı {len(contacts)} kişi bulundu. Örnek kart:\n")
        if contacts:
            print(next(iter(contacts.values())))
        return

    ip = local_ip()
    handler = make_handler(SyncServer(args))
    srv = None
    for port in [args.port] + list(range(args.port + 1, args.port + 20)):
        try:
            srv = http.server.ThreadingHTTPServer(("0.0.0.0", port), handler)
            break
        except OSError:
            print(f"  {port} numaralı kapı dolu, bir sonrakini deniyorum...")
    if srv is None:
        print("Boş kapı bulunamadı.")
        sys.exit(1)
    args.port = port
    print(f"""
  Nokia senkron sunucusu çalışıyor — numaralı {len(contacts)} kişi hazır.

  Nokia E60'ta: Menü → Araçlar → Senk. → Seçenekler → Yeni senk. profili
    Profil adı ........ iPhone
    Taşıyıcı .......... İnternet
    Erişim noktası .... (ev Wi-Fi'ın)
    Ana bilgisayar adr. http://{ip}:{args.port}/sync
    Bağlantı noktası .. {args.port}
    Kullanıcı adı ..... {args.kullanici}
    Şifre ............. {args.sifre or '(herhangi bir şey)'}
    Kartvizit → Uzak veritabanı: contacts   (diğerlerini kapat)

  Durdurmak için Ctrl+C.
""", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nKapatıldı.")


if __name__ == "__main__":
    main()
