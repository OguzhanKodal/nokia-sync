"""Mac kişilerini Nokia S60'ın anladığı vCard 2.1'e çevirir; Nokia'dan gelen kartları okur."""
import hashlib
import quopri
import re

LABELS = {
    "_$!<Mobile>!$_": "CELL", "iPhone": "CELL", "Mobile": "CELL",
    "_$!<Home>!$_": "HOME;VOICE", "_$!<Work>!$_": "WORK;VOICE",
    "_$!<HomeFAX>!$_": "HOME;FAX", "_$!<WorkFAX>!$_": "WORK;FAX",
    "_$!<OtherFAX>!$_": "FAX", "_$!<Pager>!$_": "PAGER",
    "_$!<Main>!$_": "VOICE", "_$!<Other>!$_": "VOICE",
}


def _esc(v):
    return v.replace("\\", "\\\\").replace(";", "\\;").replace("\r", "").replace("\n", " ")


def _line(name, values):
    """values: alanların listesi (';' ile birleştirilir). ASCII değilse QP + UTF-8."""
    raw = ";".join(_esc(v) for v in values)
    try:
        raw.encode("ascii")
        return f"{name}:{raw}"
    except UnicodeEncodeError:
        qp = quopri.encodestring(raw.encode("utf-8")).decode("ascii")
        qp = qp.replace("=\n", "")  # yumuşak satır sonlarını kaldır, tek satır kalsın
        return f"{name};CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:{qp}"


def display_name(c):
    parts = [c.get("given", ""), c.get("middle", ""), c.get("family", "")]
    name = " ".join(p for p in parts if p).strip()
    if not name:
        name = c.get("org", "").strip()
    if not name and c.get("phones"):
        name = c["phones"][0]["number"]
    return name


def to_vcard21(c):
    lines = ["BEGIN:VCARD", "VERSION:2.1"]
    given, family, middle = c.get("given", ""), c.get("family", ""), c.get("middle", "")
    if not (given or family):
        given = display_name(c)
    lines.append(_line("N", [family, given, middle, "", ""]))
    lines.append(_line("FN", [display_name(c)]))
    for p in c.get("phones", []):
        kind = LABELS.get(p.get("label", ""), "VOICE")
        num = re.sub(r"[^\d+*#pw]", "", p["number"])
        if num:
            lines.append(f"TEL;{kind}:{num}")
    for e in c.get("emails", []):
        addr = e["address"].strip()
        if addr and addr.isascii():
            lines.append(f"EMAIL;INTERNET:{addr}")
    if c.get("org"):
        lines.append(_line("ORG", [c["org"]]))
    lines.append("END:VCARD")
    return "\r\n".join(lines) + "\r\n"


def card_hash(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def norm_number(n):
    d = re.sub(r"\D", "", n or "")
    return d[-10:] if len(d) >= 7 else ""


def parse_vcard(text):
    """Nokia'dan gelen kartı sadeleştirir: {'name': str, 'numbers': set}."""
    text = re.sub(r"=\r?\n", "", text)          # QP yumuşak satır devamı
    text = re.sub(r"\r?\n[ \t]", "", text)       # katlanmış satırlar
    name, fn, numbers = "", "", set()
    for line in text.splitlines():
        if ":" not in line:
            continue
        head, val = line.split(":", 1)
        parts = head.upper().split(";")
        if "ENCODING=QUOTED-PRINTABLE" in parts or "QUOTED-PRINTABLE" in parts:
            val = quopri.decodestring(val.encode("latin-1", "replace")).decode("utf-8", "replace")
        key = parts[0]
        if key == "TEL":
            n = norm_number(val)
            if n:
                numbers.add(n)
        elif key == "FN":
            fn = val.strip()
        elif key == "N":
            f = [x for x in val.split(";")]
            name = " ".join(x for x in (f[1:2] + f[0:1]) if x).strip()
    return {"name": fn or name, "numbers": numbers}


def _unescape(v):
    return v.replace("\\n", " ").replace("\\N", " ").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def _split(v):
    return [_unescape(x) for x in re.split(r"(?<!\\);", v)]


def _phone_label(types):
    t = {x.upper() for x in types}
    if "FAX" in t:
        return "_$!<HomeFAX>!$_" if "HOME" in t else "_$!<WorkFAX>!$_" if "WORK" in t else "_$!<OtherFAX>!$_"
    if "CELL" in t or "IPHONE" in t:
        return "_$!<Mobile>!$_"
    if "PAGER" in t:
        return "_$!<Pager>!$_"
    if "HOME" in t:
        return "_$!<Home>!$_"
    if "WORK" in t:
        return "_$!<Work>!$_"
    return "_$!<Main>!$_"


def read_vcf_file(path):
    """iCloud'dan dışa aktarılmış .vcf dosyasını Mac kişi biçimine (dict listesi) çevirir."""
    text = open(path, encoding="utf-8", errors="replace").read()
    text = re.sub(r"=\r?\n", "", text) if "QUOTED-PRINTABLE" in text.upper() else text
    text = re.sub(r"\r?\n[ \t]", "", text)
    contacts, cur = [], None
    for line in text.splitlines():
        if line.upper().startswith("BEGIN:VCARD"):
            cur = {"given": "", "family": "", "middle": "", "org": "", "phones": [], "emails": [], "uid": "", "fn": ""}
            continue
        if cur is None or ":" not in line:
            continue
        if line.upper().startswith("END:VCARD"):
            if not cur["given"] and not cur["family"] and not cur["org"] and cur["fn"]:
                cur["given"] = cur["fn"]
            key = cur["uid"] or card_hash(cur["family"] + cur["given"] + "".join(p["number"] for p in cur["phones"]))
            contacts.append({"id": key, **{k: cur[k] for k in ("given", "family", "middle", "org", "phones", "emails")}})
            cur = None
            continue
        head, val = line.split(":", 1)
        parts = head.split(";")
        name = parts[0].split(".")[-1].upper()
        params = []
        for p in parts[1:]:
            k, _, v = p.partition("=")
            params += (v or k).split(",")
        if any(p.upper() == "QUOTED-PRINTABLE" for p in params):
            val = quopri.decodestring(val.encode("latin-1", "replace")).decode("utf-8", "replace")
        if name == "N":
            f = _split(val) + ["", "", ""]
            cur["family"], cur["given"], cur["middle"] = f[0], f[1], f[2]
        elif name == "FN":
            cur["fn"] = _unescape(val)
        elif name == "ORG":
            cur["org"] = _split(val)[0]
        elif name == "TEL":
            cur["phones"].append({"label": _phone_label(params), "number": val.strip()})
        elif name == "EMAIL":
            cur["emails"].append({"label": "", "address": val.strip()})
        elif name == "UID":
            cur["uid"] = val.strip()
    return contacts
