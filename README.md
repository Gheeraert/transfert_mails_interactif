# transfert_mails_interactif

Un script Python qui transfère automatiquement les e-mails récents d'une boîte IMAP (typiquement une messagerie institutionnelle) vers une autre adresse via SMTP — pensé pour contourner les limitations de redirection imposées par certaines messageries.

---

## ✨ Fonctionnalités

- Connexion sécurisée à une boîte IMAP (université, institution…) et transfert vers n'importe quelle adresse SMTP (Gmail, Outlook, Zimbra…).
- Recherche des messages **non lus depuis N jours** (`UNSEEN SINCE`) — 7 jours par défaut, réglable dans la boîte de dialogue ou le fichier de config.
- Transfert fidèle du contenu : texte, HTML, pièces jointes, et un cartouche récapitulatif indiquant l'expéditeur/destinataires/date d'origine.
- **Un message n'est jamais marqué comme lu sur le serveur IMAP avant que son transfert vers l'adresse de destination soit confirmé.** Le script utilise `BODY.PEEK[]` (qui ne touche pas au flag `\Seen`) pour récupérer le message, et ne pose `\Seen` qu'après un envoi SMTP réussi. En cas d'erreur, le message reste non lu et sera retenté au cycle suivant.
- Une erreur sur un message isolé (pièce jointe corrompue, encodage inattendu…) n'interrompt pas le traitement des autres messages du lot ni le script entier.
- Les messages sans corps texte/HTML exploitable ne sont plus abandonnés : le message original est joint en `.eml` et transféré tel quel.
- Boîte de dialogue graphique (Tkinter) pour saisir les identifiants au premier lancement ; ils sont ensuite mis en cache localement (voir [Identifiants](#-identifiants) ci-dessous).
- Boucle continue avec une pause de 5 minutes entre chaque synchronisation.
- Aucune dépendance externe : uniquement la bibliothèque standard de Python (`imaplib`, `smtplib`, `email`, `tkinter`, `json`…).

---

## 🛠️ Installation

1. Cloner le dépôt :
   ```bash
   git clone https://github.com/Gheeraert/transfert_mails_interactif.git
   cd transfert_mails_interactif
   ```
2. Avoir Python 3.10+ installé (avec Tkinter — inclus par défaut sur la plupart des distributions Python officielles ; sur certaines distributions Linux minimalistes, installez le paquet `python3-tk`).
3. Lancer le script :
   ```bash
   python transfert_mails_interactif.py
   ```

Aucune installation de paquet tiers n'est nécessaire (`pip install` non requis).

---

## 🔑 Identifiants

Au premier lancement (ou si des identifiants sont manquants), une fenêtre s'ouvre pour saisir :

- le serveur et l'identifiant IMAP (boîte source),
- le mot de passe IMAP,
- l'adresse Gmail (ou autre) de destination,
- le mot de passe (ou mot de passe d'application) SMTP,
- la limite de recherche en jours (`jours_limite`, entier positif, 7 par défaut) : seuls les mails non lus reçus depuis ce nombre de jours sont transférés.

Ces informations sont ensuite enregistrées dans `mail_config.json`, créé par défaut à côté du script, et rechargées automatiquement aux lancements suivants sans repasser par la fenêtre.

La boîte de dialogue propose aussi deux boutons pour choisir librement l'emplacement du fichier de config :

- **Charger...** : importer un fichier `mail_config.json` existant, où qu'il se trouve (ex. sur une clé USB, un dossier synchronisé), et pré-remplir les champs avec son contenu.
- **Enregistrer sous...** : choisir un autre emplacement que celui par défaut pour sauvegarder la configuration.

Dans les deux cas, l'emplacement choisi est mémorisé dans `config_path.txt` (à côté du script) : les lancements suivants utiliseront directement ce fichier, sans repasser par la boîte de dialogue.

> ⚠️ **Le fichier de config contient vos mots de passe en clair sur le disque, quel que soit son emplacement.** `mail_config.json` et `config_path.txt` sont exclus du dépôt via `.gitignore` — ne les committez jamais. Protégez l'accès à la machine (et, le cas échéant, au support externe) sur laquelle il se trouve. Pour Gmail, utilisez impérativement un [mot de passe d'application](https://support.google.com/accounts/answer/185833) plutôt que le mot de passe principal du compte.

Pour resaisir des identifiants (changement de mot de passe, erreur de saisie…), supprimez le fichier de config actif (et `config_path.txt` s'il pointe ailleurs que l'emplacement par défaut) puis relancez le script.

---

## ▶️ Fonctionnement

Le script tourne en boucle infinie : à chaque cycle, il récupère les messages non lus des `jours_limite` derniers jours (7 par défaut), les transfère un par un, puis attend 5 minutes avant de recommencer. Pour l'arrêter, fermez le terminal ou interrompez le processus (Ctrl+C).

Pour une exécution ponctuelle plutôt qu'une boucle continue, retirez le `while True:` et son `time.sleep(300)` final, ou adaptez le script à votre planificateur de tâches (cron, Planificateur de tâches Windows…).

---

## 📄 Licence

Distribué sous licence MIT — voir [LICENSE](LICENSE).
