#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

if (( EUID == 0 )) || [[ $# -gt 1 || ( $# -eq 1 && $1 != --system ) ]]; then
    echo "Uso come utente normale: ./uninstall.sh [--system]" >&2
    exit 1
fi
data="$HOME/.local/share/phonecam"
config="${XDG_CONFIG_HOME:-$HOME/.config}"

if [[ -f $data/.phonecam-installation && ! -L $data ]]; then
    if [[ -f $config/systemd/user/phonecam.service ]]; then
        systemctl --user disable --now phonecam.service
    fi
    rm -f -- "$config/systemd/user/phonecam.service" "$config/autostart/phonecam.desktop" "$config/phonecam/receiver.env"
    rmdir -- "$config/phonecam" 2>/dev/null || true
    rm -rf -- "$data"
    systemctl --user daemon-reload
fi

if [[ ${1:-} == --system ]]; then
    module_options='options v4l2loopback devices=1 video_nr=-1 card_label="PhoneCam Camera" exclusive_caps=1'
    if cmp -s /etc/modprobe.d/phonecam.conf <(printf '%s\n' "$module_options") &&
        cmp -s /etc/modules-load.d/phonecam.conf <(printf '%s\n' v4l2loopback); then
        sudo rm -- /etc/modprobe.d/phonecam.conf /etc/modules-load.d/phonecam.conf
        echo "Configurazione webcam rimossa. Il dispositivo caricato scomparirà al riavvio."
    elif [[ -e /etc/modprobe.d/phonecam.conf || -e /etc/modules-load.d/phonecam.conf ]]; then
        echo "Configurazione webcam modificata o incompleta. Controlla i due file phonecam.conf." >&2
        exit 1
    fi
fi
