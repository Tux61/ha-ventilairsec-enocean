# Ventilairsec VMI EnOcean

Pont entre une VMI Ventilairsec PureVent et Home Assistant, en remplacement des
plugins Jeedom `openenocean` + `ventilairsec` (par exemple ceux de la VMI Link Box).
L'add-on parle directement à la clé EnOcean (TCM310, protocole ESP3) et publie la
VMI dans Home Assistant via MQTT, avec la découverte automatique.

Projet indépendant, non affilié à Ventilairsec ni à Jeedom SAS. Testé sur une seule
installation : utilisez-le en connaissance de cause.

## Prérequis

- Une clé EnOcean USB à puce TCM310 (USB 300 ou équivalent). Si votre ancien Jeedom
  utilisait déjà une clé USB, reprenez-la : la VMI lui obéit en général sans autre réglage. Le
  module de la VMI Link Box, lui, n'est pas réutilisable (voir *Venir d'une VMI Link Box*).
- Un broker MQTT : l'add-on *Mosquitto broker* et l'intégration *MQTT* de Home Assistant.
- **Ne pas** configurer l'intégration EnOcean native de Home Assistant sur cette clé :
  un seul programme peut ouvrir le port série. Si Home Assistant la propose en
  découverte, choisissez *Ignorer*. Si les deux se disputent la clé, l'add-on reçoit
  des trames corrompues et lève le capteur *Problème clé EnOcean*.

## Venir d'une VMI Link Box

Dans la VMI Link Box, le module radio (EnOcean TCM310) est soudé sur une carte
branchée sur le connecteur GPIO du boîtier (Rock Pi 4C). Il n'est pas utilisable
depuis Home Assistant : il faut une clé EnOcean USB (USB 300 ou équivalent, à puce
TCM310).

La VMI reste appairée à l'identifiant d'émission de la Link Box. Pour que la clé USB
soit obéie sans réappairer la VMI, donnez-lui le Base ID de la Link Box :

1. Relevez l'`actionid` de la VMI dans la sauvegarde Jeedom (voir ci-dessous).
2. Mettez dans `force_base_id` cet `actionid` arrondi au multiple de 0x80 inférieur
   (l'`actionid` lui-même s'il se termine par `00` ou `80`).
3. Si l'`actionid` ne se termine pas par `00` ou `80`, mettez-le dans `sender_id`.
4. Démarrez l'add-on, vérifiez dans le journal le nouveau Base ID, puis **remettez
   `force_base_id` à vide** (voir *Option `force_base_id`*).

Débranchez ou éteignez ensuite la Link Box : deux émetteurs avec le même identifiant
se gêneraient.

## Trouver ses identifiants dans une sauvegarde Jeedom

Dans l'archive de sauvegarde Jeedom (`.tar.gz`), le fichier `DB_backup.sql` contient
la table `eqLogic`. Cherchez les lignes du plugin `openenocean` :

| Équipement | `device` | Ce qu'il faut relever |
|---|---|---|
| VMI PureVent | `d1079-01-00` | `logicalId` → option `vmi_id` ; `actionid` → identifiant d'émission |
| Assistant (sonde d'ambiance) | `d1079-00-00` | `logicalId` → option `assistant_id` |

Par exemple :

```sh
tar xzf sauvegarde-jeedom.tar.gz ./DB_backup.sql
grep -io "'[0-9A-F]\{8\}',NULL,'openenocean','{[^}]*d1079[^}]*" DB_backup.sql
```

Le `logicalId` est l'identifiant à 8 caractères juste avant `NULL,'openenocean'`,
l'`actionid` apparaît dans la configuration qui suit. Sans sauvegarde, la page de
l'équipement dans Jeedom affiche aussi le *logicalId*. Avec `log_level: debug`,
l'add-on journalise aussi toutes les trames reçues avec leur émetteur.

## Installation

1. Ajoutez ce dépôt dans *Paramètres → Applications (ou Modules complémentaires) →
   Boutique → ⋮ → Dépôts*.
2. Installez *Ventilairsec VMI EnOcean*.
3. Dans l'onglet *Configuration*, renseignez au moins `serial_port` et `vmi_id`.
4. Démarrez, puis vérifiez dans le journal la ligne
   `Clé EnOcean OK ... Base ID XXXXXXXX`.

Les entités apparaissent dans l'appareil *VMI Ventilairsec* de l'intégration MQTT
au fil des trames reçues (la VMI émet environ une fois par minute, l'Assistant toutes
les 10 minutes).

## Options

| Option | Rôle |
|---|---|
| `serial_port` | Port de la clé, de préférence `/dev/serial/by-id/...` (*Paramètres → Système → Matériel*). |
| `vmi_id` | ID EnOcean de la VMI. |
| `assistant_id` | ID de l'Assistant (vide pour l'ignorer). |
| `sender_id` | ID d'émission des commandes. Vide = Base ID de la clé (comportement de Jeedom). |
| `force_base_id` | Écrit un Base ID dans la clé au démarrage (voir plus bas). |
| `sync_clock` | Envoie l'heure à la VMI au démarrage puis toutes les heures. Elle n'a pas de pile et revient en 2018 à chaque coupure de courant. |
| `topic_prefix` | Préfixe des topics MQTT. |
| `log_level` | `debug` affiche toutes les trames radio brutes. |

## Si les mesures arrivent mais que les commandes sont sans effet

La réception fonctionne toujours : la clé capte toutes les trames de la VMI, quel
que soit leur destinataire. En revanche, la VMI n'obéit qu'à l'émetteur avec lequel
elle a été appairée, c'est-à-dire l'`actionid` relevé dans la sauvegarde Jeedom.
Comparez-le avec le Base ID affiché au démarrage de l'add-on :

- **Même clé que Jeedom** : l'`actionid` est le Base ID de la clé (ou le Base ID + 1,
  + 2…). Laissez `sender_id` vide, ou mettez-y l'`actionid` s'il ne se termine pas
  comme le Base ID.
- **Autre clé** : deux solutions.
  - Faire émettre la nouvelle clé comme l'ancienne avec `force_base_id`. Le Base ID
    est l'`actionid` arrondi au multiple de 0x80 inférieur (par exemple `FF8A1200`
    pour un `actionid` `FF8A1201`). Mettez ensuite l'`actionid` dans `sender_id`.
  - Réappairer la VMI avec le Base ID de la nouvelle clé : mettre la VMI en mode
    appairage (procédure Ventilairsec), puis envoyer une commande depuis Home
    Assistant, par exemple changer le mode.

## Option `force_base_id`

Écrit un Base ID dans la clé au démarrage s'il diffère du Base ID actuel. La puce
TCM310 n'autorise que **10 écritures** sur toute sa durée de vie : le journal affiche
le nombre de changements restants. Remettez l'option à vide une fois le changement fait.

## Entités créées

- **Contrôles** : Mode (Standby / Eco / Standard / Confort), Bypass (Toiture / Autre),
  Programmation horaire, Débit fixe, Surventilation, Boost (minutes, 0 = arrêt),
  Vacances (jours, 0 = arrêt), consignes Préchauffage résistance, Température max
  soufflage, Préchauffage Hydro'R et SolarR, boutons *Réinitialiser filtre* et
  *Synchroniser l'horloge*.
- **Mesures** : débit d'air, vitesse moteur, puissance de préchauffage, température
  extérieure, températures et humidités des sondes internes, encrassement du filtre,
  filtre restant (100 − encrassement, comme l'application VMI+), autonomie du filtre
  en jours (365 − 7 × semaines depuis la maintenance − 3, à ± 3 jours de
  l'application), ouverture du bypass et de la vanne Hydro'R, défauts (codes et
  libellés), état système, saison, signal radio, etc.
- **Assistant** : température, humidité, batterie, signal radio.
- **Surveillance de la clé** : *Problème clé EnOcean* et *Incident clé EnOcean*
  (clé qui ne répond plus, émission non confirmée, trames incohérentes).

La consigne de pression différentielle est en pascals (la VMI la transmet en dixièmes
de pascal).

## Topics MQTT

- États : `ventilairsec/vmi/<clé>` et `ventilairsec/assistant/<clé>` (retenus).
- Commandes : `ventilairsec/vmi/set/<clé>` (ex. `ventilairsec/vmi/set/mode` → `Confort`).
- Disponibilité : `ventilairsec/status` (`online` / `offline`).

## Signal radio et silences

Les capteurs *Signal radio* de la VMI et de l'Assistant passent à « indisponible »
après 30 minutes sans trame (`expire_after`) : un silence radio apparaît comme un
trou dans les graphiques. Pour placer la clé, visez mieux que −85 dBm. Éloignez-la
des boîtiers métalliques et de la box, une rallonge USB aide souvent.

Une VMI restée en mode appairage diffuse une demande d'appairage (trame UTE) par
minute. L'add-on n'y répond pas, mais s'en sert pour mesurer le signal (ligne
« Signal VMI (trame d'appairage) » dans le journal).

## Remplacement de l'agenda Jeedom

L'agenda du plugin Jeedom n'est pas repris : utilisez des automatisations Home
Assistant sur l'entité *Mode*, ou activez *Programmation horaire* pour que la VMI
applique son propre planning interne.

## Carte de tableau de bord

Le dossier `exemples` du dépôt contient une carte prête à coller (mode, boutons
Eco / Standard / Confort, défauts, actions rapides, graphiques). Les graphiques
demandent les cartes `mini-graph-card` et `apexcharts-card` (HACS). Les noms
d'entités peuvent différer chez vous selon la langue et les renommages.

## Crédits et licence

Le décodage du profil radio Ventilairsec (MSC D1-079) reprend celui des plugins
Jeedom `openenocean` et `ventilairsec` de Jeedom SAS, publiés sous GPL v3 ou
ultérieure. Cet add-on est distribué sous la même licence (fichier `LICENSE`).
Ventilairsec et PureVent sont des marques de leurs propriétaires.
