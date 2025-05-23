# constants.py
"""
Constantes, chemins et données statiques pour l'application Sonneries Collège.
"""
import os
import logging
import sys # Pour stderr

# --- Configuration du Logger (simple pour ce fichier) ---
log_constants = logging.getLogger('constants_setup')
log_constants.setLevel(logging.INFO) # Ou DEBUG pour plus de détails
handler = logging.StreamHandler(sys.stderr) # Écrit sur stderr
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s [%(filename)s:%(lineno)d]')
handler.setFormatter(formatter)
if not any(isinstance(h, logging.StreamHandler) and h.stream == sys.stderr for h in log_constants.handlers):
    log_constants.addHandler(handler)
log_constants.propagate = False
# --- Fin config logger ---

log_constants.info("Chargement du module constants...")

# --- Définition des Chemins ---

# !!! IMPORTANT : ADAPTER CE CHEMIN RÉSEAU !!!
# Chemin UNC (\\serveur\partage\) ou chemin local (C:\...) vers le dossier racine
# contenant les sous-dossiers 'config' et 'mp3'.
NETWORK_BASE_PATH = r"\\192.168.10.15\AppSonnerieCollege" # <--- Chemin actuel (Vérifié OK)

# Définir des valeurs initiales
CONFIG_PATH = None
MP3_PATH = None
USING_NETWORK_PATH = False

try:
    log_constants.info(f"Chemin réseau/base configuré: {NETWORK_BASE_PATH}")
    log_constants.info(f"Vérification de l'accessibilité...")
    network_accessible = os.path.exists(NETWORK_BASE_PATH) and os.access(NETWORK_BASE_PATH, os.R_OK)

    if network_accessible:
        log_constants.info(f"Chemin trouvé et accessible. Utilisation de: {NETWORK_BASE_PATH}")
        CONFIG_PATH = os.path.join(NETWORK_BASE_PATH, 'config')
        MP3_PATH = os.path.join(NETWORK_BASE_PATH, 'mp3')
        USING_NETWORK_PATH = True
        # Vérifier existence sous-dossiers (avertissement seulement)
        if not os.path.isdir(CONFIG_PATH): log_constants.warning(f"Sous-dossier 'config' non trouvé dans {NETWORK_BASE_PATH}")
        if not os.path.isdir(MP3_PATH): log_constants.warning(f"Sous-dossier 'mp3' non trouvé dans {NETWORK_BASE_PATH}")
    else:
        log_constants.warning(f"Chemin '{NETWORK_BASE_PATH}' NON TROUVÉ ou INACCESSIBLE.")
        # Fallback local
        script_dir = os.path.dirname(os.path.abspath(__file__))
        LOCAL_FALLBACK_PATH = os.path.join(script_dir, 'data')
        log_constants.info(f"Tentative fallback local: '{LOCAL_FALLBACK_PATH}'")
        try:
            config_fallback = os.path.join(LOCAL_FALLBACK_PATH, 'config')
            mp3_fallback = os.path.join(LOCAL_FALLBACK_PATH, 'mp3')
            os.makedirs(config_fallback, exist_ok=True); os.makedirs(mp3_fallback, exist_ok=True)
            if os.path.isdir(config_fallback) and os.access(config_fallback, os.W_OK) and \
               os.path.isdir(mp3_fallback) and os.access(mp3_fallback, os.W_OK):
                log_constants.info("Utilisation des chemins locaux de secours.")
                CONFIG_PATH = config_fallback; MP3_PATH = mp3_fallback; USING_NETWORK_PATH = False
            else: log_constants.error("Impossible de créer/accéder aux dossiers locaux.")
        except Exception as e_fallback: log_constants.error(f"Erreur fallback local: {e_fallback}", exc_info=True)

except Exception as e_global:
    log_constants.error(f"Erreur globale détermination chemins: {e_global}", exc_info=True)

# --- Noms des Fichiers de Configuration (dans CONFIG_PATH) ---
DONNEES_SONNERIES_FILE = "donnees_sonneries.json"
PARAMS_FILE = "parametres_college.json"
USERS_FILE = "users.json"

# Nom pour le fichier ICS temporaire (dans le dossier du script ou cache_dir de HolidayManager)
# Laisser HolidayManager gérer son chemin temporaire est peut-être mieux.
# TEMP_ICS_FILE = "temp_vacances_downloaded.ics"

# --- Constantes existantes ---
DEPARTEMENTS_ZONES = {
    "01 - Ain": "A", "03 - Allier": "A", "07 - Ardèche": "A", "15 - Cantal": "A",
    "21 - Côte-d'Or": "A", "25 - Doubs": "A", "26 - Drôme": "A", "38 - Isère": "A",
    "39 - Jura": "A", "42 - Loire": "A", "43 - Haute-Loire": "A", "58 - Nièvre": "A",
    "63 - Puy-de-Dôme": "A", "69 - Rhône": "A", "70 - Haute-Saône": "A",
    "71 - Saône-et-Loire": "A", "73 - Savoie": "A", "74 - Haute-Savoie": "A",
    "89 - Yonne": "A", "90 - Territoire de Belfort": "A",
    "02 - Aisne": "B", "08 - Ardennes": "B", "10 - Aube": "B", "14 - Calvados": "B",
    "18 - Cher": "B", "22 - Côtes-d'Armor": "B", "27 - Eure": "B", "28 - Eure-et-Loir": "B",
    "29 - Finistère": "B", "35 - Ille-et-Vilaine": "B", "36 - Indre": "B",
    "37 - Indre-et-Loire": "B", "41 - Loir-et-Cher": "B", "44 - Loire-Atlantique": "B",
    "45 - Loiret": "B", "49 - Maine-et-Loire": "B", "50 - Manche": "B", "51 - Marne": "B",
    "53 - Mayenne": "B", "54 - Meurthe-et-Moselle": "B", "55 - Meuse": "B",
    "56 - Morbihan": "B", "57 - Moselle": "B", "59 - Nord": "B", "60 - Oise": "B",
    "61 - Orne": "B", "62 - Pas-de-Calais": "B", "72 - Sarthe": "B", "76 - Seine-Maritime": "B",
    "77 - Seine-et-Marne": "B", "80 - Somme": "B", "85 - Vendée": "B", "86 - Vienne": "B",
    "87 - Haute-Vienne": "B", "88 - Vosges": "B",
    "09 - Ariège": "C", "11 - Aude": "C", "12 - Aveyron": "C", "13 - Bouches-du-Rhône": "C",
    "16 - Charente": "C", "17 - Charente-Maritime": "C", "19 - Corrèze": "C",
    "23 - Creuse": "C", "24 - Dordogne": "C", "30 - Gard": "C", "31 - Haute-Garonne": "C",
    "32 - Gers": "C", "33 - Gironde": "C", "34 - Hérault": "C", "40 - Landes": "C",
    "46 - Lot": "C", "47 - Lot-et-Garonne": "C", "48 - Lozère": "C", "64 - Pyrénées-Atlantiques": "C",
    "65 - Hautes-Pyrénées": "C", "66 - Pyrénées-Orientales": "C", "75 - Paris": "C",
    "78 - Yvelines": "C", "79 - Deux-Sèvres": "C", "81 - Tarn": "C", "82 - Tarn-et-Garonne": "C",
    "83 - Var": "C", "84 - Vaucluse": "C", "91 - Essonne": "C", "92 - Hauts-de-Seine": "C",
    "93 - Seine-Saint-Denis": "C", "94 - Val-de-Marne": "C", "95 - Val-d'Oise": "C",
    "2A - Corse-du-Sud": "Corse", "2B - Haute-Corse": "Corse",
}

LISTE_DEPARTEMENTS = sorted(DEPARTEMENTS_ZONES.keys())

JOURS_SEMAINE_PLANNING = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi"]
JOURS_SEMAINE_ASSIGNATION = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
JOURS_SEMAINE_MAP_INDEX = {0: "Lundi", 1: "Mardi", 2: "Mercredi", 3: "Jeudi", 4: "Vendredi", 5: "Samedi", 6: "Dimanche"}

AUCUNE_SONNERIE = "Aucune"

# --- NOUVELLE CONSTANTE ---
# URL de base pour le téléchargement des calendriers ICS des vacances scolaires
# (Vérifiée le 2024-04-15, peut nécessiter une mise à jour future)
VACANCES_ICS_BASE_URL = "https://www.service-public.fr/simulateur/calcul/assets/dsfr-particuliers/fichiers_ics/"
# --- FIN NOUVELLE CONSTANTE ---


# --- Vérification Finale et Logs ---
log_constants.info("-" * 30)
log_constants.info(f"Chemin final CONFIG_PATH: {CONFIG_PATH}")
log_constants.info(f"Chemin final MP3_PATH: {MP3_PATH}")
log_constants.info(f"Utilisation chemin réseau: {USING_NETWORK_PATH}")
log_constants.info(f"URL Base ICS Vacances: {VACANCES_ICS_BASE_URL}") # Log de la nouvelle constante
log_constants.info("-" * 30)

if CONFIG_PATH is None or MP3_PATH is None:
    log_constants.critical("###########################################################")
    log_constants.critical("ERREUR FATALE: CONFIG_PATH ou MP3_PATH non déterminés.")
    log_constants.critical("###########################################################")

log_constants.info("Fin du chargement du module constants.")