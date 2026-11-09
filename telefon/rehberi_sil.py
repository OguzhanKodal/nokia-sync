# -*- coding: utf-8 -*-
# Nokia E6 (PyS60 2.0) icin: rehberin tamamini once E:\rehber_yedek.vcf dosyasina yedekler, sonra siler.
import appuifw
import contacts
import e32

YEDEK = u'E:\\rehber_yedek.vcf'
SONUC = u'E:\\rehberi_sil_sonuc.txt'


def yaz(metin):
    f = open(SONUC, 'w')
    f.write(metin)
    f.close()


def main():
    db = contacts.open()
    ids = list(db.keys())
    if not ids:
        appuifw.note(u'Rehber zaten bos.', 'info')
        yaz('bos\n')
        return
    if not appuifw.query(u'Rehberdeki %d kisinin TAMAMI silinecek.\nOnce yedek alinacak. Devam?' % len(ids), 'query'):
        yaz('iptal\n')
        return

    # 1) Yedek: yedek alinamazsa hicbir sey silinmez.
    appuifw.app.title = u'Yedek aliniyor...'
    e32.ao_yield()
    veri = db.export_vcards(tuple(ids))
    f = open(YEDEK, 'wb')
    if isinstance(veri, unicode):
        veri = veri.encode('utf-8')
    f.write(veri)
    f.close()
    kontrol = open(YEDEK, 'rb').read().count('BEGIN:VCARD')
    if kontrol < len(ids):
        appuifw.note(u'Yedek eksik (%d/%d), silme yapilmadi.' % (kontrol, len(ids)), 'error')
        yaz('yedek-eksik yedek=%d rehber=%d\n' % (kontrol, len(ids)))
        return

    if not appuifw.query(u'Yedek alindi (%d kisi).\nSimdi siliniyor, emin misin?' % kontrol, 'query'):
        yaz('iptal-yedekten-sonra yedek=%d\n' % kontrol)
        return

    # 2) Sil
    silindi = hata = 0
    for i in range(len(ids)):
        try:
            del db[ids[i]]
            silindi += 1
        except Exception:
            hata += 1
        if i % 25 == 0:
            appuifw.app.title = u'Siliniyor: %d / %d' % (i, len(ids))
            e32.ao_yield()

    kalan = len(db.keys())
    yaz('tamam yedek=%d silindi=%d hata=%d kalan=%d\n' % (kontrol, silindi, hata, kalan))
    appuifw.app.title = u'Rehber silindi'
    appuifw.note(u'Bitti: %d silindi, %d kaldi.\nSimdi kisi_aktar.py calistir.' % (silindi, kalan), 'conf')


try:
    main()
except Exception:
    import sys
    import traceback
    yaz('cokme ' + ''.join(traceback.format_exception(*sys.exc_info())))
    appuifw.note(u'Hata olustu, hicbir sey silinmemis olabilir. E:\\rehberi_sil_sonuc.txt', 'error')
