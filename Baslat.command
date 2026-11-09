#!/bin/zsh
# Çift tıkla: Nokia senkron sunucusunu başlatır.
cd "$(dirname "$0")"
python3 sunucu.py "$@"
echo; read "?Kapatmak için Enter'a bas..."
