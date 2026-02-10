#!/bin/bash
# Zonos 빌드에서 누락된 backbone 서브패키지를 복사합니다.
SITE_PACKAGES="$(python -c 'import site; print(site.getsitepackages()[0])')"
ZONOS_DIR="${SITE_PACKAGES}/zonos"

if [ ! -d "${ZONOS_DIR}/backbone" ]; then
    if [ ! -d /tmp/Zonos ]; then
        git clone --depth 1 https://github.com/Zyphra/Zonos.git /tmp/Zonos
    fi
    cp -R /tmp/Zonos/zonos/backbone "${ZONOS_DIR}/"
    echo "zonos/backbone 패치 완료"
else
    echo "zonos/backbone 이미 존재"
fi
