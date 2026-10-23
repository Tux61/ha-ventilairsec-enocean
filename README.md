# Ventilairsec VMI EnOcean pour Home Assistant

Add-on Home Assistant pour piloter une VMI **Ventilairsec PureVent** en radio
EnOcean, sans Jeedom. Il s'adresse notamment aux propriétaires d'une **VMI Link Box**
(Jeedom) qui veulent passer à Home Assistant : l'intégration EnOcean de Home
Assistant ne connaît pas le profil propriétaire Ventilairsec (MSC D1-079).

L'add-on lit et décode les trames de la VMI et de son Assistant (sonde d'ambiance)
reçues par une clé EnOcean USB (TCM310), ou par le module de la Link Box partagé sur
le réseau (la Link Box devient alors une antenne placée près de la VMI). Il les publie
dans Home Assistant par MQTT et renvoie les commandes : mode, boost, vacances,
surventilation, consignes de préchauffage, remise à zéro du filtre, heure. Un second
port peut servir de secours automatique.

> Projet indépendant, non affilié à Ventilairsec ni à Jeedom SAS. Testé sur une seule
> installation (PureVent avec Hydro'R et Assistant). Retours bienvenus.

## Installation

[![Ajouter le dépôt à Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https://github.com/Tux61/ha-ventilairsec-enocean)

Ou : *Paramètres → Applications (Modules complémentaires) → Boutique → ⋮ → Dépôts*,
puis ajoutez l'URL de ce dépôt.

La documentation complète (identifiants à récupérer dans une sauvegarde Jeedom,
options, appairage, entités) est dans
[ventilairsec_enocean/DOCS.md](ventilairsec_enocean/DOCS.md). Elle s'affiche aussi
dans l'onglet *Documentation* de l'add-on.

## English

Home Assistant add-on for **Ventilairsec PureVent** ventilation units over EnOcean
(proprietary MSC D1-079 profile), replacing the Jeedom `openenocean` / `ventilairsec`
plugins (e.g. the *VMI Link Box*). It needs a TCM310 module (USB stick, or a module
shared over the network with ser2net, such as the Link Box's own) and an MQTT broker,
and exposes sensors and controls through MQTT discovery. An optional fallback port
takes over automatically. Documentation is in French.

## Licence

GPL v3 ou ultérieure (fichier [LICENSE](LICENSE)). Le décodage radio reprend celui
des plugins Jeedom `openenocean` et `ventilairsec` de Jeedom SAS, publiés sous la
même licence. Ventilairsec et PureVent sont des marques de leurs propriétaires.
