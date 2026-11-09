#!/bin/zsh
# Çift tıkla: Mac kişilerini kabloyla bağlı Nokia'ya yazar.
cd "$(dirname "$0")"
python3 telefona_yaz.py
echo; read "?Kapatmak için Enter'a bas..."
