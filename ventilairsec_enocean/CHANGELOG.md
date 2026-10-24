# Changelog

## 1.1.2

- Les espaces avant ou après une valeur (souvent ramenées par un copier-coller) sont
  acceptées et retirées : elles bloquaient l'enregistrement des identifiants et
  faussaient les ports.

## 1.1.1

- Lecture du Base ID réessayée une fois sans réponse : juste après l'ouverture du
  port par ser2net, la première commande peut se perdre. Le retour sur le port
  principal ne prend plus 5 minutes de trop après un redémarrage de la passerelle.

## 1.1.0

- `serial_port` accepte `socket://IP:port` : module EnOcean partagé sur le réseau par
  ser2net, par exemple celui d'une VMI Link Box placée près de la VMI (voir
  *La Link Box comme antenne réseau* dans la documentation).
- En mode réseau, reconnexion après 10 minutes sans aucune donnée (passerelle
  redémarrée sans fermer la connexion).
- Nouvelle option `fallback_port` : port de secours utilisé après trois connexions
  ratées sur le premier, avec retour automatique dès que celui-ci répond (vérifié
  toutes les 5 minutes). Le capteur *Problème clé EnOcean* reste levé tant que
  l'add-on tourne sur le port de secours.
- Sans `sender_id`, chaque clé émet avec son propre Base ID.

## 1.0.0

Première version publique. Développée en octobre 2026 pour remplacer une VMI Link
Box (Jeedom) sur une VMI PureVent, et testée sur cette seule installation.
