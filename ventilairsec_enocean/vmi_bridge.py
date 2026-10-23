#!/usr/bin/env python3
"""Pont EnOcean (ESP3 / TCM310) <-> MQTT pour VMI Ventilairsec PureVent.

Le protocole radio est celui du profil propriétaire MSC D1-079 (fabricant 0x079),
tel qu'implémenté par les plugins Jeedom openenocean / ventilairsec (Jeedom SAS, GPL v3+).

Copyright (C) 2026 les contributeurs de ce projet.
Ce programme est un logiciel libre : vous pouvez le redistribuer et/ou le modifier
selon les termes de la GNU General Public License publiée par la Free Software
Foundation, version 3 ou (à votre choix) toute version ultérieure. Il est distribué
SANS AUCUNE GARANTIE. Voir le fichier LICENSE.
"""
import json
import logging
import os
import queue
import sys
import threading
import time
from datetime import datetime

import paho.mqtt.client as mqtt
import serial

log = logging.getLogger("vmi")

# ---------------------------------------------------------------------------
# ESP3
# ---------------------------------------------------------------------------
SYNC = 0x55
PT_RADIO_ERP1 = 0x01
PT_RESPONSE = 0x02
PT_EVENT = 0x04
PT_COMMON_COMMAND = 0x05
CO_WR_RESET = 0x02
CO_WR_IDBASE = 0x07
CO_RD_IDBASE = 0x08
RORG_MSC = 0xD1
RORG_CDM = 0x40  # télégramme chaîné (message long découpé en morceaux)
RORG_UTE = 0xD4  # demande d'appairage (une VMI restée en mode appairage en diffuse une par minute)
MANUFACTURER = 0x079
# Le signal radio passe à « indisponible » après 30 min sans trame (VMI : une trame par minute
# environ, Assistant : toutes les 10 min) : un silence radio se voit comme un trou dans les graphiques.
RSSI_EXPIRE = 1800
# Port réseau (socket://) : reconnexion après ce silence, une connexion TCP coupée
# sans fermeture (passerelle redémarrée) ne lève aucune erreur
SOCKET_SILENCE = 600
# Port de secours (fallback_port) : bascule après ECHECS_REPLI connexions ratées,
# puis essai du port principal toutes les SONDE_PRINCIPAL secondes
ECHECS_REPLI = 3
SONDE_PRINCIPAL = 300


def crc8(data):
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def build_frame(ptype, data, optional=b""):
    header = bytes([len(data) >> 8, len(data) & 0xFF, len(optional), ptype])
    body = bytes(data) + bytes(optional)
    return bytes([SYNC]) + header + bytes([crc8(header)]) + body + bytes([crc8(body)])


class ESP3:
    def __init__(self, port):
        self.port = port
        self.ser = None
        self.buf = bytearray()
        self.responses = queue.Queue()
        self.write_lock = threading.Lock()
        self.on_radio = None

    def open(self):
        # serial_for_url accepte aussi socket://hôte:port (passerelle ser2net)
        self.ser = serial.serial_for_url(self.port, 57600, timeout=0.2)
        self.buf.clear()

    def close(self):
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None

    @classmethod
    def sonder(cls, port):
        """Vérifie qu'un port répond (lecture du Base ID), sans le garder ouvert."""
        esp = cls(port)
        stop = threading.Event()
        reader = None
        try:
            esp.open()
            reader = threading.Thread(target=esp.read_loop, args=(stop,), daemon=True)
            reader.start()
            return esp.read_base_id()[0]
        except Exception as exc:
            log.debug("Port %s toujours indisponible : %s", port, exc)
            return None
        finally:
            stop.set()
            if reader:
                reader.join(1)
            esp.close()

    def _send(self, ptype, data, optional=b"", timeout=2.0):
        if self.ser is None:
            raise RuntimeError("clé EnOcean non disponible")
        frame = build_frame(ptype, data, optional)
        with self.write_lock:
            while not self.responses.empty():
                self.responses.get_nowait()
            log.debug("TX %s", frame.hex())
            self.ser.write(frame)
            try:
                return self.responses.get(timeout=timeout)
            except queue.Empty:
                return None

    def read_base_id(self):
        resp = self._send(PT_COMMON_COMMAND, [CO_RD_IDBASE])
        if not resp or resp[0][0] != 0 or len(resp[0]) < 5:
            raise RuntimeError("Lecture du Base ID impossible (réponse %r)" % (resp,))
        data, opt = resp
        remaining = opt[0] if opt else None
        return data[1:5].hex().upper(), remaining

    def write_base_id(self, base_hex):
        resp = self._send(PT_COMMON_COMMAND, bytes([CO_WR_IDBASE]) + bytes.fromhex(base_hex), timeout=5)
        if not resp or resp[0][0] != 0:
            raise RuntimeError("Écriture du Base ID refusée (réponse %r)" % (resp,))
        self._send(PT_COMMON_COMMAND, [CO_WR_RESET], timeout=2)
        time.sleep(2)

    def send_radio(self, rorg, payload, sender_hex):
        data = bytes([rorg]) + bytes(payload) + bytes.fromhex(sender_hex) + bytes([0x80])
        optional = bytes([0x03, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00])
        resp = self._send(PT_RADIO_ERP1, data, optional)
        if resp is None:
            log.error("Pas de réponse de la clé à l'émission")
            return False
        if resp[0][0] != 0:
            log.error("La clé a refusé l'émission (code retour 0x%02X)", resp[0][0])
            return False
        return True

    def read_loop(self, stop):
        network = self.port.startswith("socket://")
        last_data = time.time()
        try:
            while not stop.is_set():
                chunk = self.ser.read(64)
                if chunk:
                    last_data = time.time()
                    self.buf.extend(chunk)
                    self._parse()
                elif network and time.time() - last_data > SOCKET_SILENCE:
                    log.warning("Aucune donnée de %s depuis %d min, reconnexion", self.port, SOCKET_SILENCE // 60)
                    return
        except Exception as exc:
            log.error("Erreur de lecture série : %s", exc)

    def _parse(self):
        buf = self.buf
        while True:
            try:
                start = buf.index(SYNC)
            except ValueError:
                buf.clear()
                return
            if start:
                del buf[:start]
            if len(buf) < 6:
                return
            header = bytes(buf[1:5])
            if crc8(header) != buf[5]:
                del buf[0]
                continue
            dlen = (header[0] << 8) | header[1]
            olen = header[2]
            ptype = header[3]
            total = 6 + dlen + olen + 1
            if len(buf) < total:
                return
            body = bytes(buf[6:6 + dlen + olen])
            if crc8(body) != buf[total - 1]:
                log.debug("CRC données invalide, trame ignorée")
                del buf[0]
                continue
            del buf[:total]
            data, opt = body[:dlen], body[dlen:]
            if ptype == PT_RESPONSE:
                self.responses.put((data, opt))
            elif ptype == PT_RADIO_ERP1 and self.on_radio:
                try:
                    self.on_radio(data, opt)
                except Exception:
                    log.exception("Erreur au décodage du télégramme %s", data.hex())
            else:
                log.info("Paquet ESP3 type 0x%02X non traité : %s | %s", ptype, data.hex(), opt.hex())


# ---------------------------------------------------------------------------
# Décodage du profil D1-079
# ---------------------------------------------------------------------------
def get_bits(payload, offset, size):
    total = len(payload) * 8
    if offset + size > total:
        return None
    return (int.from_bytes(payload, "big") >> (total - offset - size)) & ((1 << size) - 1)


def signed8(value):
    return value - 256 if value is not None and value > 127 else value


MODES = {0: "Standby", 1: "Eco", 2: "Standard", 3: "Confort"}
MODES_REV = {v: k for k, v in MODES.items()}
BYPASS = {0: "Toiture", 1: "Autre"}
BYPASS_REV = {v: k for k, v in BYPASS.items()}
SAISONS = {0: "Été", 1: "Hiver", 2: "Intermédiaire"}
ETATS = {0: "Boost", 1: "Vacances", 2: "Standby", 3: "Débit fixe", 4: "Normal"}
TYPES_CAISSON = {1: "Purevent", 2: "Urban", 3: "ERP"}
POSITIONS = {
    0: "Sortie des résistances",
    1: "Entrée d'air",
    2: "Sortie boucle d'eau",
    3: "Après panneau solaire",
    4: "Bypass",
}
POS_BYPASS = {0: "Inactif", 1: "Source chaude autre", 2: "Source chaude solaire",
              3: "Puits climatique", 4: "Manuel", 255: "Inconnu"}
PIECES = {0: "Inconnue", 1: "Salon", 2: "Cuisine", 3: "Salle de bain 1", 4: "Salle de bain 2",
          5: "Chambre 1", 6: "Chambre 2", 7: "Chambre 3", 8: "Chambre 4", 9: "Cellier",
          10: "Zone neutre"}
PROFILS = {1: "Assistant Ventilairsec", 2: "A5-04-01", 3: "A5-09-04", 4: "D2-04-08"}
ERREURS = {
    0x01: "Panne résistance", 0x02: "Trop froid pour chauffage", 0x10: "Panne moteur",
    0x20: "Filtre à changer", 0x30: "Panne d'un capteur QAI",
    **{0x30 + i: "Panne capteur QAI n°%d" % i for i in range(1, 10)},
    0x3A: "Problème sur plusieurs capteurs QAI", 0x3B: "Problème d'appairage d'un capteur QAI",
    0x40: "Problème inconnu sur l'assistant", 0x41: "Erreur de température de l'assistant",
    0x42: "Perte de communication de l'assistant", 0x43: "Piles faibles de l'assistant",
    0x44: "Plusieurs pannes sur l'assistant",
    0x51: "Panne sonde sortie résistances", 0x52: "Panne sonde entrée VMI",
    0x53: "Panne sonde sortie HydroR", 0x55: "Panne sonde sortie bypass",
    0x56: "Panne sonde air extérieur bypass", 0x57: "Panne sonde comble bypass",
    0x5F: "Plusieurs pannes sondes", 0x61: "Erreur bypass", 0x65: "Erreur téléchargement logiciel",
    0x70: "Multiples erreurs", 0x81: "Problème communication bypass",
    0x82: "Problème communication bypass", 0x83: "Volet du bypass bloqué",
}


def on_off(v):
    return None if v is None else ("ON" if v else "OFF")


def decode_vmi(cmd, p):
    """Retourne un dict clé -> valeur publiable pour une trame VMI."""
    b = lambda off, size=8: get_bits(p, off, size)  # noqa: E731
    out = {}
    if cmd == 0:
        out["mode"] = MODES.get(b(16), b(16))
        out["bypass"] = BYPASS.get(b(26, 1))
        out["plage_horaire"] = on_off(b(27, 1))
        out["debit_fixe"] = on_off(b(28, 1))
        out["surventilation"] = on_off(b(30, 1))
        out["vacances"] = on_off(b(31, 1))
        if b(31, 1) == 0:
            out["vacances_jours"] = 0
        boost = b(32)
        out["boost"] = on_off(boost not in (0, None))
        out["boost_restant"] = 0 if boost in (0, 255) else boost
        out["consigne_prechauffage"] = b(40)
        out["consigne_soufflage_max"] = b(48)
        out["consigne_hydror"] = b(56)
        out["consigne_confort"] = b(64)
        out["maj_logiciel"] = on_off(b(76, 1))
        out["appairage"] = on_off(b(77, 1))
        out["test"] = on_off(b(78, 1))
        out["saison"] = SAISONS.get(b(80), b(80))
        out["_fonc"] = b(24)  # octet de fonctions brut, réutilisé pour l'envoi
    elif cmd == 1:
        out["debit_air"] = b(16, 16)
        out["puissance_chauffage"] = b(32)
        out["etat_systeme"] = ETATS.get(b(40), b(40))
        out["vitesse_moteur"] = b(48)
    elif cmd == 2:
        out["temperature_exterieure"] = signed8(b(16))
        dp = b(24, 16)  # en dixièmes de pascal (l'export de l'appli donne 592 pour ~59 Pa mesurés)
        out["consigne_pression"] = None if dp is None else dp / 10
        out["encrassement_filtre"] = b(40)
        # l'application VMI+ affiche la durée de vie restante (80 % pour 20 % d'encrassement)
        out["filtre_restant"] = None if b(40) is None else 100 - b(40)
        out["rapport_cyclique_eau"] = b(48)
        out["duree_fonctionnement"] = b(56, 16)
    elif cmd == 3:
        idm = b(16, 32)
        out["id_machine"] = None if idm is None else "%08X" % idm
        out["test_global"] = "OK" if b(55, 1) else "KO"
        errs = []
        for key, off in (("code_erreur_1", 56), ("code_erreur_2", 64)):
            code = b(off)
            out[key] = None if code is None else "%02X" % code
            if code not in (None, 0, 255):
                errs.append(ERREURS.get(code, "Code %02X" % code))
        out["defaut"] = " / ".join(errs) if errs else "Aucun"
        out["probleme"] = on_off(bool(errs))
        out["semaines_depuis_maintenance"] = b(72)
        # L'appli VMI+ affiche 365 jours moins les jours écoulés depuis le changement de filtre ;
        # la radio ne donne que des semaines : on prend le milieu de la semaine (± 3 jours).
        if b(72) is not None:
            out["autonomie_filtre"] = max(0, 365 - 7 * b(72) - 3)
        out["consommation_cumulee"] = b(80)
    elif cmd == 4:
        out["type_caisson"] = TYPES_CAISSON.get(b(16), b(16))
        out["volume_ventile"] = b(32, 16)
        if b(64) is not None:
            out["date_installation"] = "%02d/%02d/20%02d" % (b(64), b(56), b(48))
        vlog = b(72)
        out["version_logiciel"] = None if vlog is None else "%d.%d" % (vlog >> 4, vlog & 0x0F)
        velec = b(80)
        out["version_electronique"] = None if velec is None else "%d.%d" % (velec >> 4, velec & 0x0F)
        out["hydror_present"] = on_off(b(31, 1))
        out["solarr_present"] = on_off(b(30, 1))
        out["bypass_amont_present"] = on_off(b(29, 1))
    elif cmd in (5, 6):
        base = 0 if cmd == 5 else 3
        count = 3 if cmd == 5 else 2
        for i in range(count):
            off = 16 + i * 24
            pos = b(off)
            if pos is None or pos == 255:
                continue
            out["sonde_%d_temperature" % pos] = signed8(b(off + 8))
            out["sonde_%d_humidite" % pos] = b(off + 16)
            out["_sonde_%d" % (base + i)] = pos
    elif cmd == 7:
        out["ouverture_bypass"] = b(16)
        out["position_bypass"] = POS_BYPASS.get(b(24), b(24))
        out["ouverture_vanne_hydror"] = b(64)
    elif cmd == 8:
        idc, piece, prof, idx = b(16, 32), b(48), b(56), b(64)
        if idc is not None and idx is not None:
            out["capteur_%d" % idx] = "%08X | %s | %s" % (
                idc, PIECES.get(piece, piece), PROFILS.get(prof, prof))
    return out


def decode_assistant(cmd, p):
    if cmd not in (1, 3):
        return {}
    raw_t = get_bits(p, 24, 16)
    batt = get_bits(p, 16, 8)
    return {
        "temperature": None if raw_t is None else round(raw_t / 100.0 - 40, 1),
        "humidite": get_bits(p, 40, 8),
        "batterie": (batt - 1) * 20 if batt in range(1, 7) else None,
    }


# ---------------------------------------------------------------------------
# Définition des entités Home Assistant
# ---------------------------------------------------------------------------
T, H, P = "temperature", "humidity", "%"
VMI_SENSORS = [
    # clé, nom, unité, device_class, state_class, icône, catégorie
    ("mode", "Mode (état)", None, None, None, "mdi:fan", None),
    ("etat_systeme", "État système", None, None, None, "mdi:state-machine", None),
    ("boost_restant", "Boost restant", "min", "duration", None, "mdi:timer-outline", None),
    ("debit_air", "Débit d'air", "m³/h", "volume_flow_rate", "measurement", "mdi:weather-windy", None),
    ("vitesse_moteur", "Vitesse moteur", None, None, "measurement", "mdi:speedometer", None),
    ("puissance_chauffage", "Puissance préchauffage", P, "power_factor", "measurement", "mdi:radiator", None),
    ("temperature_exterieure", "Température extérieure", "°C", T, "measurement", None, None),
    ("encrassement_filtre", "Encrassement filtre", P, None, "measurement", "mdi:air-filter", None),
    ("filtre_restant", "Filtre restant", P, None, "measurement", "mdi:air-filter", None),
    ("autonomie_filtre", "Autonomie filtre", "d", "duration", None, "mdi:calendar-clock", None),
    ("consigne_pression", "Consigne pression différentielle", "Pa", "pressure", "measurement", None, "diagnostic"),
    ("rapport_cyclique_eau", "Rapport cyclique boucle d'eau", P, None, "measurement", "mdi:water-sync", "diagnostic"),
    ("duree_fonctionnement", "Durée de fonctionnement", "d", "duration", "total_increasing", None, "diagnostic"),
    ("semaines_depuis_maintenance", "Semaines depuis maintenance", "sem", None, None, "mdi:wrench-clock", "diagnostic"),
    ("consommation_cumulee", "Consommation cumulée", None, None, "total_increasing", "mdi:flash", "diagnostic"),
    ("saison", "Saison", None, None, None, "mdi:sun-snowflake-variant", None),
    ("defaut", "Défaut", None, None, None, "mdi:alert", None),
    ("code_erreur_1", "Code erreur 1", None, None, None, None, "diagnostic"),
    ("code_erreur_2", "Code erreur 2", None, None, None, None, "diagnostic"),
    ("test_global", "Autotest", None, None, None, "mdi:check-circle", "diagnostic"),
    ("id_machine", "Identifiant machine", None, None, None, None, "diagnostic"),
    ("type_caisson", "Type de caisson", None, None, None, None, "diagnostic"),
    ("volume_ventile", "Volume ventilé", "m³", "volume", None, None, "diagnostic"),
    ("date_installation", "Date d'installation", None, None, None, None, "diagnostic"),
    ("version_logiciel", "Version logiciel", None, None, None, None, "diagnostic"),
    ("version_electronique", "Version électronique", None, None, None, None, "diagnostic"),
    ("ouverture_bypass", "Ouverture bypass", P, None, "measurement", "mdi:valve", None),
    ("position_bypass", "Position bypass", None, None, None, "mdi:valve", "diagnostic"),
    ("ouverture_vanne_hydror", "Ouverture vanne HydroR", P, None, "measurement", "mdi:valve", "diagnostic"),
    ("rssi", "Signal radio", "dBm", "signal_strength", "measurement", None, "diagnostic"),
    ("derniere_trame", "Dernière trame", None, "timestamp", None, None, "diagnostic"),
    ("cle_incident", "Incident clé EnOcean", None, None, None, "mdi:usb-port", "diagnostic"),
]
VMI_BINARY = [
    ("vacances", "Vacances", None, "mdi:palm-tree"),
    ("boost", "Boost", "running", "mdi:fan-plus"),
    ("probleme", "Problème", "problem", None),
    ("cle_probleme", "Problème clé EnOcean", "problem", "mdi:usb-port"),
    ("hydror_present", "HydroR présent", None, None),
    ("solarr_present", "SolarR présent", None, None),
    ("bypass_amont_present", "Bypass amont présent", None, None),
    ("maj_logiciel", "Mise à jour logiciel", "update", None),
    ("appairage", "Mode appairage", None, "mdi:link-variant"),
]
VMI_SWITCHES = [
    # clé, nom, bit dans l'octet de fonctions (b7..b0 = 00 BYP PLH DEBF 0 SURV VAC)
    ("plage_horaire", "Programmation horaire", 4, "mdi:calendar-clock"),
    ("debit_fixe", "Débit fixe", 3, "mdi:fan-clock"),
    ("surventilation", "Surventilation", 1, "mdi:fan-chevron-up"),
]
VMI_NUMBERS = [
    # clé, nom, champ de commande, min, max, step, unité, icône
    ("consigne_prechauffage", "Préchauffage résistance", "TEMPEL", 0, 18, 1, "°C", "mdi:radiator"),
    ("consigne_soufflage_max", "Température max soufflage", "TEMPSOUF", 20, 45, 1, "°C", "mdi:thermometer-high"),
    ("consigne_hydror", "Préchauffage Hydro'R", "TEMPHYD", 8, 28, 1, "°C", "mdi:water-thermometer"),
    ("consigne_confort", "Consigne SolarR (non utilisée)", "TEMPSOL", 18, 30, 1, "°C", "mdi:solar-power"),
]
INCIDENT_DUREE = 1800  # s sans nouvel incident avant de lever l'alerte
ORDER = ["MODEFONC", "FONC", "VACS", "BOOST", "TEMPEL", "TEMPSOUF", "TEMPHYD", "TEMPSOL", "COMMAND"]


class Bridge:
    def __init__(self, opts):
        self.opts = opts
        self.prefix = opts.get("topic_prefix", "ventilairsec").strip("/")
        self.vmi_id = opts["vmi_id"].upper()
        self.assistant_id = (opts.get("assistant_id") or "").upper()
        self.sender_opt = (opts.get("sender_id") or "").upper()
        self.sender_id = self.sender_opt
        self.ports = [opts["serial_port"]]
        if opts.get("fallback_port"):
            self.ports.append(opts["fallback_port"])
        self.esp = ESP3(self.ports[0])
        self.esp.on_radio = self.on_radio
        self.state = {}
        self.chains = {}
        self.id_candidat = None  # identifiant machine différent, en attente de confirmation
        self.state[("vmi", "cle_probleme")] = "OFF"
        self.state[("vmi", "cle_incident")] = "Aucun"
        self.dernier_incident = 0
        self.announced = set()
        self.stop = threading.Event()
        self.mqtt = self._make_mqtt()

    # ---------------- MQTT ----------------
    def _make_mqtt(self):
        cid = "ventilairsec-%s" % self.vmi_id
        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cid)
        except AttributeError:  # paho-mqtt 1.x
            client = mqtt.Client(client_id=cid)
        user = os.environ.get("MQTT_USER")
        if user:
            client.username_pw_set(user, os.environ.get("MQTT_PASSWORD"))
        client.will_set(self.t("status"), "offline", retain=True)
        client.on_connect = self.on_connect
        client.on_message = self.on_message
        return client

    def t(self, *parts):
        return "/".join((self.prefix,) + parts)

    def on_connect(self, client, userdata, flags, *args):
        log.info("Connecté au broker MQTT")
        client.publish(self.t("status"), "online", retain=True)
        client.subscribe(self.t("vmi", "set", "#"))
        client.subscribe("homeassistant/status")
        self.announced.clear()
        self.publish_discovery()
        for key, value in list(self.state.items()):
            self.publish_value(*key, value)

    def on_message(self, client, userdata, msg):
        payload = msg.payload.decode(errors="replace").strip()
        if msg.topic == "homeassistant/status":
            if payload == "online":
                self.announced.clear()
                self.publish_discovery()
                for key, value in list(self.state.items()):
                    self.publish_value(*key, value)
            return
        name = msg.topic.rsplit("/", 1)[-1]
        log.info("Commande reçue : %s = %s", name, payload)
        try:
            self.handle_command(name, payload)
        except Exception:
            log.exception("Échec de la commande %s", name)

    def publish_value(self, dev, key, value):
        if value is None:
            return
        self.mqtt.publish(self.t(dev, key), str(value), retain=True)

    # ---------------- Découverte HA ----------------
    def device(self, dev):
        if dev == "vmi":
            return {"identifiers": ["ventilairsec_%s" % self.vmi_id],
                    "name": "VMI Ventilairsec", "manufacturer": "Ventilairsec",
                    "model": "PureVent", "serial_number": self.vmi_id}
        return {"identifiers": ["ventilairsec_%s" % self.assistant_id],
                "name": "Assistant Ventilairsec", "manufacturer": "Ventilairsec",
                "model": "Assistant VMI (D1-079-00)", "serial_number": self.assistant_id,
                "via_device": "ventilairsec_%s" % self.vmi_id}

    def announce(self, component, dev, key, cfg):
        uid = "ventilairsec_%s_%s" % (self.vmi_id if dev == "vmi" else self.assistant_id, key)
        if uid in self.announced:
            return
        cfg = {k: v for k, v in cfg.items() if v is not None}
        cfg.update({
            "unique_id": uid,
            "object_id": "vmi_%s" % key if dev == "vmi" else "vmi_assistant_%s" % key,
            "device": self.device(dev),
            "availability_topic": self.t("status"),
        })
        self.mqtt.publish("homeassistant/%s/%s/config" % (component, uid), json.dumps(cfg), retain=True)
        self.announced.add(uid)

    def publish_discovery(self):
        for key, name, unit, dc, sc, icon, cat in VMI_SENSORS:
            self.announce("sensor", "vmi", key, {
                "name": name, "state_topic": self.t("vmi", key), "unit_of_measurement": unit,
                "device_class": dc, "state_class": sc, "icon": icon, "entity_category": cat,
                "expire_after": RSSI_EXPIRE if key == "rssi" else None})
        for key, name, dc, icon in VMI_BINARY:
            self.announce("binary_sensor", "vmi", key, {
                "name": name, "state_topic": self.t("vmi", key), "device_class": dc, "icon": icon,
                "entity_category": None if key in ("vacances", "boost", "probleme", "cle_probleme")
                else "diagnostic"})
        self.announce("select", "vmi", "mode_select", {
            "name": "Mode", "icon": "mdi:fan", "options": list(MODES.values()),
            "state_topic": self.t("vmi", "mode"), "command_topic": self.t("vmi", "set", "mode")})
        self.announce("select", "vmi", "bypass", {
            "name": "Bypass", "icon": "mdi:valve", "options": list(BYPASS.values()),
            "state_topic": self.t("vmi", "bypass"), "command_topic": self.t("vmi", "set", "bypass")})
        for key, name, _bit, icon in VMI_SWITCHES:
            self.announce("switch", "vmi", key, {
                "name": name, "icon": icon, "state_topic": self.t("vmi", key),
                "command_topic": self.t("vmi", "set", key)})
        for key, name, _field, lo, hi, step, unit, icon in VMI_NUMBERS:
            self.announce("number", "vmi", key, {
                "name": name, "icon": icon, "min": lo, "max": hi, "step": step,
                "unit_of_measurement": unit, "mode": "box", "entity_category": "config",
                "state_topic": self.t("vmi", key), "command_topic": self.t("vmi", "set", key)})
        self.announce("number", "vmi", "boost_duree", {
            "name": "Boost (durée)", "icon": "mdi:fan-plus", "min": 0, "max": 200, "step": 5,
            "unit_of_measurement": "min", "mode": "box",
            "state_topic": self.t("vmi", "boost_restant"), "command_topic": self.t("vmi", "set", "boost")})
        self.announce("number", "vmi", "vacances_jours", {
            "name": "Vacances (jours)", "icon": "mdi:palm-tree", "min": 0, "max": 254, "step": 1,
            "unit_of_measurement": "d", "mode": "box",
            "state_topic": self.t("vmi", "vacances_jours"),
            "command_topic": self.t("vmi", "set", "vacances")})
        for key, name, icon, cat in (
                ("reset_filtre", "Réinitialiser filtre", "mdi:air-filter", "config"),
                ("sync_horloge", "Synchroniser l'horloge", "mdi:clock-check", "config")):
            self.announce("button", "vmi", key, {
                "name": name, "icon": icon, "entity_category": cat,
                "command_topic": self.t("vmi", "set", key)})
        if self.assistant_id:
            for key, name, unit, dc in (("temperature", "Température", "°C", T),
                                        ("humidite", "Humidité", P, H),
                                        ("batterie", "Batterie", P, "battery"),
                                        ("rssi", "Signal radio", "dBm", "signal_strength")):
                self.announce("sensor", "assistant", key, {
                    "name": name, "state_topic": self.t("assistant", key), "unit_of_measurement": unit,
                    "device_class": dc, "state_class": "measurement",
                    "entity_category": "diagnostic" if key in ("batterie", "rssi") else None,
                    "expire_after": RSSI_EXPIRE if key == "rssi" else None})

    def announce_dynamic(self, key):
        if key.startswith("sonde_"):
            pos = int(key.split("_")[1])
            label = POSITIONS.get(pos, "position %d" % pos)
            if key.endswith("temperature"):
                cfg = {"name": "Température %s" % label.lower(), "unit_of_measurement": "°C",
                       "device_class": T}
            else:
                cfg = {"name": "Humidité %s" % label.lower(), "unit_of_measurement": P,
                       "device_class": H}
            cfg.update({"state_topic": self.t("vmi", key), "state_class": "measurement"})
            self.announce("sensor", "vmi", key, cfg)
        elif key.startswith("capteur_"):
            self.announce("sensor", "vmi", key, {
                "name": "Capteur appairé %s" % key.split("_")[1], "icon": "mdi:access-point",
                "state_topic": self.t("vmi", key), "entity_category": "diagnostic"})

    # ---------------- Réception radio ----------------
    def reassemble(self, data, opt):
        """Recolle un message chaîné (RORG 0x40). Retourne la trame complète ou None.

        La clé inclut le destinataire : la VMI parle aussi à l'Assistant avec les mêmes
        numéros de séquence, et un morceau perdu ferait sinon recoller deux messages."""
        sender_status = data[-5:]
        seq, idx = data[1] >> 6, data[1] & 0x3F
        key = (data[-5:-1], bytes(opt[1:5]), seq)
        if idx == 0:
            self.chains[key] = {"len": (data[2] << 8) | data[3], "parts": {0: data[4:-5]},
                                "time": time.time()}
        elif key in self.chains:
            self.chains[key]["parts"][idx] = data[2:-5]
        else:
            return None
        chain = self.chains[key]
        if time.time() - chain["time"] > 5:
            del self.chains[key]
            return None
        parts = chain["parts"]
        if sorted(parts) != list(range(len(parts))):
            return None
        full = b"".join(parts[i] for i in range(len(parts)))
        if len(full) < chain["len"]:
            return None
        del self.chains[key]
        return full[:chain["len"]] + sender_status

    def on_radio(self, data, opt):
        if data[0] == RORG_CDM and len(data) >= 7:
            data = self.reassemble(data, opt)
            if data is None:
                return
        rorg = data[0]
        sender = data[-5:-1].hex().upper()
        payload = data[1:-5]
        dest = opt[1:5].hex().upper() if len(opt) >= 6 else "?"
        rssi = -opt[5] if len(opt) >= 6 else None
        if sender not in (self.vmi_id, self.assistant_id):
            log.debug("Trame d'un autre équipement %s (RORG %02X) : %s", sender, rorg, data.hex())
            return
        if rorg == RORG_UTE:
            # Ignorée, mais son niveau de réception compte : une VMI en mode appairage en diffuse
            # une par minute, ce qui donne une mesure du signal par minute (utile pour placer la clé)
            log.debug("Demande d'appairage de %s ignorée : %s", sender, data.hex())
            if sender == self.vmi_id and rssi is not None:
                log.info("Signal VMI (trame d'appairage) : %s dBm", rssi)
                self.state[("vmi", "rssi")] = rssi
                self.announce_dynamic("rssi")
                self.publish_value("vmi", "rssi", rssi)
            return
        if rorg != RORG_MSC or len(payload) < 2 or get_bits(payload, 0, 12) != MANUFACTURER:
            log.info("Trame non décodée de %s (RORG %02X, dest %s) : %s", sender, rorg, dest, data.hex())
            return
        cmd = payload[1] & 0x0F
        dev = "vmi" if sender == self.vmi_id else "assistant"
        if dev == "vmi" and dest == self.assistant_id:
            log.info("Trame VMI -> Assistant (cmd=%d) ignorée : %s", cmd, payload.hex())
            return
        values = decode_vmi(cmd, payload) if dev == "vmi" else decode_assistant(cmd, payload)
        if dev == "vmi" and cmd == 3 and not self.id_machine_ok(values.get("id_machine"), payload):
            return
        log.info("RX %s cmd=%d dest=%s rssi=%s %s", dev, cmd, dest, rssi,
                 {k: v for k, v in values.items() if not k.startswith("_")})
        values["rssi"] = rssi
        if dev == "vmi":
            values["derniere_trame"] = datetime.now().astimezone().isoformat(timespec="seconds")
        for key, value in values.items():
            self.state[(dev, key)] = value
            if key.startswith("_"):
                continue
            if dev == "vmi":
                self.announce_dynamic(key)
            self.publish_value(dev, key, value)

    def id_machine_ok(self, idm, payload):
        """L'identifiant machine ne change jamais : une trame d'état qui en porte un autre est
        une trame mal recollée. Un nouvel identifiant n'est adopté que s'il revient deux fois."""
        connu = self.state.get(("vmi", "id_machine"))
        if connu is None or idm == connu or idm == self.id_candidat:
            self.id_candidat = None
            return True
        self.id_candidat = idm
        log.warning("Trame d'état VMI ignorée (identifiant %s au lieu de %s) : %s", idm, connu, payload.hex())
        self.incident("Trame VMI incohérente reçue : la clé est peut-être lue par un autre programme")
        return False

    # ---------------- Incidents de la clé ----------------
    def incident(self, raison):
        """Signale à HA un comportement anormal de la clé (souvent un autre programme qui la lit,
        comme l'intégration EnOcean native). Retombe seul après INCIDENT_DUREE sans récidive."""
        log.warning("Incident clé EnOcean : %s", raison)
        self.dernier_incident = time.time()
        for key, value in (("cle_probleme", "ON"), ("cle_incident", raison)):
            self.state[("vmi", key)] = value
            self.publish_value("vmi", key, value)

    def fin_incident(self):
        if self.state.get(("vmi", "cle_probleme")) == "ON" and time.time() - self.dernier_incident > INCIDENT_DUREE:
            log.info("Plus d'incident clé EnOcean depuis %d min", INCIDENT_DUREE // 60)
            for key, value in (("cle_probleme", "OFF"), ("cle_incident", "Aucun")):
                self.state[("vmi", key)] = value
                self.publish_value("vmi", key, value)

    # ---------------- Émission ----------------
    def send_cmd0(self, **fields):
        payload = bytes([0x07, 0x90] + [int(fields.get(name, 0xFF)) & 0xFF for name in ORDER])
        log.info("TX commande %s -> %s", fields, payload.hex())
        return self.emettre(payload)

    def emettre(self, payload):
        if self.esp.send_radio(RORG_MSC, payload, self.sender_id):
            return True
        self.incident("La clé n'a pas confirmé une émission : elle est peut-être lue par un autre programme")
        return False

    def send_clock(self):
        now = datetime.now()
        payload = bytes([0x07, 0x91, now.year % 100, now.month, now.day,
                         now.hour, now.minute, now.second])
        log.info("Synchronisation horloge VMI : %s", now.strftime("%Y-%m-%d %H:%M:%S"))
        return self.emettre(payload)

    def handle_command(self, name, value):
        numbers = {n[0]: n for n in VMI_NUMBERS}
        switches = {s[0]: s for s in VMI_SWITCHES}
        if name == "mode":
            if value not in MODES_REV:
                raise ValueError("Mode inconnu : %s" % value)
            if self.send_cmd0(MODEFONC=MODES_REV[value]):
                self.optimistic("mode", value)
        elif name == "boost":
            minutes = max(0, min(200, int(float(value))))
            if self.send_cmd0(BOOST=minutes):
                self.optimistic("boost_restant", minutes)
                self.optimistic("boost", on_off(minutes > 0))
        elif name == "vacances":
            days = max(0, min(254, int(float(value))))
            if self.send_cmd0(VACS=days):
                self.optimistic("vacances", on_off(days > 0))
                self.optimistic("vacances_jours", days)
        elif name in numbers:
            key, _n, field, lo, hi = numbers[name][:5]
            val = max(lo, min(hi, int(float(value))))
            if self.send_cmd0(**{field: val}):
                self.optimistic(key, val)
        elif name in switches or name == "bypass":
            fonc = self.state.get(("vmi", "_fonc"))
            if fonc is None:
                log.warning("État des fonctions encore inconnu : attendez la première trame de la VMI")
                return
            if name == "bypass":
                bit, on = 5, BYPASS_REV.get(value) == 1
            else:
                bit, on = switches[name][2], value.upper() == "ON"
            fonc = (fonc & 0b00111010)  # le bit vacances (b0) n'est pas renvoyé, comme Jeedom
            fonc = fonc | (1 << bit) if on else fonc & ~(1 << bit)
            if self.send_cmd0(FONC=fonc):
                self.state[("vmi", "_fonc")] = fonc | (self.state[("vmi", "_fonc")] & 1)
                self.optimistic(name, value if name == "bypass" else on_off(on))
        elif name == "reset_filtre":
            self.send_cmd0(COMMAND=4)
        elif name == "sync_horloge":
            self.send_clock()
        else:
            log.warning("Commande inconnue : %s", name)

    def optimistic(self, key, value):
        self.state[("vmi", key)] = value
        self.publish_value("vmi", key, value)

    # ---------------- Boucle principale ----------------
    def run(self):
        host = os.environ.get("MQTT_HOST", "core-mosquitto")
        port = int(os.environ.get("MQTT_PORT") or 1883)
        self.mqtt.connect_async(host, port, keepalive=60)
        self.mqtt.loop_start()
        echecs = 0
        actif = 0  # index dans self.ports : 0 = port principal, 1 = port de secours
        while not self.stop.is_set():
            self.esp.port = self.ports[actif]
            fin = threading.Event()  # arrête la lecture de cette connexion seulement
            reader = None
            try:
                self.esp.open()
                reader = threading.Thread(target=self.esp.read_loop, args=(fin,), daemon=True)
                reader.start()
                base_id, remaining = self.esp.read_base_id()
                log.info("Clé EnOcean OK sur %s - Base ID %s (changements restants : %s)",
                         self.esp.port, base_id, remaining)
                echecs = 0
                wanted = (self.opts.get("force_base_id") or "").upper()
                if wanted and wanted != base_id:
                    log.warning("Écriture du Base ID %s dans la clé (opération limitée à 10 fois)", wanted)
                    self.esp.write_base_id(wanted)
                    base_id, remaining = self.esp.read_base_id()
                    log.warning("Nouveau Base ID %s (changements restants : %s)", base_id, remaining)
                # Sans sender_id, chaque clé émet avec son propre Base ID
                self.sender_id = self.sender_opt or base_id
                log.info("Identifiant d'émission utilisé : %s", self.sender_id)
                last_clock = 0
                last_sonde = time.time()
                while reader.is_alive() and not self.stop.is_set():
                    if self.opts.get("sync_clock", True) and time.time() - last_clock > 3600:
                        last_clock = time.time()
                        self.send_clock()
                    if actif:
                        # Sur le port de secours : l'alerte reste levée, et on guette le retour du principal
                        self.dernier_incident = time.time()
                        if time.time() - last_sonde > SONDE_PRINCIPAL:
                            last_sonde = time.time()
                            if ESP3.sonder(self.ports[0]):
                                log.warning("Le port principal %s répond de nouveau, retour dessus", self.ports[0])
                                actif = 0
                                break
                    self.fin_incident()
                    reader.join(5)
                else:
                    log.error("Lecture série interrompue")
            except Exception as exc:
                log.error("Erreur clé EnOcean (%s) : %s", self.esp.port, exc)
                echecs += 1
                if echecs == ECHECS_REPLI:
                    echecs = 0
                    if len(self.ports) > 1:
                        actif = 1 - actif
                        self.incident("%s ne répond plus : bascule sur %s" % (self.ports[1 - actif], self.ports[actif]))
                    elif self.esp.port.startswith("socket://"):
                        self.incident("La passerelle réseau %s ne répond pas" % self.esp.port)
                    else:
                        self.incident("La clé ne répond pas au démarrage : elle est peut-être utilisée par un autre "
                                      "programme (intégration EnOcean native de Home Assistant ?) ou débranchée")
            finally:
                fin.set()
                if reader:
                    reader.join(1)
                self.esp.close()
            if self.ports[actif] != self.esp.port:
                continue  # changement de port : pas d'attente
            self.stop.wait(10)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "/data/options.json"
    with open(path) as fh:
        opts = json.load(fh)
    logging.basicConfig(level=getattr(logging, opts.get("log_level", "info").upper()),
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    if not opts.get("vmi_id") or not opts.get("serial_port"):
        log.error("Renseignez le port de la clé (serial_port) et l'identifiant EnOcean de la VMI (vmi_id) "
                  "dans l'onglet Configuration : voir la documentation")
        sys.exit(1)
    Bridge(opts).run()


if __name__ == "__main__":
    main()
