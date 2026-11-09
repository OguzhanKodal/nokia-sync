"""Nokia Suite modu için SyncML istemcisi: telefon sunucu, Mac istemcidir (USB/OBEX üzerinden)."""
import xml.etree.ElementTree as ET

import wbxml
from sunucu import E, loc, to_xml, VCARD_TYPE

PUBID = {"1.1": "-//SYNCML//DTD SyncML 1.1//EN", "1.2": "-//SYNCML//DTD SyncML 1.2//EN"}
CHANGE = ("Add", "Replace", "Delete")


class Client:
    def __init__(self, obex, ver="1.2", local_id="PC Suite", remote_id="Nokia", log=None):
        self.o, self.ver, self.local_id, self.remote_id = obex, ver, local_id, remote_id
        self.sid = "1"
        self.msgid = 0
        self.log = log or (lambda *a: None)
        self.resp_uri = remote_id
        self.max_msg = 60000

    def header(self):
        self.msgid += 1
        return E("SyncHdr", children=[
            E("VerDTD", self.ver), E("VerProto", f"SyncML/{self.ver}"), E("SessionID", self.sid),
            E("MsgID", self.msgid), loc("Target", self.resp_uri), loc("Source", self.local_id),
            E("Meta", children=[E("MaxMsgSize", 60000)])])

    def send(self, cmds, final=True):
        """cmds: CmdID'siz öğeler. Gönderir, telefonun cevabını (kök, gönderilen msgid, cmdid haritası) döndürür."""
        body = E("SyncBody")
        n = 0
        ids = {}

        def number(el):
            nonlocal n
            n += 1
            el.insert(0, E("CmdID", n))
            ids[id(el)] = str(n)

        for el in cmds:
            number(el)
            if el.tag == "Sync":
                for c in el:
                    if c.tag in CHANGE:
                        number(c)
            body.append(el)
        if final:
            body.append(E("Final"))
        root = E("SyncML", children=[self.header(), body])
        self.log("giden", root)
        data = wbxml.encode(root, PUBID[self.ver], 2, opaque_text=True)
        self.last_out = data
        self.o.put("application/vnd.syncml+wbxml", data)
        resp = self.o.get("application/vnd.syncml+wbxml")
        self.last_in = resp
        r, _ = wbxml.decode(resp)
        self.log("gelen", r)
        uri = r.findtext("SyncHdr/RespURI")
        if uri:
            self.resp_uri = uri
        return r, str(self.msgid), ids

    def status_for(self, resp, code_for=lambda cmd: 200):
        """Telefonun mesajındaki her komut için Status öğeleri üretir."""
        hdr = resp.find("SyncHdr")
        msgref = hdr.findtext("MsgID")
        out = [E("Status", children=[E("MsgRef", msgref), E("CmdRef", 0), E("Cmd", "SyncHdr"),
                                     E("TargetRef", hdr.findtext("Target/LocURI")),
                                     E("SourceRef", hdr.findtext("Source/LocURI")), E("Data", 200)])]
        for c in resp.find("SyncBody"):
            if c.tag in ("Status", "Final"):
                continue
            items = [c] + ([x for x in c if x.tag in CHANGE] if c.tag == "Sync" else [])
            for it in items:
                st = E("Status", children=[E("MsgRef", msgref), E("CmdRef", it.findtext("CmdID")), E("Cmd", it.tag)])
                src = it.findtext("Item/Source/LocURI")
                tgt = it.findtext("Item/Target/LocURI") or (it.findtext("Target/LocURI") if it.tag == "Sync" else None)
                if tgt:
                    st.append(E("TargetRef", tgt))
                if src:
                    st.append(E("SourceRef", src))
                st.append(E("Data", code_for(it)))
                if it.tag == "Alert" and it.findtext("Item/Meta/Anchor/Next"):
                    st.append(E("Item", children=[E("Data", children=[E("Anchor", children=[
                        E("Next", it.findtext("Item/Meta/Anchor/Next"))])])]))
                out.append(st)
        return out


def devinf(ver, local_db="contacts"):
    ctcap = E("CTCap", children=[E("CTType", VCARD_TYPE)])
    props = [("BEGIN", ["VCARD"], []), ("END", ["VCARD"], []), ("VERSION", ["2.1"], []), ("N", [], []),
             ("FN", [], []), ("TEL", [], ["HOME", "WORK", "CELL", "VOICE", "FAX", "PAGER"]),
             ("EMAIL", [], ["INTERNET"]), ("ORG", [], [])]
    for name, vals, params in props:
        ctcap.append(E("PropName", name))
        for v in vals:
            ctcap.append(E("ValEnum", v))
        for pn in params:
            ctcap.append(E("ParamName", pn))
    return E("DevInf", children=[
        E("VerDTD", ver), E("Man", "Nokia"), E("Mod", "PC Suite"), E("FwV"), E("SwV", "1.0"), E("HwV"),
        E("DevID", "PC Suite"), E("DevTyp", "workstation"), E("UTC"), E("SupportNumberOfChanges"),
        E("DataStore", children=[
            E("SourceRef", local_db), E("DisplayName", "Contacts"), E("MaxGUIDSize", 32),
            E("Rx-Pref", children=[E("CTType", VCARD_TYPE), E("VerCT", "2.1")]),
            E("Tx-Pref", children=[E("CTType", VCARD_TYPE), E("VerCT", "2.1")]),
            E("SyncCap", children=[E("SyncType", n) for n in (1, 2, 3, 4, 5, 6, 7)])]),
        ctcap])


def devinf_put(ver, local_db="contacts"):
    di = devinf(ver, local_db)
    uri = "./devinf12" if ver == "1.2" else "./devinf11"
    return E("Put", children=[E("Meta", children=[E("Type", "application/vnd.syncml-devinf+wbxml")]),
                              E("Item", children=[loc("Source", uri), E("Data", children=[di])])])


def results_for_get(resp, ver="1.1", local_db="contacts"):
    """Telefon cihaz bilgimizi istediyse (Get ./devinf11) Results öğelerini döndürür."""
    out = []
    msgref = resp.findtext("SyncHdr/MsgID")
    for g in resp.findall("SyncBody/Get"):
        uri = g.findtext("Item/Target/LocURI") or "./devinf11"
        out.append(E("Results", children=[E("MsgRef", msgref), E("CmdRef", g.findtext("CmdID")),
                                          E("Meta", children=[E("Type", "application/vnd.syncml-devinf+wbxml")]),
                                          E("Item", children=[loc("Source", uri), E("Data", children=[devinf(ver, local_db)])])]))
    return out


def statuses(resp):
    """Telefonun Status'larını {(CmdRef, Cmd): kod} olarak döndürür."""
    out = {}
    for s in resp.findall("SyncBody/Status"):
        out[(s.findtext("CmdRef"), s.findtext("Cmd"))] = s.findtext("Data")
    return out


def pretty(root):
    return to_xml(root, "x").decode("utf-8", "replace")
