#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

fail() { echo "PhoneCam: $*" >&2; exit 1; }

if [[ $# -lt 1 || $# -gt 2 || ( $# -eq 2 && $2 != --system ) ]]; then
    fail "Uso: ./install.sh https://utente.github.io [--system]"
fi
[[ $EUID != 0 ]] || fail "Avvia lo script come utente della sessione grafica."
origin=$1
[[ $origin =~ ^https://[a-z0-9]([a-z0-9.-]*[a-z0-9])?(:[1-9][0-9]{0,4})?$ && $origin != *:443 ]] ||
    fail "Indica l’origine HTTPS esatta, senza il percorso del repository né la barra finale."
if [[ $origin =~ :([0-9]+)$ ]] && (( BASH_REMATCH[1] > 65535 )); then
    fail "Porta dell’origine HTTPS non valida."
fi

# shellcheck source=/dev/null
source /etc/os-release
[[ ${ID:-} == linuxmint && ${VERSION_ID:-} == 22.* ]] ||
    fail "Installazione automatica prevista per Linux Mint 22.x."

project=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
data="$HOME/.local/share/phonecam"
config="${XDG_CONFIG_HOME:-$HOME/.config}"
marker="$data/.phonecam-installation"
module_conf=/etc/modprobe.d/phonecam.conf
module_load=/etc/modules-load.d/phonecam.conf
module_options='options v4l2loopback devices=1 video_nr=-1 card_label="PhoneCam Camera" exclusive_caps=1'
module_name=v4l2loopback
systemctl --user show-environment >/dev/null || fail "Avvia lo script dalla tua sessione Linux."

if [[ -L $data || ( -e $data && ! -f $marker ) ]]; then fail "Directory già occupata: $data"; fi
if [[ ! -f $marker ]]; then
    for path in "$config/phonecam/receiver.env" "$config/systemd/user/phonecam.service" "$config/autostart/phonecam.desktop"; do
        [[ ! -e $path && ! -L $path ]] || fail "File già presente: $path"
    done
fi

camera_present() {
    local name
    for name in /sys/devices/virtual/video4linux/video*/name; do
        if [[ -r $name && $(<"$name") == "PhoneCam Camera" ]]; then
            return 0
        fi
    done
    return 1
}

if [[ ${2:-} == --system ]]; then
    packages=(python3-venv ffmpeg zenity v4l-utils pipewire-bin pulseaudio-utils dbus-bin)
    if ! modinfo "$module_name" >/dev/null 2>&1; then
        headers="linux-headers-$(uname -r)"
        [[ -e /lib/modules/$(uname -r)/build/Makefile ]] || packages+=("$headers")
        packages+=(v4l2loopback-dkms)
    fi
    missing=()
    for package in "${packages[@]}"; do
        if [[ $(dpkg-query -W -f='${db:Status-Status}' "$package" 2>/dev/null) != installed ]]; then
            missing+=("$package")
        fi
    done
    if (( ${#missing[@]} )); then
        printf 'Pacchetti da installare con sudo: %s\n' "${missing[*]}"
        sudo apt-get install -- "${missing[@]}"
    fi

    if ! camera_present; then
        modinfo "$module_name" >/dev/null 2>&1 ||
            fail "Modulo assente per il kernel attuale. Controlla DKMS, header e Secure Boot."
        if [[ -e $module_conf || -e $module_load ]]; then
            if ! cmp -s "$module_conf" <(printf '%s\n' "$module_options") ||
                ! cmp -s "$module_load" <(printf '%s\n' "$module_name"); then
                fail "La configurazione phonecam.conf esistente richiede verifica manuale."
            fi
            [[ ! -d /sys/module/v4l2loopback ]] ||
                fail "v4l2loopback è già caricato. Riavvia per rileggere le opzioni esistenti."
            sudo modprobe "$module_name"
        else
            [[ ! -d /sys/module/v4l2loopback ]] ||
                fail "v4l2loopback è già caricato. Predisponi PhoneCam Camera preservando i dispositivi presenti, come descritto nel README."
            if modprobe -c | awk '/^(options|install|softdep)[[:space:]]+v4l2loopback([[:space:]]|$)/ { found=1 } END { exit !found }'; then
                fail "Sono già presenti opzioni per v4l2loopback. Integra PhoneCam Camera nella configurazione esistente seguendo il README."
            fi
            echo "Configurazione webcam virtuale e caricamento del modulo con sudo."
            sudo modprobe "$module_name" devices=1 video_nr=-1 'card_label=PhoneCam Camera' exclusive_caps=1
            camera_present || fail "Il modulo non ha creato il dispositivo PhoneCam Camera."
            printf '%s\n' "$module_options" | sudo install -m 0644 /dev/stdin "$module_conf"
            printf '%s\n' "$module_name" | sudo install -m 0644 /dev/stdin "$module_load"
        fi
    fi
fi

for command in ffmpeg zenity pw-loopback pactl pacat dbus-update-activation-environment; do
    command -v "$command" >/dev/null || fail "$command mancante. Usa --system per installare le dipendenze."
done
camera_present || fail "PhoneCam Camera assente. Usa --system per configurarla."
if ! pactl info >/dev/null 2>&1; then
    echo "PipeWire Pulse non risponde. Controlla pipewire-pulse.service e wireplumber.service." >&2
fi

install -d "$data" "$data/server" "$config/phonecam" "$config/systemd/user" "$config/autostart"
touch "$marker"
if ! python3 -m venv "$data/venv"; then
    fail "Creazione del venv fallita. Installa python3-venv."
fi
"$data/venv/bin/python" -m pip install -r "$project/requirements.txt"
install -m 0644 "$project"/server/*.py "$data/server/"
install -m 0644 "$project/systemd/phonecam.service" "$config/systemd/user/"
install -m 0644 "$project/systemd/phonecam.desktop" "$config/autostart/"
printf 'PHONECAM_ORIGINS=%s\n' "$origin" > "$config/phonecam/receiver.env"
systemctl --user daemon-reload
if [[ -n ${DISPLAY:-} || -n ${WAYLAND_DISPLAY:-} ]]; then
    dbus-update-activation-environment --systemd DISPLAY XAUTHORITY WAYLAND_DISPLAY
    systemctl --user restart phonecam.service
fi
echo "PhoneCam installato. Stato: systemctl --user status phonecam.service"
