"""SyncML için küçük WBXML kodlayıcı/çözücü.

Ağaç gösterimi: xml.etree.ElementTree öğeleri, ad alanı olmadan (ör. "SyncML", "Anchor").
DevInf belgeleri SyncML içinde OPAQUE olarak gömülü ayrı bir WBXML belgesidir.
"""
import xml.etree.ElementTree as ET

SYNCML_TAGS = """Add Alert Archive Atomic Chal Cmd CmdID CmdRef Copy Cred Data Delete Exec Final Get
Item Lang LocName LocURI Map MapItem Meta MsgID MsgRef NoResp NoResults Put Replace RespURI
Results Search Sequence SessionID SftDel Source SourceRef Status Sync SyncBody SyncHdr SyncML
Target TargetRef Reserved VerDTD VerProto NumberOfChanges MoreData Field Filter Record FilterType
SourceParent TargetParent Move Correlator""".split()
METINF_TAGS = """Anchor EMI Format FreeID FreeMem Last Mark MaxMsgSize Mem MetInf Next NextNonce
SharedMem Size Type Version MaxObjSize FieldLevel""".split()
DEVINF_TAGS = """CTCap CTType DataStore DataType DevID DevInf DevTyp DisplayName DSMem Ext FwV HwV
Man MaxGUIDSize MaxID MaxMem Mod OEM ParamName PropName Rx Rx-Pref SharedMem Size SourceRef SwV
SyncCap SyncType Tx Tx-Pref ValEnum VerCT VerDTD XNam XVal UTC SupportNumberOfChanges
SupportLargeObjs
Property PropParam MaxOccur NoTruncate Reserved2F Filter-Rx FilterCap FilterKeyword FieldLevel
SupportHierarchicalSync""".split()

# token -> ad (her tablo 0x05'ten başlar)
PAGES_SYNCML = {0: {i + 5: n for i, n in enumerate(SYNCML_TAGS)},
                1: {i + 5: n for i, n in enumerate(METINF_TAGS)}}
PAGES_DEVINF = {0: {i + 5: n for i, n in enumerate(DEVINF_TAGS)}}

SWITCH_PAGE, END, STR_I, STR_T, OPAQUE = 0x00, 0x01, 0x03, 0x83, 0xC3

PUBID_NAMES = {
    0x0FD1: "-//SYNCML//DTD SyncML 1.0//EN", 0x0FD3: "-//SYNCML//DTD SyncML 1.1//EN",
    0x1201: "-//SYNCML//DTD SyncML 1.2//EN",
    0x0FD2: "-//SYNCML//DTD DevInf 1.0//EN", 0x0FD4: "-//SYNCML//DTD DevInf 1.1//EN",
    0x1203: "-//SYNCML//DTD DevInf 1.2//EN",
}


class WbxmlError(Exception):
    pass


def _read_mb(buf, pos):
    val = 0
    while True:
        if pos >= len(buf):
            raise WbxmlError("mb_u_int32 eksik")
        b = buf[pos]
        pos += 1
        val = (val << 7) | (b & 0x7F)
        if not b & 0x80:
            return val, pos


def _mb(val):
    out = [val & 0x7F]
    val >>= 7
    while val:
        out.append(0x80 | (val & 0x7F))
        val >>= 7
    return bytes(reversed(out))


def _cstr(buf, pos):
    end = buf.index(0, pos)
    return buf[pos:end], end + 1


def parse_header(buf):
    """(sürüm, publicid_metni, charset, strtbl, gövde_başlangıcı) döndürür."""
    version = buf[0]
    pubid, pos = _read_mb(buf, 1)
    pubid_index = None
    if pubid == 0:
        pubid_index, pos = _read_mb(buf, pos)
    charset, pos = _read_mb(buf, pos)
    tlen, pos = _read_mb(buf, pos)
    strtbl = buf[pos:pos + tlen]
    pos += tlen
    if pubid == 0:
        name = strtbl[pubid_index:strtbl.index(0, pubid_index)].decode("ascii", "replace")
    else:
        name = PUBID_NAMES.get(pubid, hex(pubid))
    return version, name, charset, strtbl, pos


def decode(buf):
    """WBXML baytlarını (kök öğe, publicid_metni) olarak çözer."""
    version, pubid, charset, strtbl, pos = parse_header(buf)
    pages = PAGES_DEVINF if "DevInf" in pubid else PAGES_SYNCML
    enc = "utf-8" if charset in (0, 106) else "latin-1"
    page = 0
    stack, root = [], None

    def add_text(s):
        cur = stack[-1]
        if len(cur):
            last = cur[-1]
            last.tail = (last.tail or "") + s
        else:
            cur.text = (cur.text or "") + s

    while pos < len(buf):
        tok = buf[pos]
        pos += 1
        if tok == SWITCH_PAGE:
            page = buf[pos]
            pos += 1
        elif tok == END:
            stack.pop()
            if not stack:
                break
        elif tok == STR_I:
            s, pos = _cstr(buf, pos)
            add_text(s.decode(enc, "replace"))
        elif tok == STR_T:
            idx, pos = _read_mb(buf, pos)
            add_text(strtbl[idx:strtbl.index(0, idx)].decode(enc, "replace"))
        elif tok == OPAQUE:
            ln, pos = _read_mb(buf, pos)
            data = bytes(buf[pos:pos + ln])
            pos += ln
            sub = None
            if data[:1] in (b"\x01", b"\x02", b"\x03"):
                try:
                    sub, subid = decode(data)
                    if "DevInf" not in subid:
                        sub = None
                except Exception:
                    sub = None
            if sub is not None:
                stack[-1].append(sub)
            else:
                add_text(data.decode("utf-8", "replace"))
        elif tok in (0x02, 0x04, 0x40, 0x41, 0x42, 0x43, 0x80, 0x81, 0x82, 0xC0, 0xC1, 0xC2):
            raise WbxmlError(f"desteklenmeyen WBXML işareti 0x{tok:02x}")
        else:
            ident = tok & 0x3F
            name = pages.get(page, {}).get(ident, f"Unknown_{page}_{ident:02x}")
            el = ET.Element(name)
            if stack:
                stack[-1].append(el)
            else:
                root = el
            if tok & 0x80:
                raise WbxmlError("öznitelikli öğe desteklenmiyor")
            if tok & 0x40:
                stack.append(el)
            elif not stack:
                break
    return root, pubid


def _name_to_token(pages):
    m = {}
    for p, table in pages.items():
        for t, n in table.items():
            m.setdefault(n, (p, t))
    return m


_SYNCML_REV = _name_to_token(PAGES_SYNCML)
_DEVINF_REV = _name_to_token(PAGES_DEVINF)


def encode(root, pubid_text, version=0x02, opaque_text=False):
    """Kök öğeyi WBXML'e kodlar. Public id her zaman dize tablosundan verilir."""
    devinf = "DevInf" in pubid_text
    rev = _DEVINF_REV if devinf else _SYNCML_REV
    numeric = {v: k for k, v in PUBID_NAMES.items()}.get(pubid_text)
    out = bytearray([version])
    if numeric is not None:
        out += _mb(numeric) + _mb(106) + _mb(0)
    else:
        tbl = pubid_text.encode("ascii") + b"\x00"
        out += _mb(0) + _mb(0) + _mb(106) + _mb(len(tbl)) + tbl
    state = {"page": 0}

    def text(s):
        if s:
            if opaque_text:
                b = s.encode("utf-8")
                out.append(OPAQUE)
                out.extend(_mb(len(b)) + b)
            else:
                out.append(STR_I)
                out.extend(s.encode("utf-8") + b"\x00")

    def el(e):
        if e.tag == "DevInf" and not devinf:
            sub = encode(e, pubid_for_devinf(e), version, opaque_text)
            out.append(OPAQUE)
            out.extend(_mb(len(sub)) + sub)
            text(e.tail)
            return
        if e.tag not in rev:
            raise WbxmlError(f"bilinmeyen öğe {e.tag}")
        page, tok = rev[e.tag]
        if page != state["page"]:
            out.extend([SWITCH_PAGE, page])
            state["page"] = page
        has_content = bool(e.text) or len(e) > 0
        out.append(tok | (0x40 if has_content else 0))
        if has_content:
            text(e.text)
            for c in e:
                el(c)
            out.append(END)
        text(e.tail)

    el(root)
    return bytes(out)


def pubid_for_devinf(e):
    v = (e.findtext("VerDTD") or "1.1").strip()
    return f"-//SYNCML//DTD DevInf {v}//EN"
