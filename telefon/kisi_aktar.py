# -*- coding: utf-8 -*-
# Nokia E6 (PyS60 2.0) icin: E:\iPhone-Kisiler.vcf icindeki kisileri rehbere ekler.
# Ayni numarasi zaten rehberde olan kisiyi atlar; ikinci kez calistirmak kopya olusturmaz.
import appuifw
import contacts
import e32
import os

KAYNAK = u'E:\\iPhone-Kisiler.vcf'
SONUC = u'E:\\kisi_aktar_sonuc.txt'


def rakamlar(s):
    d = ''.join([c for c in s if c.isdigit()])
    if len(d) >= 7:
        return d[-10:]
    return ''


def kart_numaralari(kart):
    out = []
    for satir in kart.splitlines():
        if satir.upper().startswith('TEL'):
            n = rakamlar(satir.split(':', 1)[-1])
            if n:
                out.append(n)
    return out


def rehber_numaralari(db):
    var = {}
    try:
        for cid in db.keys():
            try:
                for alan in db[cid]:
                    try:
                        if 'number' in alan.type:
                            n = rakamlar(alan.value)
                            if n:
                                var[n] = 1
                    except Exception:
                        pass
            except Exception:
                pass
    except Exception:
        pass
    return var


def yaz(metin):
    f = open(SONUC, 'w')
    f.write(metin)
    f.close()


def main():
    if not os.path.exists(KAYNAK):
        appuifw.note(u'Dosya yok: E:\\iPhone-Kisiler.vcf', 'error')
        yaz('dosya-yok\n')
        return
    f = open(KAYNAK, 'rb')
    data = f.read()
    f.close()
    kartlar = []
    for parca in data.split('END:VCARD'):
        if 'BEGIN:VCARD' in parca:
            kartlar.append(parca[parca.index('BEGIN:VCARD'):].strip() + '\r\nEND:VCARD\r\n')

    db = contacts.open()
    var = rehber_numaralari(db)
    once = len(db.keys())
    soru = u'%d kisi rehbere eklensin mi?\nRehberde su an %d kisi var.' % (len(kartlar), once)
    if not appuifw.query(soru, 'query'):
        yaz('iptal once=%d\n' % once)
        return

    eklendi = atlandi = hata = 0
    ilk_hata = ''
    for i in range(len(kartlar)):
        kart = kartlar[i]
        nums = kart_numaralari(kart)
        if nums and all([n in var for n in nums]):
            atlandi += 1
        else:
            try:
                db.import_vcards(unicode(kart))
                eklendi += 1
                for n in nums:
                    var[n] = 1
            except Exception:
                hata += 1
                if not ilk_hata:
                    import sys
                    ilk_hata = repr(sys.exc_info()[1])
        if i % 25 == 0:
            appuifw.app.title = u'Kisiler: %d / %d' % (i, len(kartlar))
            e32.ao_yield()

    sonra = len(db.keys())
    yaz('tamam eklendi=%d atlandi=%d hata=%d once=%d sonra=%d ilk_hata=%s\n'
        % (eklendi, atlandi, hata, once, sonra, ilk_hata))
    appuifw.app.title = u'Kisiler aktarildi'
    appuifw.note(u'Bitti: %d eklendi, %d zaten vardi, %d hata' % (eklendi, atlandi, hata), 'conf')


try:
    main()
except Exception:
    import sys
    import traceback
    yaz('cokme ' + ''.join(traceback.format_exception(*sys.exc_info())))
    appuifw.note(u'Hata olustu, E:\\kisi_aktar_sonuc.txt', 'error')
