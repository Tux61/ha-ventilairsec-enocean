# Ventilairsec VMI EnOcean

Pont entre une VMI Ventilairsec PureVent et Home Assistant, en remplacement des
plugins Jeedom `openenocean` + `ventilairsec` (par exemple ceux de la VMI Link Box).
L'add-on parle directement à la clé EnOcean (TCM310, protocole ESP3) et publie la
VMI dans Home Assistant via MQTT, avec la découverte automatique.

Projet indépendant, non affilié à Ventilairsec ni à Jeedom SAS. Testé sur une seule
installation : utilisez-le en connaissance de cause.

## Prérequis

- Un module EnOcean à puce TCM310 :
  - soit une clé USB (USB 300 ou équivalent). Si votre ancien Jeedom utilisait déjà une
    clé USB, reprenez-la : la VMI lui obéit en général sans autre réglage ;
  - soit un module partagé sur le réseau avec ser2net, par exemple celui d'une VMI Link
    Box (voir *La Link Box comme antenne réseau*).
- Un broker MQTT : l'add-on *Mosquitto broker* et l'intégration *MQTT* de Home Assistant.
- **Ne pas** configurer l'intégration EnOcean native de Home Assistant sur cette clé :
  un seul programme peut ouvrir le port série. Si Home Assistant la propose en
  découverte, choisissez *Ignorer*. Si les deux se disputent la clé, l'add-on reçoit
  des trames corrompues et lève le capteur *Problème clé EnOcean*.

## Venir d'une VMI Link Box

Dans la VMI Link Box, le module radio (EnOcean TCM310) est soudé sur une carte
branchée sur le connecteur GPIO du boîtier (Rock Pi 4C). Home Assistant ne peut pas
l'utiliser directement. Deux solutions :

- **Garder la Link Box comme antenne réseau** : elle partage son module sur le réseau
  et l'add-on s'y connecte (voir *La Link Box comme antenne réseau*). La VMI est déjà
  appairée avec ce module : rien à réappairer, et la Link Box peut être placée près de
  la VMI pour un meilleur signal.
- **Utiliser une clé EnOcean USB** (USB 300 ou équivalent, à puce TCM310) branchée
  sur Home Assistant, et débrancher la Link Box. Suivez alors les étapes ci-dessous.

La VMI reste appairée à l'identifiant d'émission de la Link Box. Pour que la clé USB
soit obéie sans réappairer la VMI, donnez-lui le Base ID de la Link Box :

1. Relevez l'`actionid` de la VMI dans la sauvegarde Jeedom (voir ci-dessous).
2. Mettez dans `force_base_id` cet `actionid` arrondi au multiple de 0x80 inférieur
   (l'`actionid` lui-même s'il se termine par `00` ou `80`).
3. Si l'`actionid` ne se termine pas par `00` ou `80`, mettez-le dans `sender_id`.
4. Démarrez l'add-on, vérifiez dans le journal le nouveau Base ID, puis **remettez
   `force_base_id` à vide** (voir *Option `force_base_id`*).

Débranchez ou éteignez ensuite la Link Box : deux émetteurs avec le même identifiant
se gêneraient. Vous pouvez aussi combiner les deux solutions : la Link Box comme port
principal et la clé USB en secours (option `fallback_port`). Les deux ont alors le
même Base ID, mais seul l'add-on les fait émettre, et toujours une seule à la fois.

## La Link Box comme antenne réseau

Testé sur une Link Box sous Armbian 21.05 (Debian 10), module sur `/dev/ttyS2`. Les
commandes se tapent en SSH sur la Link Box, en root.

1. **Relever le port du module** dans Jeedom : *Plugins → EnOcean → Configuration*
   (par exemple `Jeedom Atlas (/dev/ttyS2)`), puis **désactiver le plugin EnOcean**
   (*Plugins → Gestion des plugins*) pour qu'il libère le port.
2. **Installer ser2net.** Les dépôts de Debian 10 sont archivés et `apt` ne fonctionne
   plus en l'état ; récupérez le paquet directement :

   ```sh
   wget http://archive.debian.org/debian/pool/main/s/ser2net/ser2net_3.5-2_arm64.deb
   dpkg -i ser2net_3.5-2_arm64.deb
   systemctl stop ser2net
   ```

3. **Le configurer** : remplacez le contenu de `/etc/ser2net.conf` par cette seule
   ligne (la configuration d'origine ouvre d'autres ports série sur le réseau) :

   ```
   3333:raw:0:/dev/ttyS2:57600 8DATABITS NONE 1STOPBIT
   ```

   Puis limitez l'accès à Home Assistant (remplacez l'adresse par la sienne) :

   ```sh
   echo "ser2net: 192.168.1.10" >> /etc/hosts.allow
   echo "ser2net: ALL" >> /etc/hosts.deny
   systemctl start ser2net
   ```

4. **Dans l'add-on**, mettez `serial_port` à `socket://IP-de-la-Link-Box:3333`, et
   laissez `sender_id` et `force_base_id` vides : le Base ID est déjà celui avec lequel
   la VMI a été appairée. Le journal doit afficher
   `Clé EnOcean OK sur socket://... - Base ID ...`.
5. **Donner une adresse fixe** à la Link Box (bail statique sur la box internet), car
   l'add-on s'y connecte par son adresse.

Pour la placer près de la VMI, connectez-la au Wi-Fi (le mot de passe est demandé) :

```sh
nmcli --ask dev wifi connect "Nom-du-réseau" ifname wlan0
```

Précautions :

- **Arrêtez-la toujours proprement** (`poweroff`) avant de la débrancher. L'image
  d'origine retarde les écritures sur le disque de 10 minutes (`commit=600` dans
  `/etc/fstab`) : une coupure peut effacer une configuration récente, par exemple le
  profil Wi-Fi. Retirer `commit=600` de `/etc/fstab` supprime ce risque.
- Le système de la Link Box (Debian 10) ne reçoit plus de mises à jour de sécurité :
  gardez-la sur le réseau local. Une fois le module partagé, vous pouvez désactiver
  Jeedom (`apache2`, `mariadb`, `mosquitto`) et son accès à distance (`openvpn`,
  tâches `/etc/cron.d/jeedom*`).
- Si la Link Box est injoignable, Home Assistant ne voit plus la VMI (elle continue de
  fonctionner seule, dans son dernier mode). Une clé USB en `fallback_port` prend le
  relais.

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
| `serial_port` | Port de la clé, de préférence `/dev/serial/by-id/...` (*Paramètres → Système → Matériel*), ou `socket://IP:port` pour un module partagé sur le réseau. |
| `fallback_port` | Port de secours, facultatif (voir *Port de secours*). |
| `vmi_id` | ID EnOcean de la VMI. |
| `assistant_id` | ID de l'Assistant (vide pour l'ignorer). |
| `sender_id` | ID d'émission des commandes. Vide = Base ID de la clé (comportement de Jeedom). |
| `force_base_id` | Écrit un Base ID dans la clé au démarrage (voir plus bas). |
| `sync_clock` | Envoie l'heure à la VMI au démarrage puis toutes les heures. Elle n'a pas de pile et revient en 2018 à chaque coupure de courant. |
| `topic_prefix` | Préfixe des topics MQTT. |
| `log_level` | `debug` affiche toutes les trames radio brutes. |

## Port de secours

Avec `fallback_port` renseigné (une clé USB, ou un autre `socket://`), l'add-on bascule
sur ce port après trois connexions ratées sur `serial_port` (environ 30 secondes). Le
capteur *Problème clé EnOcean* reste alors levé, avec la raison dans *Incident clé
EnOcean*. Toutes les 5 minutes, l'add-on vérifie si le port principal répond de
nouveau et y revient tout seul ; l'alerte retombe 30 minutes après.

Pour que la VMI obéisse aux deux, donnez-leur le même Base ID (voir `force_base_id`)
et laissez `sender_id` vide. En mode réseau, l'add-on se reconnecte aussi après
10 minutes sans aucune donnée.

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
