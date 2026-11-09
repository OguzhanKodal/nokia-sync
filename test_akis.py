"""Nokia E60 gibi davranan sahte istemciyle uçtan uca senkron testi (ağ kullanmaz)."""
import shutil
import sys
import tempfile
import types
import xml.etree.ElementTree as ET
from pathlib import Path

import sunucu
import wbxml
from sunucu import E, loc

PUBID = "-//SYNCML//DTD SyncML 1.1//EN"

VCF1 = """BEGIN:VCARD
VERSION:3.0
UID:u-ahmet
N:Yılmaz;Ahmet;;;
TEL;type=CELL:+1 202 555 0101
END:VCARD
BEGIN:VCARD
VERSION:3.0
UID:u-sule
N:Öztürk;Şule;;;
TEL;type=HOME:+1 202 555 0102
TEL;type=WORK:+1 202 555 0103
EMAIL:sule@example.com
END:VCARD
BEGIN:VCARD
VERSION:3.0
UID:u-numarasiz
N:Boş;Kişi;;;
END:VCARD
""" + "".join(f"""BEGIN:VCARD
VERSION:3.0
UID:u-{i}
N:Kişi{i};Test;;;
TEL;type=CELL:+1 312 555 01{i:02d}
END:VCARD
""" for i in range(40))


class Phone:
    """Çok basit E60 benzetimi: luid -> vcard tutar."""

    def __init__(self, app, wbxml_mode=True, max_msg=3000):
        self.app, self.wb, self.max_msg = app, wbxml_mode, max_msg
        self.db = {"1": "BEGIN:VCARD\r\nVERSION:2.1\r\nN:Yilmaz;Ahmet\r\nTEL;CELL:12025550101\r\nEND:VCARD\r\n",
                   "2": "BEGIN:VCARD\r\nVERSION:2.1\r\nN:Annem\r\nTEL;CELL:12025550100\r\nEND:VCARD\r\n"}
        self.next_luid = 100
        self.anchor = None
        self.sid = 0

    def post(self, root):
        if self.wb:
            data, ctype = wbxml.encode(root, PUBID, 2), "application/vnd.syncml+wbxml"
        else:
            data, ctype = sunucu.to_xml(root, "SYNCML:SYNCML1.1"), "application/vnd.syncml+xml"
        out, octype = self.app.handle(data, ctype)
        assert len(out) <= max(self.max_msg, 60000), len(out)
        if "wbxml" in octype:
            r, _ = wbxml.decode(out)
        else:
            r = sunucu.strip_ns(ET.fromstring(out))
        return r, len(out)

    def msg(self, msgid, cmds, final=True, cred=False):
        hdr = E("SyncHdr", children=[E("VerDTD", "1.1"), E("VerProto", "SyncML/1.1"), E("SessionID", self.sid),
                                     E("MsgID", msgid), loc("Target", "http://mac:8080/sync"), loc("Source", "IMEI:000000000000000"),
                                     E("Cred", children=[E("Meta", children=[E("Format", "b64"), E("Type", "syncml:auth-basic")]),
                                                         E("Data", "bm9raWE6MTIzNA==")]) if cred else None,
                                     E("Meta", children=[E("MaxMsgSize", self.max_msg)])])
        body = E("SyncBody", children=cmds + ([E("Final")] if final else []))
        for i, c in enumerate(body):
            if c.tag != "Final":
                c.insert(0, E("CmdID", i + 1))
        return E("SyncML", children=[hdr, body])

    def sync(self, code=200, changes=()):
        self.sid += 1
        new_anchor = str(self.sid * 1000)
        alert = E("Alert", children=[E("Data", code), E("Item", children=[
            loc("Target", "contacts"), loc("Source", "./C\\Contacts.cdb"),
            E("Meta", children=[E("Anchor", children=[E("Last", self.anchor or "0"), E("Next", new_anchor)])])])])
        get = E("Get", children=[E("Meta", children=[E("Type", "application/vnd.syncml-devinf+wbxml")]),
                                 E("Item", children=[loc("Target", "./devinf11")])])
        r, _ = self.post(self.msg(1, [alert, get], cred=True))
        body = r.find("SyncBody")
        hdr_status = body.find("Status")
        assert hdr_status.findtext("Data") == "212", "kimlik doğrulama"
        srv_alert = [a for a in body.findall("Alert")][0]
        mode = int(srv_alert.findtext("Data"))
        assert body.find("Results/Item/Data/DevInf") is not None, "devinf yok"
        # paket 3: telefonun değişiklikleri
        sync = E("Sync", children=[loc("Target", "contacts"), loc("Source", "./C\\Contacts.cdb")])
        items = list(self.db.items()) if mode == 201 else list(changes)
        n = 10
        for luid, card in items:
            n += 1
            tag = "Replace" if mode == 201 else ("Delete" if card is None else "Replace")
            sync.append(E(tag, children=[E("CmdID", n), E("Item", children=[
                loc("Source", luid), E("Data", card) if card else None])]))
        r, _ = self.post(self.msg(2, [sync]))
        msgid, chunks = 2, 0
        mapping = []
        while True:
            body = r.find("SyncBody")
            chunks += 1
            replies = []
            srv_msgid = r.findtext("SyncHdr/MsgID")
            for s in body.findall("Sync"):
                for c in s:
                    if c.tag not in ("Add", "Replace", "Delete"):
                        continue
                    code = 200
                    if c.tag == "Add":
                        luid = str(self.next_luid)
                        self.next_luid += 1
                        self.db[luid] = c.findtext("Item/Data")
                        mapping.append((c.findtext("Item/Source/LocURI"), luid))
                        code = 201
                    elif c.tag == "Replace":
                        self.db[c.findtext("Item/Target/LocURI")] = c.findtext("Item/Data")
                    else:
                        self.db.pop(c.findtext("Item/Target/LocURI"), None)
                    replies.append(E("Status", children=[E("MsgRef", srv_msgid), E("CmdRef", c.findtext("CmdID")),
                                                         E("Cmd", c.tag), E("Data", code)]))
            msgid += 1
            if body.find("Final") is None:
                replies.append(E("Alert", children=[E("Data", 222)]))
                r, _ = self.post(self.msg(msgid, replies, final=False))
                continue
            mp = E("Map", children=[loc("Target", "contacts"), loc("Source", "./C\\Contacts.cdb")] + [
                E("MapItem", children=[loc("Target", g), loc("Source", l)]) for g, l in mapping])
            r, _ = self.post(self.msg(msgid, replies + [mp]))
            assert r.find("SyncBody/Final") is not None
            break
        self.anchor = new_anchor
        return mode, chunks


def names(db):
    import vcard
    return sorted(vcard.parse_vcard(c)["name"] for c in db.values())


def run(wb):
    tmp = Path(tempfile.mkdtemp())
    sunucu.HERE = tmp
    vcf = tmp / "k.vcf"
    vcf.write_text(VCF1)
    args = types.SimpleNamespace(vcf=str(vcf), kullanici="nokia", sifre="1234")
    app = sunucu.SyncServer(args)
    ph = Phone(app, wbxml_mode=wb)

    mode, chunks = ph.sync(200)
    assert mode == 201, mode
    assert chunks > 1, "parçalı gönderim denenmedi"
    nm = names(ph.db)
    assert "Annem" in nm and "Ahmet Yılmaz" in nm and "Şule Öztürk" in nm, nm
    assert len(ph.db) == 2 + 41, len(ph.db)  # Ahmet eşleşti, 41 yeni (Şule + 40), numarasız atlandı
    assert "=C3=96" in next(c for c in ph.db.values() if "sule" in c.lower() or "=C3=9E" in c), "QP Türkçe"
    print(f"  1. tur (ilk/yavaş): {len(ph.db)} kişi, {chunks} parça ✓")

    mode, _ = ph.sync(200)
    assert mode == 200
    print("  2. tur (değişiklik yok): normal senkron ✓")

    txt = VCF1.replace("+1 202 555 0102", "+1 202 555 0199").replace(
        "BEGIN:VCARD\nVERSION:3.0\nUID:u-ahmet\nN:Yılmaz;Ahmet;;;\nTEL;type=CELL:+1 202 555 0101\nEND:VCARD\n", "")
    txt += "BEGIN:VCARD\nVERSION:3.0\nUID:u-yeni\nN:Kaya;Zeynep;;;\nTEL:+12025550104\nEND:VCARD\n"
    vcf.write_text(txt)
    before = len(ph.db)
    mode, _ = ph.sync(200)
    nm = names(ph.db)
    assert mode == 200 and "Zeynep Kaya" in nm and "Ahmet Yılmaz" not in nm and "Annem" in nm, nm
    assert len(ph.db) == before, (len(ph.db), before)
    assert any("12025550199" in c for c in ph.db.values())
    print("  3. tur (ekle/sil/güncelle): ✓  (Nokia'ya özel 'Annem' korunuyor)")

    mode, _ = ph.sync(201)
    assert mode == 201 and len(ph.db) == before, len(ph.db)
    print("  4. tur (telefon yavaş senkron istedi): kopya oluşmadı ✓")
    shutil.rmtree(tmp)


if __name__ == "__main__":
    for wb in (True, False):
        print("WBXML" if wb else "XML")
        run(wb)
    print("Tüm testler geçti.")
