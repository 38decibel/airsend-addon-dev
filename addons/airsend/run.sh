#!/usr/bin/with-contenv bashio
set -e

# --- Reprend fidelement la logique de detection d'archi de l'addon d'origine ---
# (mapping des noms d'archi apk vers les noms de dossier du tarball AirSendWebService)
arch="$(apk --print-arch)"
case "$arch" in \
    aarch64) arch='arm64' ;; \
    armhf) arch='armhf' ;; \
    armv7) arch='arm' ;; \
    amd64) arch='x86_64' ;; \
    i386) arch='x86' ;; \
esac
ulimit -n 4096
bashio::log.info "AirSendWebService arch: ${arch}"

# --- Config -> variables d'env consommees par main.py ---
export MQTT_HOST=$(bashio::services mqtt "host")
export MQTT_PORT=$(bashio::services mqtt "port")
export MQTT_USER=$(bashio::services mqtt "username")
export MQTT_PASS=$(bashio::services mqtt "password")
export MQTT_SSL=$(bashio::config 'mqtt.ssl' 'false')
export BOXES_JSON=$(bashio::config 'boxes' | jq -c .)
export LOG_LEVEL=$(bashio::config 'system.log_level' 'INFO')

# --- Demarre AirSendWebService ---
# Invocation ("... 99399") reprise telle quelle de la branche "SUPERVISOR_TOKEN
# present" de l'addon d'origine, qui est toujours notre cas puisqu'on tourne
# sous Supervisor. La signification exacte de l'argument "99399" n'est pas
# documentee (probablement un port de controle interne different du port
# HTTP applicatif, qui lui reste fixe sur 33863 quoi qu'il arrive - confirme
# par curl reel lors du diagnostic). On ne modifie pas cette invocation
# puisqu'elle est celle empiriquement validee.
cd /opt/airsend
./bin/unix/${arch}/AirSendWebService 99399 &
ASW_PID=$!
bashio::log.info "AirSendWebService started (PID: ${ASW_PID})"

bashio::log.info "Waiting for AirSendWebService on 127.0.0.1:33863..."
for i in $(seq 1 30); do
    if wget -q -O /dev/null "http://127.0.0.1:33863/service/status" 2>/dev/null; then
        bashio::log.info "AirSendWebService is up."
        break
    fi
    sleep 1
done

# --- Surveille AirSendWebService en fond. S'il meurt, on arrete tout le
# conteneur plutot que de laisser tourner l'app Python sans moteur RF
# fonctionnel - Supervisor redemarrera l'addon selon sa politique habituelle
# (meme logique de fond que le moniteur de l'addon d'origine, adaptee ici en
# "on tue le process 1" plutot qu'en boucle infinie avec exit 1 en fin de
# script). ---
(
    while kill -0 "$ASW_PID" 2>/dev/null; do
        sleep 10
    done
    bashio::log.error "AirSendWebService (PID: ${ASW_PID}) died, stopping addon..."
    kill -TERM 1
) &

# --- Demarre l'app Python en foreground (devient le PID 1 logique du
# conteneur suite a l'exec ci-dessous ; le moniteur ci-dessus reste un
# processus enfant independant, non affecte par cet exec). ---
cd /app
exec python3 main.py
