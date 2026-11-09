#!/usr/bin/env python3
"""Mac Kişiler'i, Depolama modunda bağlı Nokia'nın Others/Contacts klasörüne tek tek vCard olarak yazar."""
import re
import sys
from pathlib import Path

import sunucu


def find_phone():
    for v in Path("/Volumes").iterdir():
        if (v / "Others").is_dir() and (v / "Private").exists():
            return v
    return None


def main():
    phone = find_phone()
    if not phone:
        print("Nokia bulunamadı. Kabloyu tak ve telefonda 'Depolama' modunu seç.")
        return 1
    try:
        contacts = sunucu.load_contacts()
    except Exception as e:
        print(f"Kişiler okunamadı: {e}")
        return 1
    out = phone / "Others" / "Contacts"
    out.mkdir(parents=True, exist_ok=True)
    old = [f for f in out.glob("*.vcf") if not f.name.startswith("._")]
    for f in out.glob("*.vcf"):
        f.unlink(missing_ok=True)
    used = set()
    for i, card in enumerate(contacts.values(), 1):
        name = sunucu.vcard.parse_vcard(card)["name"] or f"kisi{i}"
        base = re.sub(r"[^\w\- ]", "", name, flags=re.UNICODE).strip()[:40] or f"kisi{i}"
        fn = base
        n = 2
        while fn.lower() in used:
            fn = f"{base} {n}"
            n += 1
        used.add(fn.lower())
        (out / f"{fn}.vcf").write_bytes(card.encode("utf-8"))
    (phone / "iPhone-Kisiler.vcf").write_bytes("".join(contacts.values()).encode("utf-8"))
    # macOS'un FAT diske bıraktığı "._" eş dosyaları Nokia'yı şaşırtır; temizle.
    for f in list(out.glob("._*")) + list(phone.glob("._iPhone-Kisiler.vcf")):
        f.unlink(missing_ok=True)
    print(f"\n✓ {len(contacts)} kişi telefona yazıldı.")
    print(f"  Tek tek: Others/Contacts   ·   Tek dosya: iPhone-Kisiler.vcf")
    print("\nŞimdi Finder'da telefonu çıkar (⏏), sonra kabloyu çek.")
    if old:
        print(f"  (klasördeki eski {len(old)} dosya değiştirildi)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
