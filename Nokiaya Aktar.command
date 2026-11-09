#!/bin/zsh
# Çift tıkla: iPhone (iCloud) kişilerini kabloyla Nokia E6'ya hazırlar.
cd "$(dirname "$0")"
.venv/bin/python nokiaya_aktar.py
echo; read "?Kapatmak için Enter'a bas..."
