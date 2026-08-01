import imaplib
import email
import json
import smtplib
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path
from email.message import EmailMessage
from email.utils import make_msgid, formatdate, getaddresses
from email.header import decode_header, make_header
from email.errors import HeaderParseError
import ssl
from datetime import datetime, timedelta
import re
import html




# === CONFIGURATION (identifiants saisis via une boîte de dialogue, puis
#     mis en cache dans un fichier JSON local — jamais dans le code source) ===
# Emplacement par défaut du fichier de config, et petit fichier-pointeur (à
# côté du script) qui mémorise l'emplacement choisi par l'utilisateur via les
# boutons "Charger..." / "Enregistrer sous..." de la boîte de dialogue.
CONFIG_FILE_DEFAUT = Path(__file__).resolve().parent / "mail_config.json"
POINTEUR_FILE = Path(__file__).resolve().parent / "config_path.txt"

# (clé JSON, libellé affiché, valeur par défaut proposée, champ secret ?)
CHAMPS_CONFIG = [
    ("imap_host", "Serveur IMAP", "imap.univ-rouen.fr", False),
    ("imap_user", "Identifiant IMAP", "gheerton@univ-rouen.fr", False),
    ("imap_pass", "Mot de passe IMAP", "", True),
    ("gmail_user", "Adresse Gmail (expéditeur)", "blaisepascaldeportroyal@gmail.com", False),
    ("gmail_pass", "Mot de passe application Gmail", "", True),
    ("jours_limite", "Rechercher les mails non lus des N derniers jours", "7", False),
]


def chemin_config_actif():
    """Retourne le fichier de config à utiliser : celui mémorisé par
    POINTEUR_FILE s'il existe, sinon l'emplacement par défaut."""
    if POINTEUR_FILE.exists():
        try:
            chemin = POINTEUR_FILE.read_text(encoding="utf-8").strip()
            if chemin:
                return Path(chemin)
        except OSError:
            pass
    return CONFIG_FILE_DEFAUT


def memoriser_chemin_config(chemin: Path):
    """Enregistre l'emplacement choisi pour que les prochains lancements
    utilisent directement le même fichier, sans repasser par la boîte."""
    try:
        POINTEUR_FILE.write_text(str(chemin), encoding="utf-8")
    except OSError:
        pass


config_file = chemin_config_actif()


def charger_config():
    """Lit le fichier JSON courant s'il existe ; retourne {} sinon ou si corrompu."""
    if config_file.exists():
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def sauvegarder_config(config):
    with open(config_file, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def demander_identifiants(config_existant):
    """Affiche une fenêtre tkinter pour saisir/compléter les identifiants manquants.

    Pré-remplit les champs déjà connus (config_existant) ; les mots de passe
    sont masqués à l'écran. Deux boutons permettent de choisir librement
    l'emplacement du fichier de config : "Charger..." (importer un fichier
    existant, où qu'il soit) et "Enregistrer sous..." (choisir où sauvegarder).
    Ferme et retourne {} si l'utilisateur annule.
    """
    resultat = {}

    root = tk.Tk()
    root.title("Identifiants — transfert de mails")
    root.resizable(False, False)

    entrees = {}
    for i, (cle, libelle, defaut, est_secret) in enumerate(CHAMPS_CONFIG):
        tk.Label(root, text=libelle).grid(row=i, column=0, sticky="w", padx=8, pady=6)
        var = tk.StringVar(value=config_existant.get(cle, defaut))
        entree = tk.Entry(root, textvariable=var, width=42, show="•" if est_secret else "")
        entree.grid(row=i, column=1, padx=8, pady=6)
        entrees[cle] = var

    ligne_fichier = len(CHAMPS_CONFIG)
    chemin_var = tk.StringVar(value=f"Fichier : {config_file}")
    tk.Label(root, textvariable=chemin_var, fg="gray40", anchor="w").grid(
        row=ligne_fichier, column=0, columnspan=2, sticky="w", padx=8, pady=(4, 0)
    )

    def valeurs_valides():
        """Relit les champs, vérifie qu'ils sont complets et cohérents.
        Retourne le dict de valeurs si tout est valide, None sinon (avec un
        message d'erreur déjà affiché)."""
        valeurs = {cle: var.get().strip() for cle, var in entrees.items()}
        manquants = [libelle for cle, libelle, _, _ in CHAMPS_CONFIG if not valeurs.get(cle)]
        if manquants:
            messagebox.showerror("Champs manquants", "Veuillez renseigner : " + ", ".join(manquants))
            return None
        if not valeurs["jours_limite"].isdigit() or int(valeurs["jours_limite"]) < 1:
            messagebox.showerror(
                "Valeur invalide",
                "« Rechercher les mails non lus des N derniers jours » doit être un nombre entier positif."
            )
            return None
        return valeurs

    def charger_depuis_fichier():
        global config_file
        chemin = filedialog.askopenfilename(
            title="Charger un fichier de configuration",
            filetypes=[("Fichiers JSON", "*.json"), ("Tous les fichiers", "*.*")],
        )
        if not chemin:
            return
        try:
            with open(chemin, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            messagebox.showerror("Erreur de lecture", f"Impossible de lire ce fichier :\n{e}")
            return
        for cle, var in entrees.items():
            if cle in data:
                var.set(str(data[cle]))
        config_file = Path(chemin)
        memoriser_chemin_config(config_file)
        chemin_var.set(f"Fichier : {config_file}")

    def enregistrer_sous():
        global config_file
        valeurs = valeurs_valides()
        if valeurs is None:
            return
        chemin = filedialog.asksaveasfilename(
            title="Enregistrer la configuration sous...",
            defaultextension=".json",
            filetypes=[("Fichiers JSON", "*.json"), ("Tous les fichiers", "*.*")],
            initialfile="mail_config.json",
        )
        if not chemin:
            return
        config_file = Path(chemin)
        memoriser_chemin_config(config_file)
        resultat.update(valeurs)
        root.destroy()

    def valider():
        valeurs = valeurs_valides()
        if valeurs is None:
            return
        resultat.update(valeurs)
        root.destroy()

    def annuler():
        resultat.clear()
        root.destroy()

    boutons = tk.Frame(root)
    boutons.grid(row=ligne_fichier + 1, column=0, columnspan=2, pady=10)
    tk.Button(boutons, text="Charger...", command=charger_depuis_fichier).pack(side="left", padx=6)
    tk.Button(boutons, text="Enregistrer", command=valider).pack(side="left", padx=6)
    tk.Button(boutons, text="Enregistrer sous...", command=enregistrer_sous).pack(side="left", padx=6)
    tk.Button(boutons, text="Annuler", command=annuler).pack(side="left", padx=6)

    root.protocol("WM_DELETE_WINDOW", annuler)
    root.mainloop()

    return resultat


def obtenir_config():
    """Charge la config JSON courante ; si des identifiants manquent, ouvre la
    boîte de dialogue tkinter, sauvegarde le résultat, et le retourne."""
    config = charger_config()
    requis = [cle for cle, _, _, _ in CHAMPS_CONFIG]
    if all(config.get(cle) for cle in requis):
        return config

    config = demander_identifiants(config)
    if not config:
        sys.exit("Saisie des identifiants annulée — arrêt du script.")

    sauvegarder_config(config)
    return config


_config = obtenir_config()

# === PARAMÈTRES PERSONNALISABLES ===
IMAP_HOST = _config['imap_host']
IMAP_USER = _config['imap_user']
IMAP_PASS = _config['imap_pass']


SMTP_HOST = 'smtp.gmail.com'
SMTP_PORT = 587
GMAIL_USER = _config['gmail_user']
GMAIL_PASS = _config['gmail_pass']

try:
    JOURS_LIMITE = int(_config['jours_limite'])
    if JOURS_LIMITE < 1:
        raise ValueError
except (KeyError, ValueError):
    sys.exit(
        "Valeur invalide pour 'jours_limite' dans mail_config.json (entier positif attendu). "
        "Supprimez le fichier ou corrigez cette valeur, puis relancez le script."
    )




# === UTILITAIRES ===
def nettoyer_header(texte):
    if texte:
        return re.sub(r'[\r\n]+', ' ', texte).strip()
    return ''


def decode_mime_words(value: str) -> str:
    """Décode les en-têtes MIME (=?utf-8?Q?...?=)."""
    if not value:
        return ''
    try:
        return str(make_header(decode_header(value))).strip()
    except (LookupError, UnicodeError, ValueError, IndexError, HeaderParseError):
        return value.strip()


def parse_address_list(header_value: str):
    """Retourne une liste de 'Nom <email>' proprement décodés à partir d'un en-tête."""
    lst = []
    for (name, addr) in getaddresses([header_value or ""]):
        name_dec = decode_mime_words(name)
        addr = (addr or "").strip()
        if name_dec and addr:
            lst.append(f"{name_dec} <{addr}>")
        elif addr:
            lst.append(addr)
        elif name_dec:
            lst.append(name_dec)
    return lst


def join_for_header(lst):
    return ", ".join(lst) if lst else ""


def construire_corps(body_text, body_html, recipients_text, recipients_html):
    """Construit la partie texte/HTML à insérer dans le message transféré.

    Toujours utilisée (même corps vide) pour éviter toute logique dupliquée
    entre le chemin normal et un éventuel chemin de secours.
    """
    contenu = EmailMessage()
    if body_text and body_html:
        contenu.set_content(recipients_text + "\n" + body_text)
        contenu.add_alternative(recipients_html + body_html, subtype='html')
    elif body_html:
        contenu.set_content(recipients_text + "\n(Ce message contient une version HTML.)")
        contenu.add_alternative(recipients_html + body_html, subtype='html')
    elif body_text:
        contenu.set_content(recipients_text + "\n" + body_text)
    else:
        contenu.set_content(
            recipients_text + "\n(Aucun corps texte/HTML lisible n'a été trouvé "
            "dans ce message ; voir la pièce jointe pour l'original complet.)"
        )
    return contenu




# === BOUCLE PRINCIPALE ===
while True:
    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST)
        imap.login(IMAP_USER, IMAP_PASS)
        imap.select('INBOX')


        date_limite = (datetime.now() - timedelta(days=JOURS_LIMITE)).strftime("%d-%b-%Y")
        search_criteria = f'(UNSEEN SINCE {date_limite})'
        status, messages = imap.search(None, search_criteria)
        transferred = []


        for num in messages[0].split():
            print(f"\n📩 Traitement du mail UID {num.decode()}")

            try:
                # BODY.PEEK[] : récupère le message SANS positionner \Seen sur le
                # serveur. Le flag \Seen n'est posé, explicitement, qu'après
                # confirmation de l'envoi réussi vers Gmail — jamais avant.
                status, data = imap.fetch(num, '(BODY.PEEK[])')
                if status != 'OK' or not data or data[0] is None:
                    raise RuntimeError(f"Échec de la récupération IMAP (status={status})")
                raw_email = data[0][1]
                original = email.message_from_bytes(raw_email)

                # === Décodage propre des en-têtes principaux ===
                subject_raw = original.get('Subject')
                subject = decode_mime_words(subject_raw) if subject_raw else '(Sans objet)'
                from_raw = original.get('From')
                from_dec = decode_mime_words(from_raw)
                to_list = parse_address_list(original.get('To'))
                cc_list = parse_address_list(original.get('Cc'))
                date_header = decode_mime_words(original.get('Date', formatdate()))
                reply_to = decode_mime_words(original.get('Reply-To') or from_raw)

                print(f"Sujet : {subject}")
                print("En-têtes originaux :", original.items())

                msg = EmailMessage()
                msg['Subject'] = nettoyer_header(subject)
                msg['From'] = nettoyer_header(from_dec)
                msg['To'] = GMAIL_USER
                msg['Reply-To'] = nettoyer_header(reply_to)
                msg['Date'] = nettoyer_header(date_header)
                msg['Message-ID'] = make_msgid()

                # En-têtes informatifs pour la recherche/traçabilité dans Gmail
                msg['X-Original-From'] = nettoyer_header(from_dec)
                msg['X-Original-To'] = nettoyer_header(join_for_header(to_list))
                msg['X-Original-Cc'] = nettoyer_header(join_for_header(cc_list))
                msg['X-Original-Subject'] = nettoyer_header(subject)

                body_text = None
                body_html = None

                # Extraction du corps et des pièces jointes.
                # Les pièces jointes sont attachées directement à `msg` ici : ce
                # sont les mêmes objets qui seront envoyés plus bas, il n'y a
                # donc aucun risque de les perdre en reconstruisant le message.
                if original.is_multipart():
                    for part in original.walk():
                        content_type = part.get_content_type()
                        content_disposition = str(part.get("Content-Disposition") or "")
                        payload = part.get_payload(decode=True)
                        if not payload:
                            continue

                        if "attachment" in content_disposition:
                            filename = part.get_filename()
                            if filename:
                                filename_clean = nettoyer_header(decode_mime_words(filename))
                                msg.add_attachment(
                                    payload,
                                    maintype=part.get_content_maintype(),
                                    subtype=part.get_content_subtype(),
                                    filename=filename_clean
                                )
                        elif content_type == "text/html" and body_html is None:
                            charset = part.get_content_charset() or 'utf-8'
                            try:
                                body_html = payload.decode(charset, errors='ignore')
                            except LookupError as e:
                                print("Erreur HTML multipart :", e)
                        elif content_type == "text/plain" and body_text is None:
                            charset = part.get_content_charset() or 'utf-8'
                            try:
                                body_text = payload.decode(charset, errors='ignore')
                            except LookupError as e:
                                print("Erreur texte multipart :", e)
                else:
                    payload = original.get_payload(decode=True)
                    charset = original.get_content_charset() or 'utf-8'
                    try:
                        decoded = payload.decode(charset, errors='ignore') if payload else ''
                        content_type = original.get_content_type()
                        if content_type == "text/html":
                            body_html = decoded
                        else:
                            body_text = decoded
                        print("✔ Corps simple détecté :")
                        print("Type :", content_type)
                        print("Charset :", charset)
                        print("Contenu (début) :", decoded[:200], "...")
                    except LookupError as e:
                        print("Erreur non multipart :", e)

                corps_absent = not body_text and not body_html
                if corps_absent:
                    # Aucun corps texte/HTML lisible : on joint quand même le
                    # message original brut pour ne rien perdre (au lieu de
                    # sauter l'envoi comme le faisait l'ancienne version).
                    print("⚠ Aucun corps détecté — le message original brut est joint en secours.")
                    msg.add_attachment(raw_email, maintype='message', subtype='rfc822', filename='message_original.eml')

                # === Cartouche récapitulatif (destinataires d'origine) ===
                recipients_text = (
                    f"De : {from_dec}\n"
                    f"À  : {join_for_header(to_list) or '—'}\n"
                    f"Cc : {join_for_header(cc_list) or '—'}\n"
                    f"Date : {date_header}\n"
                )
                recipients_html = f"""
<div style="border:1px solid #ddd;padding:10px;margin-bottom:12px;font-size:0.95em;">
  <strong>Transfert automatique — destinataires d'origine</strong><br>
  <span><strong>De :</strong> {html.escape(from_dec or '')}</span><br>
  <span><strong>À&nbsp;:</strong> {html.escape(join_for_header(to_list) or '—')}</span><br>
  <span><strong>Cc :</strong> {html.escape(join_for_header(cc_list) or '—')}</span><br>
  <span><strong>Date :</strong> {html.escape(date_header or '')}</span><br>
  <em>Note :</em> les destinataires en copie cachée (Bcc) ne sont pas visibles par conception.
</div>
""".strip()

                # Construction du contenu à insérer (logique unique, non dupliquée)
                contenu = construire_corps(body_text, body_html, recipients_text, recipients_html)

                # S'assurer que le message principal est multipart pour recevoir le contenu
                if msg.get_content_maintype() != 'multipart':
                    msg.make_mixed()

                # Attacher le contenu (texte/HTML) dans le message final
                if contenu.is_multipart():
                    for part in contenu.iter_parts():
                        msg.attach(part)
                else:
                    # cas texte seul : on attache une part text/plain
                    txt_part = EmailMessage()
                    txt_part.set_content(contenu.get_content())
                    msg.attach(txt_part)

                print(f"✉ Envoi du mail : {msg['Subject']}")
                with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                    server.starttls(context=ssl.create_default_context())
                    server.login(GMAIL_USER, GMAIL_PASS)
                    server.send_message(msg)

                print("✅ Transfert réussi")

                # Le message n'est marqué \Seen sur le serveur IMAP qu'ici,
                # une fois l'envoi confirmé sans exception. En cas d'échec
                # avant ce point, le message reste UNSEEN et sera retenté au
                # prochain cycle.
                imap.store(num, '+FLAGS', '\\Seen')
                transferred.append(msg['Subject'])

            except Exception as e:
                # Une erreur sur CE message ne doit pas interrompre le
                # traitement des autres mails du lot, ni faire planter tout
                # le script : on journalise et on passe au suivant. Le
                # message n'a pas été marqué \Seen, il sera retenté plus tard.
                print(f"❌ Échec du traitement du mail UID {num.decode()} : {e}")
                continue


        imap.logout()
        print(f"\n📬 Total transféré : {len(transferred)} message(s)")
        print(f"⏳ Pause de 5 minutes... ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})\n")
        time.sleep(300)


    except Exception as e:
        print(f"💥 Erreur : {e}")
        print("Le script s'arrête. Corrigez le bug avant de relancer.")
        break
