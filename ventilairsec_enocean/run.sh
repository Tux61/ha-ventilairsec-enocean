#!/usr/bin/with-contenv bashio
# Broker MQTT : externe si mqtt_host est renseigné, sinon celui fourni par le Superviseur
if bashio::config.has_value 'mqtt_host'; then
    export MQTT_HOST="$(bashio::config 'mqtt_host')"
    export MQTT_PORT="$(bashio::config 'mqtt_port' 1883)"
    export MQTT_USER="$(bashio::config 'mqtt_user' '')"
    export MQTT_PASSWORD="$(bashio::config 'mqtt_password' '')"
elif bashio::services.available 'mqtt'; then
    export MQTT_HOST="$(bashio::services mqtt 'host')"
    export MQTT_PORT="$(bashio::services mqtt 'port')"
    export MQTT_USER="$(bashio::services mqtt 'username')"
    export MQTT_PASSWORD="$(bashio::services mqtt 'password')"
else
    bashio::exit.nok "Aucun broker MQTT : installez l'add-on Mosquitto broker ou renseignez mqtt_host"
fi

bashio::log.info "Démarrage du pont Ventilairsec EnOcean (broker ${MQTT_HOST}:${MQTT_PORT})"
exec python3 -u /vmi_bridge.py /data/options.json
