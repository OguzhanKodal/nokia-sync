# nokia-sync

iPhone kişilerini (iCloud → Mac Kişiler) eski bir **Nokia E6-00**'ın (Symbian Anna) rehberine aktarır.
iPhone kişileri SIM'e yazamıyor ve Apple dışı cihaza Bluetooth ile dosya gönderemiyor; bu yüzden arada Mac var.

```
iPhone → iCloud → Mac Kişiler → (USB kablo) → Nokia E:\iPhone-Kisiler.vcf → telefonda Python → Rehber
```

## Gerekenler

**Mac'te**
- macOS, Xcode komut satırı araçları (`swiftc` için: `xcode-select --install`)
- Python 3 ve libusb: `brew install libusb`
- Proje klasöründe bir kez:
  ```bash
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
  ```
- İlk çalıştırmada Terminal'e **Kişiler** izni ver (Sistem Ayarları → Gizlilik ve Güvenlik → Kişiler).

**Telefonda (bir kez)**
- **PyS60 2.0**: `Python_2.0.0.sis` + `PythonScriptShell_2.0.0_3_2.sis`
  (Internet Archive'daki `symbian_dev` koleksiyonunda, sertifika hatası düzeltilmiş paket).
- `telefon/` klasöründeki iki programı telefonun `E:\data\python\` klasörüne kopyala.

## Kullanım
1. Telefonu kabloyla tak, telefonda **Nokia Suite** modunu seç.
2. Mac'te **Nokiaya Aktar.command** dosyasına çift tıkla. iCloud'u eşitler, numaralı kişileri vCard 2.1 olarak
   telefonun `E:\iPhone-Kisiler.vcf` dosyasına yazar ve geri okuyarak doğrular.
3. Telefonda **Python → Seçenekler → Run script**:
   - `kisi_aktar.py` → yalnız yeni kişileri ekler (numarası rehberde olanı atlar, kopya yapmaz).
   - Rehberi baştan kurmak için önce `rehberi_sil.py` (önce `E:\rehber_yedek.vcf` yedeği alır), sonra `kisi_aktar.py`.
   - Sonuçlar: `E:\kisi_aktar_sonuc.txt`, `E:\rehberi_sil_sonuc.txt`.

## Dosyalar
| Dosya | Ne yapar |
|---|---|
| `nokiaya_aktar.py` | Tek tık aktarıcı (Mac tarafı) |
| `obex_usb.py` | Nokia'nın USB OBEX kanalı: SyncML ve dosya aktarımı (PC Suite Services / FTP) |
| `kisiler.swift` | Mac Kişiler okuyucu (ilk çalıştırmada otomatik derlenir) |
| `vcard.py` | vCard 2.1 dönüşümü; Türkçe karakterler `CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE` |
| `telefon/` | Telefonda çalışan PyS60 programları |
| `at_modem.py` | USB modem kapısına AT komutu gönderen yardımcı |

### Denenip çalışmayanlar (öğrenmek isteyen için duruyor)
- `sunucu.py` + `Baslat.command`: Wi-Fi üzerinden SyncML sunucusu. Telefon bağlanıyor ama E6 rehberine yazmadı.
- `istemci.py` + `san.py`: USB üzerinden SyncML (telefon sunucu, Mac istemci). Alert kabul, Sync her denemede 500.
- `telefona_yaz.py`: Depolama modunda dosya kopyalama. E6, çok kişili .vcf'den yalnız ilk kişiyi alıyor.
- `wbxml.py`: SyncML için küçük WBXML kodlayıcı/çözücü (libwbxml ile çapraz doğrulandı).
- `test_akis.py`: Sahte telefonla uçtan uca SyncML testi (`python3 test_akis.py`). İçindeki numaralar hayalidir.

## Bilinen sınırlar
- `kisi_aktar.py` yalnız ekler: iPhone'da adı değişen ya da silinen kişi Nokia'da güncellenmez; bunun için sil + aktar.
- Numarasız kişiler aktarılmaz. iPhone'da aynı numarayla birden çok kayıt varsa yalnız ilki eklenir.
- Yalnız Nokia E6-00 (Symbian Anna) üzerinde denendi.

## Gizlilik
Program kişilerini hiçbir sunucuya göndermez; her şey Mac ile telefon arasında kabloyla olur.
`.gitignore`, oturum kayıtlarının ve `.vcf` dosyalarının repoya girmesini engeller.
