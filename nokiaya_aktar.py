"""Mac Kişiler'i (iCloud) güncel haliyle Nokia E6'nın E:\\iPhone-Kisiler.vcf dosyasına kabloyla yazar.
Telefon 'Nokia Suite' modunda bağlı olmalı. Sonra telefonda Python → kisi_aktar.py çalıştırılır."""
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import obex_usb  # noqa: E402
import sunucu  # noqa: E402

print("1/3 iCloud kişileri eşitleniyor (Kişiler uygulaması 30 sn açık kalacak)...")
subprocess.run(["open", "-g", "-a", "Contacts"])
time.sleep(30)

print("2/3 Kişiler okunuyor...")
contacts = sunucu.load_contacts()
data = "".join(contacts.values()).encode("utf-8")
print(f"    numaralı {len(contacts)} kişi")

print("3/3 Telefona yazılıyor...")
try:
    o = obex_usb.ftp()
except Exception as e:
    print(f"\nTelefona bağlanılamadı: {e}\nKabloyu tak, telefonda 'Nokia Suite' modunu seç, tekrar çift tıkla.")
    sys.exit(1)
try:
    o.setpath("E:")
    o.put_file("iPhone-Kisiler.vcf", data)
    ok = o.get_file("iPhone-Kisiler.vcf") == data
finally:
    o.disconnect()
    o.close()
if not ok:
    print("\nUYARI: Telefondaki dosya doğrulanamadı, tekrar dene.")
    sys.exit(1)
print(f"""
✓ {len(contacts)} kişi telefona yazıldı ve doğrulandı.

Telefonda:
  • Sadece yenileri eklemek için: Python → Run script → kisi_aktar.py
  • Rehberi baştan kurmak için:   önce rehberi_sil.py, sonra kisi_aktar.py
""")
