#!/usr/bin/with-contenv bashio
# Récupère les identifiants du broker Mosquitto fournis par le Superviseur
export MQTT_HOST="$(bashio::services mqtt 'host')"
export MQTT_PORT="$(bashio::services mqtt 'port')"
export MQTT_USER="$(bashio::services mqtt 'username')"
export MQTT_PASSWORD="$(bashio::services mqtt 'password')"

bashio::log.info "Démarrage du pont Ventilairsec EnOcean (broker ${MQTT_HOST}:${MQTT_PORT})"
exec python3 -u /vmi_bridge.py /data/options.json
