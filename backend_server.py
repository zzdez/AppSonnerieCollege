# backend_server.py

import os
import json
import threading
import time
import subprocess
from datetime import datetime, date, timedelta
import logging
from logging.handlers import RotatingFileHandler
import sys
import glob

# --- Import des dépendances Web ---
import requests
from flask import (Flask, request, jsonify, render_template, Response,
                   redirect, url_for, flash, send_from_directory) # Fonctions Flask nécessaires
from flask_login import (LoginManager, UserMixin, login_user, logout_user,
                           login_required, current_user) # Flask-Login
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from functools import wraps

# --- Import des modules locaux ---
# Utilisation d'un bloc try/except pour une erreur de démarrage plus claire
try:
    from scheduler import SchedulerManager
    from constants import (CONFIG_PATH, MP3_PATH, USERS_FILE, PARAMS_FILE,
                           DONNEES_SONNERIES_FILE,
                           DEPARTEMENTS_ZONES, LISTE_DEPARTEMENTS, JOURS_SEMAINE_ASSIGNATION, AUCUNE_SONNERIE)  # <--- AJOUTE CES DEUX LIGNES ICI
    from holiday_manager import HolidayManager
    MODULES_LOADED = True
except ImportError as e_imp:
    # Loggue sur stderr si le logger principal n'est pas encore dispo
    print(f"ERREUR CRITIQUE : Impossible d'importer un module essentiel : {e_imp}", file=sys.stderr)
    print("Vérifiez que tous les fichiers .py (scheduler, constants, holiday_manager) sont présents.", file=sys.stderr)
    MODULES_LOADED = False
    # On ne peut pas continuer sans ces modules
    sys.exit("Arrêt dû à une erreur d'importation de module.")
except Exception as e_other:
    print(f"ERREUR CRITIQUE Inattendue pendant les imports initiaux : {e_other}", file=sys.stderr)
    MODULES_LOADED = False
    sys.exit("Arrêt dû à une erreur inattendue pendant l'importation.")

# --- Import Sounddevice (optionnel) ---
try:
    import sounddevice as sd
    SOUNDDEVICE_AVAILABLE = True
except Exception as e_sd:
    # logger n'est pas encore dispo si on est avant sa config.
    # On logue sur stderr pour le moment, puis on reloguera avec le logger principal si besoin.
    print(f"AVERTISSEMENT: Bibliothèque sounddevice non trouvée ou erreur à l'import: {e_sd}. La sélection de périphérique audio sera limitée.", file=sys.stderr)
    SOUNDDEVICE_AVAILABLE = False
    sd = None # Pour éviter des NameError plus loin si on essaie d'y accéder


# ==============================================================================
# Configuration de l'Application Flask et des Extensions
# ==============================================================================

app = Flask(__name__)

# --- Clé Secrète (TRÈS IMPORTANT) ---
# À CHANGER ABSOLUMENT pour une valeur complexe et unique en production !
# Générer avec : python -c "import os; print(os.urandom(24))"
app.secret_key = b'_5#y2L"F4Q8z\n\xec]/' # !!! CHANGER CECI !!!
# ------------------------------------

# --- Configuration Flask-Login ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login' # Redirige vers la route @app.route('/login') si non connecté
login_manager.login_message = "Veuillez vous connecter pour accéder à cette page."
login_manager.login_message_category = "info" # Classe CSS pour les messages flash (Bootstrap)
# ---------------------------------

# --- Décorateurs de Rôle ---

ROLES_HIERARCHIE = {
    "administrateur": 3,
    "collaborateur": 2,
    "lecteur": 1
}

def role_access_denied(required_role_name_for_message):
    # Action commune en cas de refus d'accès.
    user_display_name = current_user.id if current_user.is_authenticated else 'Anonyme'
    user_actual_role = getattr(current_user, 'role', 'N/A') if current_user.is_authenticated else 'N/A'

    logger.warning(f"Accès refusé pour l'utilisateur '{user_display_name}' (rôle: {user_actual_role}) à une ressource nécessitant au moins le rôle '{required_role_name_for_message}'.")
    flash(f"Accès refusé. Vous devez avoir au moins le rôle '{required_role_name_for_message}' pour accéder à cette ressource.", "error")
    return redirect(url_for('index'))


def require_role(required_role_name):
    # Décorateur générique pour vérifier un rôle minimum.
    required_role_level = ROLES_HIERARCHIE.get(required_role_name)

    if required_role_level is None:
        logger.error(f"ERREUR DE CONFIGURATION: Nom de rôle requis invalide ('{required_role_name}') utilisé dans un décorateur @require_role.")
        # Cette fonction lambda retourne une autre fonction qui ignore ses arguments et appelle role_access_denied.
        # Essentiellement, si le nom du rôle est mal configuré dans le code, personne ne peut accéder à la route.
        def actual_decorator(func_to_decorate):
            @wraps(func_to_decorate)
            def wrapper(*a, **kw):
                return role_access_denied("rôle mal configuré en interne")
            return wrapper
        return actual_decorator

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # @login_required devrait être utilisé en premier sur la route pour s'assurer que current_user est authentifié.
            if not current_user.is_authenticated:
                # Redirection gérée par Flask-Login si @login_required est présent.
                # Sinon, comportement par défaut de Flask-Login (souvent un abort 401).
                # Pourrait explicitement appeler login_manager.unauthorized() ici si @login_required n'est pas garanti.
                # Mais la convention est d'empiler @login_required puis @<role>_required.
                pass # On suppose que @login_required a fait son travail ou que Flask-Login gère.

            user_role_str = getattr(current_user, 'role', None) # Obtient le rôle de l'objet User
            user_role_level = ROLES_HIERARCHIE.get(user_role_str, 0) # 0 si rôle inconnu ou non défini

            if user_role_level < required_role_level:
                return role_access_denied(required_role_name) # Message inclut le nom du rôle attendu
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def lecteur_required(f):
    return require_role("lecteur")(f)

def collaborateur_required(f):
    return require_role("collaborateur")(f)

def admin_required(f):
    return require_role("administrateur")(f)

# --- Fin Décorateurs de Rôle ---

# ==============================================================================
# Configuration du Logging Principal
# ==============================================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, 'logs')
LOG_FILE = os.path.join(LOG_DIR, 'sonnerie_backend.log')

# S'assurer que le dossier de logs existe
try:
    os.makedirs(LOG_DIR, exist_ok=True)
except OSError as e:
    print(f"AVERTISSEMENT: Impossible de créer le dossier logs {LOG_DIR}: {e}", file=sys.stderr)
    LOG_FILE = None # Désactiver logging fichier si dossier non créable

# Créer et configurer le logger principal
logger = logging.getLogger('sonnerie_backend')
# --- Niveau de Log Principal ---
# Mettre DEBUG pour voir tous les détails, INFO pour moins de verbosité
logger.setLevel(logging.INFO)
# -------------------------------
logger.propagate = False # Éviter double logging si logger racine configuré
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - [%(threadName)s] - %(name)s:%(lineno)d - %(message)s') # Ajout lineno

# Handler pour écrire dans un fichier rotatif
if LOG_FILE:
    try:
        file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5*1024*1024, backupCount=3, encoding='utf-8') # 5MB x 3 fichiers
        # --- Niveau Log Fichier ---
        file_handler.setLevel(logging.INFO) # Ou DEBUG si besoin
        # --------------------------
        file_handler.setFormatter(log_formatter)
        logger.addHandler(file_handler)
    except Exception as e:
         print(f"AVERTISSEMENT: Échec création handler log fichier {LOG_FILE}: {e}", file=sys.stderr)

# Handler pour afficher les logs dans la console (stderr, visible via NSSM/systemd)
console_handler = logging.StreamHandler(sys.stderr)
# --- Niveau Log Console ---
console_handler.setLevel(logging.INFO) # Ou DEBUG si besoin
# --------------------------
console_handler.setFormatter(log_formatter)
# Ajouter seulement si pas déjà présent (évite doublons si script relancé)
if not any(isinstance(h, logging.StreamHandler) and h.stream == sys.stderr for h in logger.handlers):
    logger.addHandler(console_handler)

logger.info("="*60)
logger.info(" démarrage du Logger Principal - Sonnerie Backend ".center(60, "="))
logger.info(f"Répertoire de base: {BASE_DIR}")
logger.info(f"Chemin Configuration: {CONFIG_PATH if CONFIG_PATH else 'Non défini/Inaccessible !'}")
logger.info(f"Chemin MP3: {MP3_PATH if MP3_PATH else 'Non défini/Inaccessible !'}")
logger.info(f"Fichier Log: {LOG_FILE if LOG_FILE else 'Désactivé'}")
logger.info("="*60)

# Vérification critique des chemins essentiels au démarrage
if not CONFIG_PATH or not os.path.isdir(CONFIG_PATH):
    logger.critical(f"Le chemin de configuration CONFIG_PATH ('{CONFIG_PATH}') est invalide ou inaccessible. Arrêt.")
    sys.exit("Erreur chemin configuration.")
if not MP3_PATH or not os.path.isdir(MP3_PATH):
    logger.critical(f"Le chemin des MP3 MP3_PATH ('{MP3_PATH}') est invalide ou inaccessible. Arrêt.")
    sys.exit("Erreur chemin MP3.")

# ==============================================================================
# Définitions Globales et Initialisation des Managers
# ==============================================================================

# --- Classe Utilisateur pour Flask-Login ---
class User(UserMixin):
    """Représente un utilisateur connecté."""
    def __init__(self, id, role="lecteur", nom_complet=""):
        self.id = id
        self.role = role
        self.nom_complet = nom_complet

@login_manager.user_loader
def load_user(user_id):
    """Charge un utilisateur à partir de l'ID stocké dans la session."""
    user_info = users_data.get(user_id)
    if user_info and isinstance(user_info, dict):
        user_role = user_info.get("role", "lecteur")
        user_nom_complet = user_info.get("nom_complet", "")
        return User(user_id, role=user_role, nom_complet=user_nom_complet)
    elif isinstance(user_info, str): # Fallback pour ancien format (juste le hash)
         logger.warning(f"Utilisateur '{user_id}' avec ancien format de données. Rôle 'lecteur' par défaut.")
         return User(user_id, role="lecteur", nom_complet="Utilisateur (format obsolète)")
    logger.warning(f"Tentative chargement utilisateur inexistant ou format invalide: {user_id}")
    return None # Indique à Flask-Login que l'utilisateur n'est plus valide

# --- Variables globales pour stocker la configuration chargée ---
users_data = {}
college_params = {}
day_types = {}
weekly_planning = {}
planning_exceptions = {}

# --- Instances des gestionnaires ---
# Initialiser HolidayManager (passe le logger et le dossier cache)
holiday_manager = HolidayManager(logger, cache_dir=CONFIG_PATH)

# Le scheduler sera initialisé après chargement config dans le bloc __main__
scheduler_thread = None
schedule_manager = None
alert_process = None # Référence au subprocess de l'alerte active


# ==============================================================================
# Fonctions de Chargement / Rechargement de la Configuration
# ==============================================================================

def load_users(filename=USERS_FILE):
    """Charge le fichier des utilisateurs (users.json)."""
    global users_data
    path = os.path.join(CONFIG_PATH, filename); logger.info(f"Load users: {path}")
    try:
        with open(path, 'r', encoding='utf-8') as f: users_data = json.load(f)
        logger.info(f"Users OK ({len(users_data)})."); return True
    except FileNotFoundError: logger.info(f"Users file not found: {path}. Init vide."); users_data = {}; return True
    except json.JSONDecodeError: logger.error(f"Users file JSON error: {path}"); users_data = {}; return False
    except Exception as e: logger.error(f"Users load error: {path}: {e}", exc_info=True); users_data = {}; return False

def load_college_params(filename=PARAMS_FILE):
    """Charge les paramètres généraux (parametres_college.json) et lance le chargement API fériés."""
    global college_params, holiday_manager
    path = os.path.join(CONFIG_PATH, filename); logger.info(f"Load params: {path}")
    try:
        with open(path, 'r', encoding='utf-8') as f: college_params = json.load(f)
        logger.info(f"Params OK: {college_params}")
        api_url = college_params.get('api_holidays_url'); country = college_params.get('country_code_holidays', 'FR')
        if holiday_manager: holiday_manager.load_holidays_from_api(api_url, country) # Log interne à HM
        else: logger.error("HolidayManager non init pour load holidays.")
        return True
    except FileNotFoundError: logger.info(f"Params file not found: {path}. Init vide."); college_params = {}; return True
    except json.JSONDecodeError: logger.error(f"Params file JSON error: {path}"); college_params = {}; return False
    except Exception as e: logger.error(f"Params load error: {path}: {e}", exc_info=True); college_params = {}; return False

def load_sonneries_data(filename=DONNEES_SONNERIES_FILE):
    """Charge les données des sonneries (donnees_sonneries.json) et lance le chargement des vacances."""
    global day_types, weekly_planning, planning_exceptions, holiday_manager, college_params
    path = os.path.join(CONFIG_PATH, filename); logger.info(f"Load sonneries data: {path}")
    try:
        with open(path, 'r', encoding='utf-8') as f: data = json.load(f)
        day_types = data.get("journees_types", {}); weekly_planning = data.get("planning_hebdomadaire", {})
        planning_exceptions = data.get("exceptions_planning", {}); logger.info(f"Sonneries data OK.")
        # Charger vacances après, en utilisant zone et url manuelle des params déjà chargés
        ics_path_local = data.get('vacances', {}).get('ics_file_path')
        zone = college_params.get('zone') # Lire la zone
        manual_url = college_params.get("vacances_ics_base_url_manuel") # Lire URL manuelle
        if holiday_manager: holiday_manager.load_vacations(zone=zone, local_ics_path=ics_path_local, manual_ics_base_url=manual_url)
        else: logger.error("HolidayManager non init pour load vacations.")
        return True
    except FileNotFoundError: logger.info(f"Sonneries data file not found: {path}. Init vide."); day_types={}; weekly_planning={}; planning_exceptions={}; return True
    except json.JSONDecodeError: logger.error(f"Sonneries data JSON error: {path}"); day_types={}; weekly_planning={}; planning_exceptions={}; return False
    except Exception as e: logger.error(f"Sonneries data load error: {path}: {e}", exc_info=True); day_types={}; weekly_planning={}; planning_exceptions={}; return False

def load_all_configs():
    """Charge toutes les configurations dans le bon ordre."""
    logger.info("Chargement de toutes les configurations...")
    # Ordre: Params (pour zone/URL) -> Sonneries (utilise params) -> Users
    success_params = load_college_params()
    success_sonneries = load_sonneries_data()
    success_users = load_users()
    all_ok = success_users and success_params and success_sonneries
    if all_ok: logger.info("Chargement configs terminé (sans erreur de format).")
    else: logger.error("Erreur de format ou inattendue lors chargement config.")
    return all_ok


# ==============================================================================
# Initialisation et Contrôle du Scheduler
# ==============================================================================

def start_scheduler_thread():
    """Tente de démarrer le thread du scheduler si les pré-requis sont OK."""
    global scheduler_thread, schedule_manager
    logger.info("Vérification pré-requis scheduler...")
    if scheduler_thread and scheduler_thread.is_alive(): logger.warning("Scheduler déjà lancé."); return False
    if not day_types: logger.error("Start scheduler échoué: journees_types vide."); return False
    if not weekly_planning: logger.error("Start scheduler échoué: planning_hebdomadaire vide."); return False
    if not MP3_PATH or not os.path.isdir(MP3_PATH): logger.error(f"Start scheduler échoué: MP3_PATH invalide: {MP3_PATH}"); return False

    try:
        logger.info("Création instance SchedulerManager...")
        # schedule_manager = SchedulerManager(day_types, weekly_planning, planning_exceptions, holiday_manager, MP3_PATH, logger) # OLD
        audio_device_from_params = college_params.get("nom_peripherique_audio_sonneries")
        schedule_manager = SchedulerManager(day_types, weekly_planning, planning_exceptions, holiday_manager, MP3_PATH, logger, audio_device_name=audio_device_from_params) # NEW
        logger.info("Création thread Scheduler..."); scheduler_thread = threading.Thread(target=schedule_manager.run, name="SchedulerThread"); scheduler_thread.daemon = True; scheduler_thread.start()
        logger.info("Thread Scheduler démarré."); return True
    except Exception as e:
        logger.error(f"Erreur critique lors de l'initialisation/démarrage du Scheduler: {e}", exc_info=True)
        schedule_manager = None; scheduler_thread = None; return False


# ==============================================================================
# Routes Flask : Authentification et Pages
# ==============================================================================

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Affiche et traite le formulaire de connexion."""
    if current_user.is_authenticated: return redirect(url_for('index')) # Déjà loggué
    if request.method == 'POST':
        username = request.form.get('username'); password = request.form.get('password')
        logger.debug(f"Tentative login formulaire pour: {username}")
        user_data_dict = users_data.get(username) # NOUVELLE LIGNE (remplace user_hash)

        if user_data_dict and isinstance(user_data_dict, dict) and check_password_hash(user_data_dict.get("hash"), password): # NOUVELLE CONDITION
            user_role = user_data_dict.get("role", "lecteur") # Valeur par défaut
            user_nom_complet = user_data_dict.get("nom_complet", "") # Valeur par défaut
            user_obj = User(username, role=user_role, nom_complet=user_nom_complet) # Passer les nouvelles infos
            login_user(user_obj); logger.info(f"Login OK pour '{username}'. Role: '{user_role}', Nom: '{user_nom_complet}'.")
            next_page = request.args.get('next'); logger.info(f"Redirection post-login vers: {next_page or url_for('index')}")
            return redirect(next_page or url_for('index'))
        elif isinstance(user_data_dict, str) and check_password_hash(user_data_dict, password): # Fallback pour ancien format (valeur est le hash)
            logger.warning(f"Utilisateur '{username}' avec ancien format de données (login). Rôle 'lecteur' par défaut.")
            user_obj = User(username, role="lecteur", nom_complet="Utilisateur (format obsolète)")
            login_user(user_obj); logger.info(f"Login OK (ancien format) pour '{username}'.")
            next_page = request.args.get('next'); logger.info(f"Redirection post-login vers: {next_page or url_for('index')}")
            return redirect(next_page or url_for('index'))
        else:
            logger.warning(f"Login échoué pour: {username}. Données utilisateur: {user_data_dict}")
            flash("Identifiants incorrects.", "error")
    return render_template('login.html') # Afficher si GET ou si POST échoue

@app.route('/logout')
@login_required # Doit être connecté pour se déconnecter
def logout():
    """Déconnecte l'utilisateur."""
    user_id = current_user.id; logout_user(); logger.info(f"Utilisateur '{user_id}' déconnecté.")
    flash("Vous avez été déconnecté.", "info"); return redirect(url_for('login'))

@app.route('/config/general')
@login_required
@collaborateur_required
def config_general_page():
    """Sert la page de configuration générale et des alertes."""
    user_id = current_user.id
    logger.info(f"User '{user_id}' accède à la page /config/general")
    # Importer les constantes ici pour les passer au template
    from constants import DEPARTEMENTS_ZONES, LISTE_DEPARTEMENTS
    # On passe current_user et les constantes au template
    return render_template('config_general.html', current_user=current_user,
                           constants={"DEPARTEMENTS_ZONES": DEPARTEMENTS_ZONES,
                                      "LISTE_DEPARTEMENTS": LISTE_DEPARTEMENTS})

@app.route('/config/weekly')
@login_required
@collaborateur_required
def config_weekly_page():
    """Sert la page de configuration du planning hebdomadaire."""
    user_id = current_user.id
    logger.info(f"User '{user_id}' accède à la page /config/weekly")
    # On passe current_user pour la barre de navigation du template
    return render_template('config_weekly.html', current_user=current_user)

@app.route('/config/day_types')
@login_required
@collaborateur_required
def config_day_types_page():
    """Sert la page de configuration des journées types."""
    user_id = current_user.id
    logger.info(f"User '{user_id}' accède à la page /config/day_types")
    # On pourrait passer la liste des noms de JT directement ici pour éviter un appel API initial,
    # mais pour l'instant on laisse le JS faire l'appel API.
    return render_template('config_day_types.html', current_user=current_user)

@app.route('/config/exceptions')
@login_required
@collaborateur_required
def config_exceptions_page():
    """Sert la page de configuration des exceptions de planning."""
    user_id = current_user.id
    logger.info(f"User '{user_id}' accède à la page /config/exceptions")
    # On pourrait passer la liste des JT ici pour le select, mais le JS la chargera.
    return render_template('config_exceptions.html', current_user=current_user)
@app.route('/api/config/sounds', methods=['GET'])
@login_required

def get_configured_sounds():
    """
    Retourne la liste des sonneries configurées (nom convivial -> nom de fichier).
    """
    user_id = current_user.id
    logger.info(f"User '{user_id}' requête GET /api/config/sounds")

    try:
        donnees_path = os.path.join(CONFIG_PATH, DONNEES_SONNERIES_FILE)
        configured_sounds = {}

        if os.path.exists(donnees_path):
            with open(donnees_path, 'r', encoding='utf-8') as f:
                donnees_sonneries = json.load(f)
            configured_sounds = donnees_sonneries.get("sonneries", {})
            if not isinstance(configured_sounds, dict):
                logger.warning(f"Section 'sonneries' invalide dans {DONNEES_SONNERIES_FILE}, retour liste vide.")
                configured_sounds = {}
        else:
            logger.warning(f"Fichier {DONNEES_SONNERIES_FILE} non trouvé pour API /api/config/sounds.")
            # Renvoyer un objet vide si le fichier n'existe pas

        logger.debug(f"API List Configured Sounds renvoie: {len(configured_sounds)} sonneries")
        return jsonify({"configured_sounds": configured_sounds}), 200

    except json.JSONDecodeError as e_json:
        logger.error(f"Erreur JSON lecture {DONNEES_SONNERIES_FILE} pour API sounds: {e_json}", exc_info=True)
        return jsonify({"error": "Erreur format fichier config sonneries."}), 500
    except Exception as e:
        logger.error(f"Erreur API GET /api/config/sounds: {e}", exc_info=True)
        return jsonify({"error": f"Erreur serveur: {str(e)}"}), 500

@app.route('/config/sounds')
@login_required
@collaborateur_required
def config_sounds_page():
    """Sert la page de configuration des sonneries disponibles."""
    user_id = current_user.id
    logger.info(f"User '{user_id}' accède à la page /config/sounds")
    return render_template('config_sounds.html', current_user=current_user)

@app.route('/')
@login_required # Protéger la page principale
def index():
    """Sert la page de contrôle principale (control.html)."""
    user = current_user.id; logger.info(f"User '{user}' accède à '/'...")
    try: return render_template('control.html', current_user=current_user) # Passer l'user au template
    except Exception as e: logger.error(f"Erreur rendu template control.html: {e}", exc_info=True); return "Erreur interne serveur.", 500


# ==============================================================================
# Routes Flask : API
# ==============================================================================

@app.route('/api/status')
# @login_required # Laisser public pour affichage initial simple
def api_status():
    """Retourne le statut actuel (scheduler, alerte, prochaine sonnerie)."""
    logger.debug("Requête /api/status")
    # ... (Code de api_status, inchangé et correct) ...
    sch_running = False; next_time = None; next_label = None; last_err = "Scheduler non initialisé"; alert_act = alert_process is not None and alert_process.poll() is None
    if schedule_manager: sch_running = schedule_manager.is_running(); next_time = schedule_manager.get_next_ring_time_iso(); next_label = schedule_manager.get_next_ring_label(); last_err = schedule_manager.get_last_error()
    status = {"scheduler_running": sch_running, "next_ring_time": next_time, "next_ring_label": next_label, "last_error": last_err or "Aucune", "alert_active": alert_act, "current_time": datetime.now().isoformat()}
    logger.debug(f"Statut renvoyé: {status}"); return jsonify(status)

@app.route('/api/planning/activate', methods=['POST'])
@login_required
@collaborateur_required
def activate_planning():
    """Active le scheduler."""
    user = current_user.id; logger.info(f"User '{user}': Activate planning"); msg = "Planning déjà actif."; code = 200
    if schedule_manager:
        if not schedule_manager.is_running(): schedule_manager.start(); logger.info("Scheduler activé."); msg = "Planning activé."
        else: logger.warning("Activation demandée mais déjà actif.")
    else: msg = "Scheduler non initialisé."; code = 500; logger.error(msg)
    return jsonify({"message": msg}), code

@app.route('/api/planning/deactivate', methods=['POST'])
@login_required
@collaborateur_required
def deactivate_planning():
    """Désactive le scheduler."""
    user = current_user.id; logger.info(f"User '{user}': Deactivate planning"); msg = "Planning déjà inactif."; code = 200
    if schedule_manager:
        if schedule_manager.is_running(): schedule_manager.stop(); logger.info("Scheduler désactivé."); msg = "Planning désactivé."
        else: logger.warning("Désactivation demandée mais déjà inactif.")
    else: msg = "Scheduler non initialisé."; code = 500; logger.error(msg)
    return jsonify({"message": msg}), code

# --- Fonction Helper pour arrêter l'alerte ---
def stop_current_alert_process():
    """Tente d'arrêter le processus d'alerte courant."""
    global alert_process; action_taken = False
    if alert_process and alert_process.poll() is None:
        pid = alert_process.pid; logger.warning(f"Arrêt processus alerte (PID: {pid})...")
        action_taken = True
        try:
            alert_process.terminate(); alert_process.wait(timeout=2); logger.info(f"Alerte (PID: {pid}) arrêtée (terminate).")
        except subprocess.TimeoutExpired: logger.error(f"Timeout arrêt alerte (PID:{pid}), kill."); alert_process.kill(); alert_process.wait(); logger.info(f"Alerte (PID: {pid}) arrêtée (kill).")
        except Exception as e: logger.error(f"Erreur arrêt alerte (PID:{pid}): {e}", exc_info=True)
        finally: alert_process = None # Toujours réinitialiser
    else: logger.debug("stop_current_alert_process: Pas d'alerte active.")
    return action_taken

@app.route('/api/alert/trigger/<filename>', methods=['POST'])
@login_required
@lecteur_required
def trigger_alert(filename):
    """Déclenche une alerte (arrête la précédente si besoin)."""
    user = current_user.id; logger.info(f"User '{user}': Trigger alert: {filename}"); global alert_process
    logger.info("Vérification et arrêt alerte précédente..."); stop_current_alert_process()
    if not MP3_PATH or not os.path.isdir(MP3_PATH): logger.error(f"Trigger alert échoué: MP3_PATH invalide: {MP3_PATH}"); return jsonify({"error": "Config MP3 invalide."}), 500
    sound_path = os.path.join(MP3_PATH, filename)
    if not os.path.isfile(sound_path): logger.error(f"Alert file not found: {sound_path}"); return jsonify({"error": f"Fichier alerte '{filename}' introuvable."}), 404
    try:
        logger.info(f"Lancement processus alerte '{filename}' (non-boucle)...")
        audio_device_name = college_params.get("nom_peripherique_audio_sonneries")
        cmd = [sys.executable, __file__, '--play-sound', sound_path]
        if audio_device_name:
            cmd.extend(['--device', audio_device_name])
            logger.info(f"Triggering alert '{filename}' on device: {audio_device_name}")
        else:
            logger.info(f"Triggering alert '{filename}' on default device.")
        logger.debug(f"Cmd: {' '.join(cmd)}")
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0; alert_process = subprocess.Popen(cmd, creationflags=flags)
        logger.info(f"Nouveau processus alerte démarré (PID: {alert_process.pid})."); return jsonify({"message": f"Alerte '{filename}' déclenchée (durée limitée)."}), 200
    except Exception as e: logger.error(f"Erreur lancement processus alerte: {e}", exc_info=True); alert_process = None; return jsonify({"error": f"Erreur serveur: {e}"}), 500

@app.route('/api/alert/stop', methods=['POST'])
@login_required
@lecteur_required
def stop_alert():
    """Arrête l'alerte active."""
    user = current_user.id; logger.info(f"User '{user}': Stop alert via API")
    stopped = stop_current_alert_process()
    return jsonify({"message": "Tentative d'arrêt effectuée." if stopped else "Aucune alerte active."}), 200

@app.route('/api/alert/end', methods=['POST'])
@login_required
@lecteur_required
def end_alert():
    """Arrête l'alerte en cours ET joue le son de fin d'alerte."""
    user = current_user.id
    logger.info(f"User '{user}': Déclenchement FIN d'alerte")
    global alert_process # On va lire college_params

    # 1. Arrêter l'alerte en cours
    logger.info("Arrêt de l'alerte en cours (si active)...")
    stop_current_alert_process() # Utilise la fonction helper

    # 2. Jouer le son de fin d'alerte
    fin_alerte_filename = college_params.get("sonnerie_fin_alerte")
    if not fin_alerte_filename:
        logger.warning("Aucune sonnerie de fin d'alerte configurée.")
        # On retourne succès quand même, car l'alerte principale est arrêtée
        return jsonify({"message": "Alerte arrêtée (pas de son de fin configuré)."}), 200

    # Vérifier MP3_PATH et fichier
    if not MP3_PATH or not os.path.isdir(MP3_PATH):
        logger.error(f"Fin alerte échouée: MP3_PATH invalide: {MP3_PATH}")
        return jsonify({"error": "Config MP3 invalide."}), 500
    sound_path = os.path.join(MP3_PATH, fin_alerte_filename)
    if not os.path.isfile(sound_path):
        logger.error(f"Fichier fin d'alerte introuvable: {sound_path}")
        return jsonify({"error": f"Fichier fin d'alerte '{fin_alerte_filename}' introuvable."}), 404

    # Lancer le son de fin (non bloquant, sans boucle)
    try:
        logger.info(f"Lancement processus pour son de fin d'alerte: {fin_alerte_filename}")
        audio_device_name = college_params.get("nom_peripherique_audio_sonneries")
        cmd = [sys.executable, __file__, '--play-sound', sound_path] # Pas de --loop
        if audio_device_name:
            cmd.extend(['--device', audio_device_name])
            logger.info(f"Playing end_alert sound '{fin_alerte_filename}' on device: {audio_device_name}")
        else:
            logger.info(f"Playing end_alert sound '{fin_alerte_filename}' on default device.")
        logger.debug(f"Cmd fin alerte: {' '.join(cmd)}")
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        # On ne suit pas ce processus
        subprocess.Popen(cmd, creationflags=flags)
        logger.info(f"Processus fin d'alerte lancé pour jouer {fin_alerte_filename}.")
        return jsonify({"message": f"Fin d'alerte déclenchée ({fin_alerte_filename})."}), 200
    except Exception as e:
        logger.error(f"Erreur lancement processus fin d'alerte: {e}", exc_info=True)
        return jsonify({"error": f"Erreur serveur lancement fin alerte: {e}"}), 500

@app.route('/api/config/reload', methods=['POST'])
@login_required
@admin_required
def reload_config_route():
    """Recharge tous les fichiers de configuration et met à jour le scheduler."""
    user = current_user.id; logger.info(f"User '{user}': Reload config"); msg=""
    try:
        logger.info("Rechargement config depuis fichiers..."); load_ok = load_all_configs()
        if not load_ok: logger.error("Échec rechargement fichiers config."); return jsonify({"error": "Erreur lecture fichiers config."}), 500
        if schedule_manager:
            logger.info("Notification scheduler pour reload...")
            # schedule_manager.reload_schedule(day_types, weekly_planning, planning_exceptions, holiday_manager) # OLD
            audio_device_from_params_reload = college_params.get("nom_peripherique_audio_sonneries")
            schedule_manager.reload_schedule(day_types, weekly_planning, planning_exceptions, holiday_manager, audio_device_name=audio_device_from_params_reload) # NEW
            msg = "Config rechargée & planning màj." if schedule_manager.is_running() else "Config rechargée (scheduler inactif)."
            logger.info(msg)
        else:
            logger.warning("Config rechargée, tentative démarrage scheduler..."); sch_started = start_scheduler_thread()
            if sch_started and schedule_manager: logger.info("Scheduler démarré post-reload. Activation..."); schedule_manager.start()
            msg = "Config rechargée" + (", scheduler démarré." if sch_started else ", échec démarrage scheduler.")
        return jsonify({"message": msg}), 200
    except Exception as e: logger.error(f"Erreur majeure reload config API: {e}", exc_info=True); return jsonify({"error": f"Erreur serveur reload: {e}"}), 500

@app.route('/api/config/settings')
@login_required
def api_config_settings():
    """Retourne les paramètres utiles à l'UI (noms fichiers alerte)."""
    user = current_user.id; logger.debug(f"User '{user}' requête /api/config/settings")
    try:
        settings = {"alert_files": {"ppms": college_params.get("sonnerie_ppms"), "attentat": college_params.get("sonnerie_attentat")}}
        return jsonify(settings)
    except Exception as e: logger.error(f"Erreur API /api/config/settings: {e}", exc_info=True); return jsonify({"error": "Erreur serveur"}), 500

@app.route('/api/audio_devices', methods=['GET'])
@login_required
def get_audio_devices():
    user_id = current_user.id
    logger.info(f"User '{user_id}' requête GET /api/audio_devices")

    if not SOUNDDEVICE_AVAILABLE:
        logger.error("API /api/audio_devices: sounddevice n'est pas disponible.")
        return jsonify({"audio_devices": [{"name": "Périphérique par défaut système", "id": None}], "error": "sounddevice_unavailable"}), 503

    devices_options = []
    default_device_option = {"name": "Périphérique par défaut système", "id": None}
    devices_options.append(default_device_option)

    processed_devices = {} # Utiliser un dictionnaire pour stocker les noms candidats

    try:
        device_list = sd.query_devices()

        for device_info in device_list:
            if device_info.get('max_output_channels', 0) > 0:
                device_name_full = device_info.get('name', 'Périphérique inconnu').strip()
                if not device_name_full:
                    continue

                # Filtrer les noms clairement tronqués ou non désirés
                if '(' in device_name_full and ')' not in device_name_full:
                    logger.debug(f"Périphérique ignoré (parenthèse non fermée): {device_name_full}")
                    continue
                if "Microsoft Sound Mapper" in device_name_full or                    "Périphérique audio principal" in device_name_full or                    "Primary Sound Capture Driver" in device_name_full or                    "Pilote de capture audio principal" in device_name_full: # Exemples de noms à ignorer
                    logger.debug(f"Périphérique ignoré (générique système/entrée): {device_name_full}")
                    continue

                # Logique pour préférer les noms plus longs/complets
                # Si un nom est un préfixe d'un nom déjà dans processed_devices, on ne fait rien.
                # Si un nom dans processed_devices est un préfixe du nom actuel, on remplace.
                # Sinon, on ajoute.

                should_add_new = True
                keys_to_remove = []

                for existing_name in processed_devices.keys():
                    if device_name_full.startswith(existing_name) and len(device_name_full) > len(existing_name):
                        # Le nouveau est plus long et l'existant est un préfixe, marquer l'existant pour suppression
                        keys_to_remove.append(existing_name)
                    elif existing_name.startswith(device_name_full) and len(existing_name) > len(device_name_full):
                        # L'existant est plus long et le nouveau est un préfixe, ne pas ajouter le nouveau
                        should_add_new = False
                        break
                    elif device_name_full == existing_name: # Exactement le même nom
                        should_add_new = False
                        break

                for key_rem in keys_to_remove:
                    logger.debug(f"Remplacement de '{key_rem}' par une version plus longue '{device_name_full}'")
                    del processed_devices[key_rem]

                if should_add_new:
                    logger.debug(f"Ajout du périphérique candidat: {device_name_full}")
                    processed_devices[device_name_full] = device_name_full

        # Convertir le dictionnaire de noms traités en liste pour l'API, triée pour un ordre consistant
        for name_id in sorted(list(processed_devices.keys())):
            devices_options.append({"name": name_id, "id": name_id})

        logger.info(f"API /api/audio_devices: {len(processed_devices)} périphériques de sortie uniques et filtrés trouvés.")
        return jsonify({"audio_devices": devices_options})

    except Exception as e:
        logger.error(f"Erreur lors de la récupération des périphériques audio: {e}", exc_info=True)
        # En cas d'erreur majeure, retourner seulement l'option par défaut
        return jsonify({"audio_devices": [default_device_option], "error": str(e)}), 500

@app.route('/api/calendar_view')
@login_required # Protéger l'accès au calendrier détaillé
@lecteur_required
def api_calendar_view():
    """Fournit les données pour le calendrier annuel/scolaire."""
    user = current_user.id; logger.debug(f"User '{user}' requête /api/calendar_view")
    try:
        year_param = request.args.get('year', default=str(datetime.now().year))
        start_date, end_date = None, None; year_label = year_param # Pour log
        if '-' in year_param: # Année scolaire YYYY-YYYY
            try:
                start_y, end_y = map(int, year_param.split('-')); assert end_y == start_y + 1
                start_date = date(start_y, 9, 1); end_date = date(end_y, 8, 31)
            except: logger.warning(f"Format année scolaire invalide: {year_param}. Utilisation année civile."); year_param = str(datetime.now().year) # Fallback
        if start_date is None: # Année civile YYYY
             try: year = int(year_param); start_date = date(year, 1, 1); end_date = date(year, 12, 31)
             except ValueError: logger.error(f"Param année invalide: {year_param}. Utilisation année courante."); year = datetime.now().year; start_date = date(year, 1, 1); end_date = date(year, 12, 31); year_label = str(year)
        logger.info(f"Calcul calendrier pour période: {start_date} à {end_date} (demandé: {year_label})")
        data = get_calendar_view_data_range(start_date, end_date)
        if 'error' in data: return jsonify({"error": f"Erreur gen calendrier: {data['error']}"}), 500
        return jsonify(data)
    except Exception as e: logger.error(f"Erreur API /api/calendar_view: {e}", exc_info=True); return jsonify({"error": f"Erreur serveur API: {e}"}), 500

def get_calendar_view_data_range(start_date, end_date):
    """Prépare les données calendrier pour une période."""
    logger.debug(f"Prep données calendrier: {start_date} -> {end_date}"); data = {'days': {}, 'vacations': [], 'holidays': []}
    try:
        curr = start_date
        while curr <= end_date:
            date_str = curr.strftime('%Y-%m-%d')
            if holiday_manager: day_info = holiday_manager.get_day_type_and_desc(curr, weekly_planning, planning_exceptions)
            else: day_info = {"type": "Erreur", "description": "HolidayManager NA", "schedule_name": None}
            data['days'][date_str] = {"type": day_info['type'], "description": day_info['description']}
            curr += timedelta(days=1)
        if holiday_manager: data['vacations'] = holiday_manager.get_vacation_periods(); data['holidays'] = [{"date": d.strftime('%Y-%m-%d'), "description": desc} for d, desc in holiday_manager.get_holidays()]
    except Exception as e: logger.error(f"Erreur get_calendar_view_data_range: {e}", exc_info=True); data['error'] = str(e)
    logger.debug(f"Données calendrier prêtes ({len(data['days'])} jours)."); return data

@app.route('/api/daily_schedule')
@login_required
@lecteur_required
def api_daily_schedule():
    """Fournit le planning détaillé pour une date."""
    user = current_user.id; date_str = request.args.get('date'); logger.debug(f"User '{user}' requête /api/daily_schedule?date={date_str}")
    if not date_str: logger.warning("Param 'date' manquant."); return jsonify({"error": "Param 'date' manquant."}), 400
    schedule_details = get_daily_schedule_data(date_str)
    if "error" in schedule_details: return jsonify(schedule_details), 500
    else: return jsonify(schedule_details), 200

def get_daily_schedule_data(date_str):
    logger.debug(f"get_daily_schedule_data pour: {date_str}")
    try: target_date = date.fromisoformat(date_str)
    except ValueError: logger.warning(f"Format date invalide: {date_str}"); return {"error": "Format date invalide (YYYY-MM-DD)."}
    if not schedule_manager: logger.warning("Scheduler non init pour daily schedule."); return {"message": "Scheduler non actif.", "schedule": [], "day_type": "Inconnu"}
    try: return schedule_manager.get_schedule_for_date(target_date)
    except Exception as e: logger.error(f"Erreur get_schedule_for_date({target_date}): {e}", exc_info=True); return {"error": f"Erreur serveur: {e}"}

# --- NOUVELLES ROUTES POUR LA CONFIGURATION WEB ---

@app.route('/api/config/general_and_alerts', methods=['GET'])
@login_required
def get_general_and_alerts_config():
    """
    Fournit les paramètres généraux et d'alerte, ainsi que la liste des sonneries.
    """
    user_id = current_user.id
    logger.info(f"User '{user_id}' requête GET /api/config/general_and_alerts")

    try:
        # Charger parametres_college.json
        params_path = os.path.join(CONFIG_PATH, PARAMS_FILE)
        current_params = {}
        if os.path.exists(params_path):
            with open(params_path, 'r', encoding='utf-8') as f:
                current_params = json.load(f)
        else:
            logger.warning(f"Fichier {PARAMS_FILE} introuvable lors de la lecture pour l'API config.")
            # Renvoyer des valeurs par défaut ou une erreur ? Pour GET, valeurs par défaut est plus souple.
            current_params = {
                "departement": "", "zone": "", "api_holidays_url": "", "country_code_holidays": "FR",
                "vacances_ics_base_url_manuel": None, "sonnerie_ppms": None, "sonnerie_attentat": None,
                "sonnerie_fin_alerte": None
            }

        # Charger la liste des sonneries depuis donnees_sonneries.json
        sonneries_data_path = os.path.join(CONFIG_PATH, DONNEES_SONNERIES_FILE)
        available_ringtones = {}
        if os.path.exists(sonneries_data_path):
            with open(sonneries_data_path, 'r', encoding='utf-8') as f:
                donnees_sonneries = json.load(f)
            available_ringtones = donnees_sonneries.get("sonneries", {})
            if not isinstance(available_ringtones, dict):
                logger.warning(f"La section 'sonneries' dans {DONNEES_SONNERIES_FILE} n'est pas un dictionnaire. Renvoyer liste vide.")
                available_ringtones = {}
        else:
            logger.warning(f"Fichier {DONNEES_SONNERIES_FILE} introuvable. Aucune sonnerie disponible pour la config.")

        # Préparer les données à renvoyer
        # On ne renvoie que les clés utiles pour cette page de config
        config_data_to_return = {
            "departement": current_params.get("departement"),
            "zone": current_params.get("zone"), # La zone est aussi lue, elle est souvent liée au département
            "vacances_ics_base_url_manuel": current_params.get("vacances_ics_base_url_manuel"),
            "sonnerie_ppms": current_params.get("sonnerie_ppms"),
            "sonnerie_attentat": current_params.get("sonnerie_attentat"),
            "sonnerie_fin_alerte": current_params.get("sonnerie_fin_alerte"), # Ajouté !
            "nom_peripherique_audio_sonneries": current_params.get("nom_peripherique_audio_sonneries"), # LIGNE IMPORTANTE
            "available_ringtones": available_ringtones # Format { "Nom Affiché": "fichier.mp3", ... }
        }
        # On pourrait aussi inclure api_holidays_url et country_code_holidays si on veut les configurer

        logger.debug(f"Config générale et alertes renvoyée: {config_data_to_return}")
        return jsonify(config_data_to_return), 200

    except json.JSONDecodeError as e_json:
        logger.error(f"Erreur JSON lecture config pour API /api/config/general_and_alerts: {e_json}", exc_info=True)
        return jsonify({"error": "Erreur de format dans un fichier de configuration."}), 500
    except Exception as e:
        logger.error(f"Erreur API GET /api/config/general_and_alerts: {e}", exc_info=True)
        return jsonify({"error": f"Erreur serveur: {str(e)}"}), 500

@app.route('/api/config/general_and_alerts', methods=['POST'])
@login_required
def set_general_and_alerts_config():
    """
    Met à jour les paramètres généraux et d'alerte dans parametres_college.json.
    """
    user_id = current_user.id
    logger.info(f"User '{user_id}' requête POST /api/config/general_and_alerts")

    try:
        # Récupérer les données JSON envoyées par le client
        data_from_client = request.get_json()
        if not data_from_client:
            logger.warning("Requête POST sans données JSON.")
            return jsonify({"error": "Aucune donnée JSON reçue."}), 400

        logger.debug(f"Données reçues du client: {data_from_client}")

        params_path = os.path.join(CONFIG_PATH, PARAMS_FILE)

        # Charger la configuration actuelle pour ne modifier que les clés nécessaires
        current_params = {}
        if os.path.exists(params_path):
            with open(params_path, 'r', encoding='utf-8') as f:
                current_params = json.load(f)
        else:
            # Si le fichier n'existe pas, on va le créer.
            # Cela ne devrait pas arriver si le backend a démarré correctement.
            logger.warning(f"Fichier {PARAMS_FILE} introuvable. Il sera créé.")
            # Initialiser avec des valeurs par défaut au cas où.
            current_params = {
                "departement": None, "zone": None, "api_holidays_url": "https://date.nager.at/api/v3/PublicHolidays",
                "country_code_holidays": "FR", "vacances_ics_base_url_manuel": None,
                "sonnerie_ppms": None, "sonnerie_attentat": None, "sonnerie_fin_alerte": None
            }

        # Mettre à jour les clés concernées avec les valeurs reçues du client
        # On vérifie si la clé est présente dans data_from_client avant de mettre à jour,
        # pour permettre des mises à jour partielles si on le souhaitait.
        # Pour cette page, on s'attend à ce que toutes les clés pertinentes soient envoyées.

        if "departement" in data_from_client:
            current_params["departement"] = data_from_client["departement"]
            # La zone est déduite du département, elle sera mise à jour lors du prochain rechargement
            # ou si on a une logique spécifique ici pour la recalculer et la sauvegarder.
            # Pour l'instant, on ne sauvegarde que le département.
            # Si on a besoin de la zone exacte, il faudra la chercher dans DEPARTEMENTS_ZONES.
            if "zone" in data_from_client: # Si le client envoie la zone, on la prend aussi
                 current_params["zone"] = data_from_client["zone"]


        if "vacances_ics_base_url_manuel" in data_from_client:
            # Si la chaîne est vide, on stocke None
            current_params["vacances_ics_base_url_manuel"] = data_from_client["vacances_ics_base_url_manuel"] or None

        if "sonnerie_ppms" in data_from_client:
            current_params["sonnerie_ppms"] = data_from_client["sonnerie_ppms"] or None # null si ""

        if "sonnerie_attentat" in data_from_client:
            current_params["sonnerie_attentat"] = data_from_client["sonnerie_attentat"] or None # null si ""

        if "sonnerie_fin_alerte" in data_from_client:
            current_params["sonnerie_fin_alerte"] = data_from_client["sonnerie_fin_alerte"] or None # null si ""

        if "nom_peripherique_audio_sonneries" in data_from_client:
            # La valeur envoyée par le JS sera soit le nom du device (string), soit null (qui devient None en Python).
            current_params["nom_peripherique_audio_sonneries"] = data_from_client["nom_peripherique_audio_sonneries"]
            logger.info(f"Audio device for sonneries set to: {current_params['nom_peripherique_audio_sonneries']}")

        # Sauvegarder le fichier mis à jour
        with open(params_path, 'w', encoding='utf-8') as f:
            json.dump(current_params, f, indent=2, ensure_ascii=False)

        logger.info(f"Fichier {PARAMS_FILE} mis à jour avec succès.")

        # Important : Après une modification de configuration, il faut notifier le backend
        # pour qu'il recharge ses configurations en mémoire.
        # Nous avons déjà la fonction reload_config_route() pour cela.
        # Cependant, appeler une autre route directement d'ici n'est pas l'idéal.
        # Le mieux est que le client (JavaScript) appelle /api/config/reload APRÈS cette route POST.
        # Ou, si nous voulons le faire côté serveur, nous devrions appeler directement les fonctions
        # load_all_configs() et schedule_manager.reload_schedule() comme le fait reload_config_route().

        # Pour l'instant, on retourne juste un succès. Le client JS devra gérer le reload.
        return jsonify({"message": "Configuration générale et alertes mise à jour avec succès."}), 200

    except json.JSONDecodeError as e_json:
        logger.error(f"Erreur JSON lors de la mise à jour de {PARAMS_FILE}: {e_json}", exc_info=True)
        return jsonify({"error": "Erreur de format dans les données reçues ou le fichier de configuration existant."}), 400 # 400 Bad Request ou 500
    except Exception as e:
        logger.error(f"Erreur API POST /api/config/general_and_alerts: {e}", exc_info=True)
        return jsonify({"error": f"Erreur serveur: {str(e)}"}), 500

@app.route('/api/sound/<path:filename>')
@login_required
def serve_sound_file(filename):
    """
    Sert un fichier son depuis le dossier MP3_PATH.
    Utilisé pour la pré-écoute dans l'interface de configuration.
    """
    user_id = current_user.id
    logger.info(f"User '{user_id}' demande le fichier son: {filename} depuis MP3_PATH: {MP3_PATH}")

    if not MP3_PATH or not os.path.isdir(MP3_PATH):
        logger.error(f"MP3_PATH ('{MP3_PATH}') n'est pas configuré ou n'est pas un dossier.")
        return jsonify({"error": "Le répertoire des sons n'est pas configuré sur le serveur."}), 500

    # Sécurité : Valider que le nom de fichier ne contient pas de ".." pour éviter les traversées de répertoire
    # bien que send_from_directory offre une certaine protection.
    if ".." in filename or filename.startswith("/"): # Ou os.path.isabs(filename)
        logger.warning(f"Tentative d'accès invalide au fichier son (caractères interdits): {filename}")
        return jsonify({"error": "Nom de fichier invalide."}), 400

    try:
        # send_from_directory gère la construction du chemin et les erreurs de fichier non trouvé (404)
        # Il s'assure aussi que le client ne demande pas des fichiers en dehors du dossier spécifié.
        return send_from_directory(MP3_PATH, filename, as_attachment=False)
    except FileNotFoundError: # Bien que send_from_directory devrait gérer ça, une double vérif.
        logger.error(f"Fichier son '{filename}' non trouvé dans {MP3_PATH}.")
        return jsonify({"error": f"Fichier son '{filename}' introuvable."}), 404
    except Exception as e:
        logger.error(f"Erreur lors de la tentative de servir le fichier son '{filename}': {e}", exc_info=True)
        return jsonify({"error": "Erreur serveur lors de l'accès au fichier son."}), 500

@app.route('/api/config/weekly_schedule', methods=['GET'])
@login_required
def get_weekly_schedule_config():
    """
    Fournit le planning hebdomadaire actuel et la liste des journées types disponibles.
    """
    user_id = current_user.id
    logger.info(f"User '{user_id}' requête GET /api/config/weekly_schedule")

    try:
        donnees_path = os.path.join(CONFIG_PATH, DONNEES_SONNERIES_FILE)

        current_weekly_planning = {}
        available_day_types_names = []

        if os.path.exists(donnees_path):
            with open(donnees_path, 'r', encoding='utf-8') as f:
                donnees_sonneries = json.load(f)

            current_weekly_planning = donnees_sonneries.get("planning_hebdomadaire", {})
            if not isinstance(current_weekly_planning, dict):
                logger.warning(f"La section 'planning_hebdomadaire' dans {DONNEES_SONNERIES_FILE} n'est pas un dictionnaire. Renvoyer planning vide.")
                current_weekly_planning = {}

            day_types_data = donnees_sonneries.get("journees_types", {})
            if isinstance(day_types_data, dict):
                available_day_types_names = sorted(list(day_types_data.keys())) # Obtenir une liste triée des noms de JT
            else:
                logger.warning(f"La section 'journees_types' dans {DONNEES_SONNERIES_FILE} n'est pas un dictionnaire. Aucune journée type disponible.")

        else:
            logger.warning(f"Fichier {DONNEES_SONNERIES_FILE} introuvable lors de la lecture pour l'API weekly_schedule.")
            # Renvoyer des valeurs par défaut
            # Pour le planning hebdo, on peut utiliser JOURS_SEMAINE_ASSIGNATION de constants
            #from constants import JOURS_SEMAINE_ASSIGNATION, AUCUNE_SONNERIE # Importer si pas déjà global
            current_weekly_planning = {day: AUCUNE_SONNERIE for day in JOURS_SEMAINE_ASSIGNATION}


        config_data_to_return = {
            "weekly_planning": current_weekly_planning,
            "available_day_types": available_day_types_names
        }

        logger.debug(f"Config planning hebdo renvoyée: {config_data_to_return}")
        return jsonify(config_data_to_return), 200

    except json.JSONDecodeError as e_json:
        logger.error(f"Erreur JSON lecture config pour API /api/config/weekly_schedule: {e_json}", exc_info=True)
        return jsonify({"error": "Erreur de format dans le fichier de configuration des sonneries."}), 500
    except Exception as e:
        logger.error(f"Erreur API GET /api/config/weekly_schedule: {e}", exc_info=True)
        return jsonify({"error": f"Erreur serveur: {str(e)}"}), 500

@app.route('/api/config/weekly_schedule', methods=['POST'])
@login_required
def set_weekly_schedule_config():
    """
    Met à jour la section 'planning_hebdomadaire' dans donnees_sonneries.json.
    """
    user_id = current_user.id
    logger.info(f"User '{user_id}' requête POST /api/config/weekly_schedule")

    try:
        data_from_client = request.get_json()
        if not data_from_client:
            logger.warning("Requête POST /api/config/weekly_schedule sans données JSON.")
            return jsonify({"error": "Aucune donnée JSON reçue."}), 400

        new_planning = data_from_client.get("weekly_planning")
        if not isinstance(new_planning, dict):
            logger.warning(f"Données 'weekly_planning' invalides ou manquantes dans la requête: {new_planning}")
            return jsonify({"error": "Format de données pour 'weekly_planning' incorrect."}), 400

        # Valider que tous les jours attendus sont présents et que les JT assignées existent (ou sont "Aucune")
        # (Cette validation est optionnelle ici mais recommandée pour la robustesse)
        #from constants import JOURS_SEMAINE_ASSIGNATION, AUCUNE_SONNERIE # Importer si pas déjà global

        donnees_path = os.path.join(CONFIG_PATH, DONNEES_SONNERIES_FILE)

        # Charger l'intégralité du fichier donnees_sonneries.json
        if os.path.exists(donnees_path):
            with open(donnees_path, 'r', encoding='utf-8') as f:
                donnees_sonneries = json.load(f)
        else:
            # Si le fichier n'existe pas, c'est un problème plus grave.
            # Le backend n'aurait pas dû démarrer sans. On logue une erreur critique.
            logger.error(f"Fichier critique {DONNEES_SONNERIES_FILE} introuvable lors de la sauvegarde du planning hebdomadaire. Opération annulée.")
            return jsonify({"error": "Fichier de configuration principal manquant sur le serveur."}), 500

        # Vérifier que les journées types assignées existent (ou sont AUCUNE_SONNERIE)
        available_day_types = list(donnees_sonneries.get("journees_types", {}).keys())
        valid_planning = True
        for day in JOURS_SEMAINE_ASSIGNATION:
            if day not in new_planning:
                logger.warning(f"Jour '{day}' manquant dans le planning hebdomadaire envoyé par le client.")
                valid_planning = False; break
            assigned_jt = new_planning[day]
            if assigned_jt != AUCUNE_SONNERIE and assigned_jt not in available_day_types:
                logger.warning(f"Journée type '{assigned_jt}' assignée à '{day}' n'existe pas. Options valides: {available_day_types}")
                valid_planning = False; break

        if not valid_planning:
            return jsonify({"error": "Planning hebdomadaire invalide: jour manquant ou journée type assignée inexistante."}), 400

        # Mettre à jour uniquement la section 'planning_hebdomadaire'
        donnees_sonneries["planning_hebdomadaire"] = new_planning

        # Sauvegarder le fichier donnees_sonneries.json complet
        with open(donnees_path, 'w', encoding='utf-8') as f:
            json.dump(donnees_sonneries, f, indent=2, ensure_ascii=False)

        logger.info(f"Section 'planning_hebdomadaire' dans {DONNEES_SONNERIES_FILE} mise à jour avec succès.")

        # Le client JS devrait ensuite appeler /api/config/reload
        return jsonify({"message": "Planning hebdomadaire mis à jour avec succès."}), 200

    except json.JSONDecodeError as e_json: # Erreur si le fichier existant était corrompu
        logger.error(f"Erreur JSON lors de la lecture/sauvegarde de {DONNEES_SONNERIES_FILE} pour planning hebdo: {e_json}", exc_info=True)
        return jsonify({"error": "Erreur de format dans le fichier de configuration des sonneries."}), 500
    except Exception as e:
        logger.error(f"Erreur API POST /api/config/weekly_schedule: {e}", exc_info=True)
        return jsonify({"error": f"Erreur serveur: {str(e)}"}), 500


@app.route('/api/config/day_types', methods=['GET'])
@login_required
def get_day_type_list():
    """
    Retourne la liste des noms des journées types existantes.
    """
    user_id = current_user.id
    logger.info(f"User '{user_id}' requête GET /api/config/day_types (liste)")

    try:
        donnees_path = os.path.join(CONFIG_PATH, DONNEES_SONNERIES_FILE)
        available_day_types_names = []

        if os.path.exists(donnees_path):
            with open(donnees_path, 'r', encoding='utf-8') as f:
                donnees_sonneries = json.load(f)

            day_types_data = donnees_sonneries.get("journees_types", {})
            if isinstance(day_types_data, dict):
                available_day_types_names = sorted(list(day_types_data.keys()))
            else:
                logger.warning(f"La section 'journees_types' dans {DONNEES_SONNERIES_FILE} n'est pas un dict.")
        else:
             logger.warning(f"Fichier {DONNEES_SONNERIES_FILE} introuvable pour API day_types list.")
             # Renvoyer une liste vide si le fichier n'existe pas

        logger.debug(f"API List DayTypes renvoie: {available_day_types_names}")
        return jsonify({"day_types": available_day_types_names}), 200

    except json.JSONDecodeError as e_json:
        logger.error(f"Erreur JSON lecture {DONNEES_SONNERIES_FILE} pour API day_types list: {e_json}", exc_info=True)
        return jsonify({"error": "Erreur format fichier config sonneries."}), 500
    except Exception as e:
        logger.error(f"Erreur API GET /api/config/day_types (liste): {e}", exc_info=True)
        return jsonify({"error": f"Erreur serveur: {str(e)}"}), 500

@app.route('/api/config/day_types/<path:day_type_name>', methods=['GET'])
@login_required
def get_day_type_details(day_type_name):
    """
    Retourne les détails (nom, périodes) d'une journée type spécifique.
    """
    user_id = current_user.id
    logger.info(f"User '{user_id}' requête GET /api/config/day_types/{day_type_name} (détails)")

    # On pourrait vouloir décoder le nom si il contient des caractères spéciaux passés dans l'URL
    # import urllib.parse
    # decoded_name = urllib.parse.unquote(day_type_name)
    # Pour l'instant, on suppose des noms simples.

    try:
        donnees_path = os.path.join(CONFIG_PATH, DONNEES_SONNERIES_FILE)
        day_type_details = None

        if os.path.exists(donnees_path):
            with open(donnees_path, 'r', encoding='utf-8') as f:
                donnees_sonneries = json.load(f)

            day_types_data = donnees_sonneries.get("journees_types", {})
            if isinstance(day_types_data, dict):
                # Utiliser get() pour éviter KeyError si le nom n'existe pas
                day_type_details = day_types_data.get(day_type_name)
            else:
                 logger.warning(f"Section 'journees_types' invalide dans {DONNEES_SONNERIES_FILE}.")

        if day_type_details:
            # On pourrait trier les périodes par heure de début ici avant de renvoyer
            if 'periodes' in day_type_details and isinstance(day_type_details['periodes'], list):
                try:
                    day_type_details['periodes'].sort(key=lambda p: datetime.strptime(p.get("heure_debut", "99:99:99"), "%H:%M:%S").time())
                except ValueError:
                    logger.warning(f"Impossible de trier les périodes pour JT '{day_type_name}' (format heure invalide?).")

            logger.debug(f"Détails JT '{day_type_name}' renvoyés.")
            return jsonify(day_type_details), 200
        else:
            logger.warning(f"Journée type '{day_type_name}' non trouvée.")
            return jsonify({"error": f"Journée type '{day_type_name}' non trouvée."}), 404 # Not Found

    except json.JSONDecodeError as e_json:
        logger.error(f"Erreur JSON lecture {DONNEES_SONNERIES_FILE} pour détails JT '{day_type_name}': {e_json}", exc_info=True)
        return jsonify({"error": "Erreur format fichier config sonneries."}), 500
    except Exception as e:
        logger.error(f"Erreur API GET /api/config/day_types/{day_type_name}: {e}", exc_info=True)
        return jsonify({"error": f"Erreur serveur: {str(e)}"}), 500

@app.route('/api/config/day_types', methods=['POST'])
@login_required
def create_day_type():
    """
    Crée une nouvelle journée type (avec un nom, initialement sans périodes).
    """
    user_id = current_user.id

    data_from_client = request.get_json()
    if not data_from_client or 'name' not in data_from_client:
        logger.warning(f"User '{user_id}' - POST /api/config/day_types: Données invalides (nom manquant).")
        return jsonify({"error": "Le nom de la journée type est requis."}), 400

    new_day_type_name = data_from_client['name'].strip()
    logger.info(f"User '{user_id}' - POST /api/config/day_types: Tentative création JT '{new_day_type_name}'")

    if not new_day_type_name:
        logger.warning(f"User '{user_id}' - Tentative création JT avec nom vide.")
        return jsonify({"error": "Le nom de la journée type ne peut pas être vide."}), 400

    donnees_path = os.path.join(CONFIG_PATH, DONNEES_SONNERIES_FILE)

    try:
        donnees_sonneries = {}
        if os.path.exists(donnees_path):
            with open(donnees_path, 'r', encoding='utf-8') as f:
                donnees_sonneries = json.load(f)
        else:
            # Devrait être créé au démarrage du backend, mais par sécurité
            logger.warning(f"Fichier {DONNEES_SONNERIES_FILE} non trouvé, création d'une nouvelle structure.")
            donnees_sonneries = {"sonneries": {}, "journees_types": {}, "planning_hebdomadaire": {}, "exceptions_planning": {}, "vacances": {}}

        if "journees_types" not in donnees_sonneries or not isinstance(donnees_sonneries["journees_types"], dict):
            donnees_sonneries["journees_types"] = {} # Assurer que la clé existe et est un dict

        if new_day_type_name in donnees_sonneries["journees_types"]:
            logger.warning(f"User '{user_id}' - Tentative création JT existante: '{new_day_type_name}'")
            return jsonify({"error": f"La journée type '{new_day_type_name}' existe déjà."}), 409 # 409 Conflict

        # Ajouter la nouvelle journée type (avec une structure de base)
        donnees_sonneries["journees_types"][new_day_type_name] = {
            "nom": new_day_type_name,
            "periodes": [] # Initialement vide
        }

        # Sauvegarder le fichier complet
        with open(donnees_path, 'w', encoding='utf-8') as f:
            json.dump(donnees_sonneries, f, indent=2, ensure_ascii=False)

        logger.info(f"Journée type '{new_day_type_name}' créée avec succès par user '{user_id}'.")

        # Renvoyer la journée type créée (ou juste un message de succès)
        return jsonify({
            "message": f"Journée type '{new_day_type_name}' créée avec succès.",
            "day_type": donnees_sonneries["journees_types"][new_day_type_name]
        }), 201 # 201 Created

    except json.JSONDecodeError as e_json:
        logger.error(f"Erreur JSON lors de la création de JT '{new_day_type_name}': {e_json}", exc_info=True)
        return jsonify({"error": "Erreur de format dans le fichier de configuration."}), 500
    except Exception as e:
        logger.error(f"Erreur API POST /api/config/day_types (création JT '{new_day_type_name}'): {e}", exc_info=True)
        return jsonify({"error": f"Erreur serveur: {str(e)}"}), 500
@app.route('/api/config/day_types/<path:day_type_name>', methods=['DELETE'])
@login_required
def delete_day_type_entry(day_type_name): # Renommé pour éviter conflit avec get_day_type_details
    """
    Supprime une journée type existante.
    """
    user_id = current_user.id
    # Décoder le nom au cas où il contiendrait des caractères spéciaux encodés dans l'URL
    # import urllib.parse # S'assurer que c'est importé en haut du fichier si besoin
    # decoded_name = urllib.parse.unquote(day_type_name)
    # Pour l'instant, on utilise day_type_name directement.

    logger.info(f"User '{user_id}' - DELETE /api/config/day_types: Tentative suppression JT '{day_type_name}'")

    if not day_type_name: # Ne devrait pas arriver si l'URL est bien formée
        return jsonify({"error": "Nom de la journée type manquant dans la requête."}), 400

    donnees_path = os.path.join(CONFIG_PATH, DONNEES_SONNERIES_FILE)

    try:
        donnees_sonneries = {}
        if os.path.exists(donnees_path):
            with open(donnees_path, 'r', encoding='utf-8') as f:
                donnees_sonneries = json.load(f)
        else:
            logger.error(f"Fichier critique {DONNEES_SONNERIES_FILE} introuvable lors suppression JT '{day_type_name}'.")
            return jsonify({"error": "Fichier de configuration principal manquant."}), 500

        if "journees_types" not in donnees_sonneries or not isinstance(donnees_sonneries["journees_types"], dict):
            logger.warning(f"Section 'journees_types' non trouvée ou invalide lors suppression JT '{day_type_name}'.")
            return jsonify({"error": f"Journée type '{day_type_name}' non trouvée (section manquante)."}), 404

        if day_type_name not in donnees_sonneries["journees_types"]:
            logger.warning(f"User '{user_id}' - Tentative suppression JT inexistante: '{day_type_name}'")
            return jsonify({"error": f"La journée type '{day_type_name}' n'existe pas."}), 404 # Not Found

        # Supprimer la journée type
        del donnees_sonneries["journees_types"][day_type_name]

        # Optionnel mais recommandé : Nettoyer les références à cette JT dans planning_hebdomadaire et exceptions
        # 1. Nettoyer planning_hebdomadaire
        if "planning_hebdomadaire" in donnees_sonneries and isinstance(donnees_sonneries["planning_hebdomadaire"], dict):
            from constants import AUCUNE_SONNERIE # S'assurer de l'import global ou local
            for day, jt_assigned in donnees_sonneries["planning_hebdomadaire"].items():
                if jt_assigned == day_type_name:
                    donnees_sonneries["planning_hebdomadaire"][day] = AUCUNE_SONNERIE # Ou une valeur par défaut
                    logger.info(f"JT '{day_type_name}' retirée du planning pour le jour '{day}'.")

        # 2. Nettoyer exceptions_planning (plus complexe: soit supprimer l'exception, soit la mettre en silence)
        # Pour l'instant, on va juste logguer un avertissement si elle est utilisée dans les exceptions.
        # Une gestion plus fine pourrait être d'offrir à l'utilisateur le choix.
        if "exceptions_planning" in donnees_sonneries and isinstance(donnees_sonneries["exceptions_planning"], dict):
            exceptions_to_warn_about = []
            for date_exc, details_exc in donnees_sonneries["exceptions_planning"].items():
                if details_exc.get("action") == "utiliser_jt" and details_exc.get("journee_type") == day_type_name:
                    exceptions_to_warn_about.append(date_exc)
                    # On pourrait ici modifier l'exception:
                    # donnees_sonneries["exceptions_planning"][date_exc]["action"] = "silence"
                    # donnees_sonneries["exceptions_planning"][date_exc]["journee_type"] = None
                    # donnees_sonneries["exceptions_planning"][date_exc]["description"] = (details_exc.get("description","") + f" (JT '{day_type_name}' supprimée)").strip()
            if exceptions_to_warn_about:
                 logger.warning(f"La journée type '{day_type_name}' était utilisée dans les exceptions pour les dates: {', '.join(exceptions_to_warn_about)}. Ces exceptions devront être vérifiées/modifiées manuellement.")
                 # Si on modifiait les exceptions, ajouter un message de succès plus détaillé.


        # Sauvegarder le fichier complet
        with open(donnees_path, 'w', encoding='utf-8') as f:
            json.dump(donnees_sonneries, f, indent=2, ensure_ascii=False)

        logger.info(f"Journée type '{day_type_name}' supprimée avec succès par user '{user_id}'.")
        return jsonify({"message": f"Journée type '{day_type_name}' supprimée avec succès."}), 200 # Ou 204 No Content

    except json.JSONDecodeError as e_json:
        logger.error(f"Erreur JSON lors de la suppression de JT '{day_type_name}': {e_json}", exc_info=True)
        return jsonify({"error": "Erreur de format dans le fichier de configuration."}), 500
    except Exception as e:
        logger.error(f"Erreur API DELETE /api/config/day_types (suppression JT '{day_type_name}'): {e}", exc_info=True)
        return jsonify({"error": f"Erreur serveur: {str(e)}"}), 500

# ... (fin du code de delete_day_type_entry)

@app.route('/api/config/day_types/<path:day_type_name_url>', methods=['PUT'])
@login_required
def update_day_type_entry(day_type_name_url):
    """
    Met à jour une journée type existante:
    - Peut renommer la journée type (si 'new_name' est fourni).
    - Peut mettre à jour sa liste de périodes (si 'periods' est fourni).
    """
    user_id = current_user.id
    logger.info(f"User '{user_id}' - PUT /api/config/day_types/{day_type_name_url}")

    data_from_client = request.get_json()
    if not data_from_client:
        logger.warning(f"PUT /api/config/day_types/{day_type_name_url}: Données JSON manquantes.")
        return jsonify({"error": "Données JSON requises."}), 400

    # ----- Chargement de la configuration -----
    donnees_path = os.path.join(CONFIG_PATH, DONNEES_SONNERIES_FILE)
    donnees_sonneries = {}
    try:
        if os.path.exists(donnees_path):
            with open(donnees_path, 'r', encoding='utf-8') as f:
                donnees_sonneries = json.load(f)
        else:
            logger.error(f"Fichier critique {DONNEES_SONNERIES_FILE} introuvable (PUT day_type).")
            return jsonify({"error": "Fichier de configuration principal manquant."}), 500

        if "journees_types" not in donnees_sonneries or \
           not isinstance(donnees_sonneries["journees_types"], dict) or \
           day_type_name_url not in donnees_sonneries["journees_types"]:
            logger.warning(f"Journée type '{day_type_name_url}' non trouvée pour mise à jour.")
            return jsonify({"error": f"Journée type '{day_type_name_url}' non trouvée."}), 404
    except Exception as e:
        logger.error(f"Erreur chargement config pour PUT day_type '{day_type_name_url}': {e}", exc_info=True)
        return jsonify({"error": "Erreur lecture configuration."}), 500

    # ----- Logique de mise à jour -----
    current_day_type_key_in_file = day_type_name_url # Le nom actuel tel qu'il est dans le fichier et l'URL
    day_type_data_to_update = donnees_sonneries["journees_types"][current_day_type_key_in_file].copy()

    renamed = False
    new_name_for_key = current_day_type_key_in_file # Par défaut, la clé ne change pas

    # 1. Gestion du Renommage
    if 'new_name' in data_from_client:
        new_name_from_client = data_from_client['new_name'].strip()
        if new_name_from_client and new_name_from_client != current_day_type_key_in_file:
            if not new_name_from_client : # Redondant avec strip mais sécurité
                return jsonify({"error": "Le nouveau nom ne peut pas être vide."}), 400
            if new_name_from_client in donnees_sonneries["journees_types"]:
                logger.warning(f"Conflit: Tentative renommage JT '{current_day_type_key_in_file}' en nom existant '{new_name_from_client}'")
                return jsonify({"error": f"Une journée type nommée '{new_name_from_client}' existe déjà."}), 409

            logger.info(f"Renommage de '{current_day_type_key_in_file}' en '{new_name_from_client}'.")
            day_type_data_to_update["nom"] = new_name_from_client # Met à jour le champ "nom" interne
            new_name_for_key = new_name_from_client # La clé du dictionnaire va changer
            renamed = True

    # 2. Gestion de la mise à jour des Périodes
    periods_updated = False
    if "periods" in data_from_client:
        new_periods = data_from_client.get("periods")
        if not isinstance(new_periods, list):
            return jsonify({"error": "Le champ 'periods' doit être une liste."}), 400

        valid_periods = []
        for p_idx, p_data in enumerate(new_periods):
            if not isinstance(p_data, dict) or \
               not p_data.get("nom") or \
               not p_data.get("heure_debut") or \
               not p_data.get("heure_fin"):
                return jsonify({"error": f"Période invalide à l'index {p_idx}: nom, heure_debut, heure_fin requis."}), 400
            try:
                datetime.strptime(p_data["heure_debut"], "%H:%M:%S")
                datetime.strptime(p_data["heure_fin"], "%H:%M:%S")
            except ValueError:
                return jsonify({"error": f"Format d'heure invalide (HH:MM:SS) pour période à l'index {p_idx}."}), 400

            p_data["sonnerie_debut"] = p_data.get("sonnerie_debut")
            p_data["sonnerie_fin"] = p_data.get("sonnerie_fin")
            valid_periods.append(p_data)

        day_type_data_to_update["periodes"] = valid_periods
        periods_updated = True
        logger.info(f"Périodes mises à jour pour JT (clé: '{new_name_for_key}'). Nombre: {len(valid_periods)}")

    if not renamed and not periods_updated:
        return jsonify({"message": "Aucune modification fournie (ni nom, ni périodes)."}), 200

    # ----- Application des modifications et sauvegarde -----
    try:
        if renamed: # Si la clé a changé
            donnees_sonneries["journees_types"].pop(current_day_type_key_in_file) # Supprimer l'ancienne clé
        donnees_sonneries["journees_types"][new_name_for_key] = day_type_data_to_update # Ajouter/mettre à jour avec la nouvelle clé

        if renamed: # Mettre à jour les références SEULEMENT si le nom de la clé a changé
            if "planning_hebdomadaire" in donnees_sonneries:
                for day, jt_assigned in donnees_sonneries["planning_hebdomadaire"].items():
                    if jt_assigned == current_day_type_key_in_file:
                        donnees_sonneries["planning_hebdomadaire"][day] = new_name_for_key
            if "exceptions_planning" in donnees_sonneries:
                for date_exc, details_exc in donnees_sonneries["exceptions_planning"].items():
                    if details_exc.get("action") == "utiliser_jt" and details_exc.get("journee_type") == current_day_type_key_in_file:
                        donnees_sonneries["exceptions_planning"][date_exc]["journee_type"] = new_name_for_key

        with open(donnees_path, 'w', encoding='utf-8') as f:
            json.dump(donnees_sonneries, f, indent=2, ensure_ascii=False)

        msg = f"Journée type '{new_name_for_key}' mise à jour avec succès."
        if renamed and current_day_type_key_in_file != new_name_for_key:
            msg = f"Journée type '{current_day_type_key_in_file}' renommée en '{new_name_for_key}' et mise à jour."

        logger.info(msg)
        return jsonify({
            "message": msg,
            "updated_day_type_name": new_name_for_key, # Le nom final de la clé
            "day_type_data": day_type_data_to_update
        }), 200

    except Exception as e:
        logger.error(f"Erreur lors de la sauvegarde/mise à jour de JT '{day_type_name_url}': {e}", exc_info=True)
        return jsonify({"error": f"Erreur serveur lors de la mise à jour: {str(e)}"}), 500

# --- FIN DE LA FONCTION update_day_type_entry ---

@app.route('/api/config/exceptions', methods=['GET'])
@login_required
def get_all_exceptions():
    """
    Retourne toutes les exceptions de planning configurées.
    """
    user_id = current_user.id
    logger.info(f"User '{user_id}' requête GET /api/config/exceptions")

    try:
        donnees_path = os.path.join(CONFIG_PATH, DONNEES_SONNERIES_FILE)
        exceptions_planning = {}

        if os.path.exists(donnees_path):
            with open(donnees_path, 'r', encoding='utf-8') as f:
                donnees_sonneries = json.load(f)
            exceptions_planning = donnees_sonneries.get("exceptions_planning", {})
            if not isinstance(exceptions_planning, dict):
                logger.warning(f"Section 'exceptions_planning' invalide dans {DONNEES_SONNERIES_FILE}, retour liste vide.")
                exceptions_planning = {}
        else:
            logger.warning(f"Fichier {DONNEES_SONNERIES_FILE} non trouvé pour API exceptions list.")
            # Renvoyer un objet vide si le fichier n'existe pas

        # Renvoyer les exceptions. Le client pourra les trier par date s'il le souhaite.
        # On pourrait aussi renvoyer la liste des noms de JT disponibles pour les selects côté client
        # mais la page aura peut-être déjà cette info ou la récupérera séparément.
        # Pour l'instant, on ne renvoie que les exceptions.
        logger.debug(f"API List Exceptions renvoie: {exceptions_planning}")
        return jsonify({"exceptions_planning": exceptions_planning}), 200

    except json.JSONDecodeError as e_json:
        logger.error(f"Erreur JSON lecture {DONNEES_SONNERIES_FILE} pour API exceptions: {e_json}", exc_info=True)
        return jsonify({"error": "Erreur format fichier config sonneries."}), 500
    except Exception as e:
        logger.error(f"Erreur API GET /api/config/exceptions: {e}", exc_info=True)
        return jsonify({"error": f"Erreur serveur: {str(e)}"}), 500

@app.route('/api/config/exceptions/<string:date_str>', methods=['PUT']) # date_str est une string YYYY-MM-DD
@login_required
def update_exception(date_str):
    """
    Modifie une exception existante pour une date donnée.
    Attend un JSON avec: {"action": "silence"|"utiliser_jt",
                          "journee_type": "NomJT" (si action=utiliser_jt), "description": "..."}
    """
    user_id = current_user.id
    logger.info(f"User '{user_id}' - PUT /api/config/exceptions/{date_str}: Tentative de modification.")

    data_from_client = request.get_json()
    if not data_from_client:
        return jsonify({"error": "Données JSON requises."}), 400

    action = data_from_client.get('action')
    description = data_from_client.get('description', "")
    journee_type_name = data_from_client.get('journee_type') if action == "utiliser_jt" else None

    # Valider la date_str passée dans l'URL
    try:
        date.fromisoformat(date_str)
    except ValueError:
        return jsonify({"error": "Format de date invalide dans l'URL. Utilisez YYYY-MM-DD."}), 400

    if not action: # L'action est requise pour une modification
        return jsonify({"error": "Le champ 'action' est requis pour la modification."}), 400
    if action not in ["silence", "utiliser_jt"]:
        return jsonify({"error": "Action invalide. Doit être 'silence' ou 'utiliser_jt'."}), 400
    if action == "utiliser_jt" and not journee_type_name:
        return jsonify({"error": "Une journée type doit être spécifiée pour l'action 'utiliser_jt'."}), 400

    donnees_path = os.path.join(CONFIG_PATH, DONNEES_SONNERIES_FILE)
    try:
        donnees_sonneries = {}
        if os.path.exists(donnees_path):
            with open(donnees_path, 'r', encoding='utf-8') as f:
                donnees_sonneries = json.load(f)
        else:
            logger.error(f"Fichier {DONNEES_SONNERIES_FILE} non trouvé lors modif. exception pour '{date_str}'.")
            return jsonify({"error": "Fichier de configuration principal manquant."}), 500

        exceptions_planning = donnees_sonneries.get("exceptions_planning", {})
        if date_str not in exceptions_planning:
            logger.warning(f"User '{user_id}' - Tentative modif. exception inexistante pour date '{date_str}'.")
            return jsonify({"error": f"Aucune exception trouvée pour la date '{date_str}' à modifier."}), 404

        # Vérifier si la journée type spécifiée existe (si action = utiliser_jt)
        if action == "utiliser_jt":
            if "journees_types" not in donnees_sonneries or journee_type_name not in donnees_sonneries.get("journees_types", {}):
                logger.warning(f"User '{user_id}' - Tentative modif. exception avec JT inexistante: '{journee_type_name}' pour '{date_str}'")
                return jsonify({"error": f"La journée type '{journee_type_name}' n'existe pas."}), 400

        updated_exception = {
            "action": action,
            "description": description
        }
        if action == "utiliser_jt":
            updated_exception["journee_type"] = journee_type_name
        else: # Si c'est silence, s'assurer qu'il n'y a pas de clé journee_type
            if "journee_type" in exceptions_planning[date_str]: # Si elle existait avant
                updated_exception["journee_type"] = None # Ou la supprimer complètement

        exceptions_planning[date_str] = updated_exception
        donnees_sonneries["exceptions_planning"] = exceptions_planning # Réassigner au cas où c'était None avant

        with open(donnees_path, 'w', encoding='utf-8') as f:
            json.dump(donnees_sonneries, f, indent=2, ensure_ascii=False)

        logger.info(f"Exception pour date '{date_str}' modifiée avec succès par user '{user_id}'.")
        return jsonify({
            "message": f"Exception pour la date '{date_str}' modifiée avec succès.",
            "date": date_str,
            "exception_details": updated_exception
        }), 200

    except Exception as e:
        logger.error(f"Erreur API PUT /api/config/exceptions/{date_str} (modif): {e}", exc_info=True)
        return jsonify({"error": f"Erreur serveur: {str(e)}"}), 500

@app.route('/api/config/exceptions/<string:date_str>', methods=['DELETE'])
@login_required
def delete_exception(date_str):
    """
    Supprime une exception de planning pour une date donnée.
    """
    user_id = current_user.id
    logger.info(f"User '{user_id}' - DELETE /api/config/exceptions/{date_str}: Tentative de suppression.")

    # Valider la date_str passée dans l'URL
    try:
        date.fromisoformat(date_str)
    except ValueError:
        return jsonify({"error": "Format de date invalide dans l'URL. Utilisez YYYY-MM-DD."}), 400

    donnees_path = os.path.join(CONFIG_PATH, DONNEES_SONNERIES_FILE)
    try:
        donnees_sonneries = {}
        if os.path.exists(donnees_path):
            with open(donnees_path, 'r', encoding='utf-8') as f:
                donnees_sonneries = json.load(f)
        else:
            logger.error(f"Fichier {DONNEES_SONNERIES_FILE} non trouvé lors suppression exception pour '{date_str}'.")
            return jsonify({"error": "Fichier de configuration principal manquant."}), 500

        exceptions_planning = donnees_sonneries.get("exceptions_planning", {})
        if date_str not in exceptions_planning:
            logger.warning(f"User '{user_id}' - Tentative suppression exception inexistante pour date '{date_str}'.")
            return jsonify({"error": f"Aucune exception trouvée pour la date '{date_str}'."}), 404

        del exceptions_planning[date_str]
        donnees_sonneries["exceptions_planning"] = exceptions_planning

        with open(donnees_path, 'w', encoding='utf-8') as f:
            json.dump(donnees_sonneries, f, indent=2, ensure_ascii=False)

        logger.info(f"Exception pour date '{date_str}' supprimée avec succès par user '{user_id}'.")
        return jsonify({"message": f"Exception pour la date '{date_str}' supprimée avec succès."}), 200 # ou 204

    except Exception as e:
        logger.error(f"Erreur API DELETE /api/config/exceptions/{date_str}: {e}", exc_info=True)
        return jsonify({"error": f"Erreur serveur: {str(e)}"}), 500

@app.route('/api/config/exceptions', methods=['POST'])
@login_required
def add_exception():
    """
    Ajoute une nouvelle exception de planning.
    Attend un JSON avec: {"date": "YYYY-MM-DD", "action": "silence"|"utiliser_jt",
                          "journee_type": "NomJT" (si action=utiliser_jt), "description": "..."}
    """
    user_id = current_user.id

    data_from_client = request.get_json()
    if not data_from_client:
        return jsonify({"error": "Données JSON requises."}), 400

    date_str = data_from_client.get('date')
    action = data_from_client.get('action')
    description = data_from_client.get('description', "") # Description optionnelle
    journee_type_name = data_from_client.get('journee_type') if action == "utiliser_jt" else None

    logger.info(f"User '{user_id}' - POST /api/config/exceptions: Ajout exception pour date '{date_str}', action '{action}'")

    if not date_str or not action:
        return jsonify({"error": "Les champs 'date' et 'action' sont requis."}), 400

    try: # Valider le format de la date
        date_obj = date.fromisoformat(date_str)
    except ValueError:
        return jsonify({"error": "Format de date invalide. Utilisez YYYY-MM-DD."}), 400

    if action not in ["silence", "utiliser_jt"]:
        return jsonify({"error": "Action invalide. Doit être 'silence' ou 'utiliser_jt'."}), 400

    if action == "utiliser_jt" and not journee_type_name:
        return jsonify({"error": "Une journée type doit être spécifiée pour l'action 'utiliser_jt'."}), 400

    donnees_path = os.path.join(CONFIG_PATH, DONNEES_SONNERIES_FILE)
    try:
        donnees_sonneries = {}
        if os.path.exists(donnees_path):
            with open(donnees_path, 'r', encoding='utf-8') as f:
                donnees_sonneries = json.load(f)
        else: # Ne devrait pas arriver
            logger.error(f"Fichier {DONNEES_SONNERIES_FILE} non trouvé lors de l'ajout d'exception.")
            return jsonify({"error": "Fichier de configuration principal manquant."}), 500

        if "exceptions_planning" not in donnees_sonneries or not isinstance(donnees_sonneries["exceptions_planning"], dict):
            donnees_sonneries["exceptions_planning"] = {}

        # Vérifier si la journée type spécifiée existe (si action = utiliser_jt)
        if action == "utiliser_jt":
            if "journees_types" not in donnees_sonneries or journee_type_name not in donnees_sonneries.get("journees_types", {}):
                logger.warning(f"User '{user_id}' - Tentative d'ajout d'exception avec JT inexistante: '{journee_type_name}' pour date '{date_str}'")
                return jsonify({"error": f"La journée type '{journee_type_name}' n'existe pas."}), 400 # Bad request

        if date_str in donnees_sonneries["exceptions_planning"]:
            logger.warning(f"User '{user_id}' - Tentative d'ajout d'exception pour une date déjà existante: '{date_str}'. Utilisez PUT pour modifier.")
            return jsonify({"error": f"Une exception existe déjà pour la date '{date_str}'. Modifiez-la ou supprimez-la d'abord."}), 409 # Conflict

        new_exception = {
            "action": action,
            "description": description
        }
        if action == "utiliser_jt":
            new_exception["journee_type"] = journee_type_name

        donnees_sonneries["exceptions_planning"][date_str] = new_exception

        with open(donnees_path, 'w', encoding='utf-8') as f:
            json.dump(donnees_sonneries, f, indent=2, ensure_ascii=False)

        logger.info(f"Exception pour date '{date_str}' ajoutée avec succès par user '{user_id}'.")
        return jsonify({
            "message": f"Exception pour la date '{date_str}' ajoutée avec succès.",
            "date": date_str,
            "exception_details": new_exception
        }), 201 # Created

    except Exception as e:
        logger.error(f"Erreur API POST /api/config/exceptions (ajout): {e}", exc_info=True)
        return jsonify({"error": f"Erreur serveur: {str(e)}"}), 500

@app.route('/api/config/sounds/scan', methods=['POST'])
@login_required
def scan_mp3_folder_and_update_config():
    """
    Scanne le dossier MP3_PATH, compare avec les sonneries configurées
    et ajoute les nouveaux fichiers MP3 à la section 'sonneries'
    de donnees_sonneries.json avec un nom convivial par défaut.
    """
    user_id = current_user.id
    logger.info(f"User '{user_id}' - POST /api/config/sounds/scan: Demande de scan du dossier MP3.")

    if not MP3_PATH or not os.path.isdir(MP3_PATH):
        logger.error(f"Scan MP3 échoué: MP3_PATH ('{MP3_PATH}') non configuré ou inaccessible.")
        return jsonify({"error": "Répertoire MP3 non configuré ou inaccessible sur le serveur."}), 500

    donnees_path = os.path.join(CONFIG_PATH, DONNEES_SONNERIES_FILE)

    try:
        donnees_sonneries = {}
        if os.path.exists(donnees_path):
            with open(donnees_path, 'r', encoding='utf-8') as f:
                donnees_sonneries = json.load(f)
        else:
            logger.warning(f"Fichier {DONNEES_SONNERIES_FILE} non trouvé, création nouvelle structure pour scan.")
            donnees_sonneries = {"sonneries": {}, "journees_types": {}, "planning_hebdomadaire": {}, "exceptions_planning": {}, "vacances": {}}

        if "sonneries" not in donnees_sonneries or not isinstance(donnees_sonneries["sonneries"], dict):
            donnees_sonneries["sonneries"] = {}

        current_configured_files = set(donnees_sonneries["sonneries"].values())
        current_display_names = set(donnees_sonneries["sonneries"].keys())

        files_on_disk = []
        # Utiliser glob pour trouver les .mp3 de manière insensible à la casse si possible (plus complexe)
        # Pour l'instant, sensible à la casse pour .mp3
        for f_name in os.listdir(MP3_PATH):
            if f_name.lower().endswith('.mp3') and os.path.isfile(os.path.join(MP3_PATH, f_name)):
                 files_on_disk.append(f_name)

        logger.info(f"Scan MP3: {len(files_on_disk)} fichiers .mp3 trouvés dans {MP3_PATH}.")
        logger.debug(f"Scan MP3: Fichiers disques: {files_on_disk}")
        logger.debug(f"Scan MP3: Fichiers configurés: {current_configured_files}")

        added_count = 0
        updated_sonneries = donnees_sonneries["sonneries"].copy() # Travailler sur une copie

        for mp3_file in files_on_disk:
            if mp3_file not in current_configured_files:
                # Nouveau fichier trouvé, générer un nom convivial unique
                base_name = os.path.splitext(mp3_file)[0]
                display_name = base_name
                counter = 1
                while display_name in current_display_names or display_name in updated_sonneries: # Vérifier aussi dans les ajouts en cours
                    display_name = f"{base_name}_{counter}"
                    counter += 1

                updated_sonneries[display_name] = mp3_file
                current_display_names.add(display_name) # Ajouter au set pour les prochains tests d'unicité
                added_count += 1
                logger.info(f"Scan MP3: Nouveau fichier '{mp3_file}' ajouté à la config avec nom convivial '{display_name}'.")

        if added_count > 0:
            donnees_sonneries["sonneries"] = updated_sonneries
            with open(donnees_path, 'w', encoding='utf-8') as f:
                json.dump(donnees_sonneries, f, indent=2, ensure_ascii=False)
            message = f"{added_count} nouvelle(s) sonnerie(s) ajoutée(s) à la configuration."
            logger.info(message)
        else:
            message = "Aucune nouvelle sonnerie trouvée dans le dossier MP3."
            logger.info(message)

        return jsonify({
            "message": message,
            "added_count": added_count,
            "total_configured": len(updated_sonneries),
            "configured_sounds": updated_sonneries # Renvoyer la liste mise à jour
        }), 200

    except Exception as e:
        logger.error(f"Erreur API POST /api/config/sounds/scan: {e}", exc_info=True)
        return jsonify({"error": f"Erreur serveur lors du scan des MP3: {str(e)}"}), 500

@app.route('/api/config/sounds/display_name/<path:file_name>', methods=['PUT'])
@login_required
def update_sound_display_name(file_name):
    """
    Modifie le nom convivial d'une sonnerie existante.
    Le nom de fichier MP3 (file_name dans l'URL) est la clé pour retrouver la sonnerie.
    Attend un JSON avec {"new_display_name": "Nouveau Nom Convivial"}
    """
    user_id = current_user.id

    data_from_client = request.get_json()
    if not data_from_client or 'new_display_name' not in data_from_client:
        logger.warning(f"User '{user_id}' - PUT /api/config/sounds/display_name/{file_name}: 'new_display_name' manquant.")
        return jsonify({"error": "Le nouveau nom convivial est requis."}), 400

    new_display_name = data_from_client['new_display_name'].strip()
    logger.info(f"User '{user_id}' - Tentative de modification du nom convivial pour '{file_name}' en '{new_display_name}'")

    if not new_display_name:
        return jsonify({"error": "Le nouveau nom convivial ne peut pas être vide."}), 400

    donnees_path = os.path.join(CONFIG_PATH, DONNEES_SONNERIES_FILE)
    try:
        donnees_sonneries = {}
        if os.path.exists(donnees_path):
            with open(donnees_path, 'r', encoding='utf-8') as f:
                donnees_sonneries = json.load(f)
        else:
            logger.error(f"Fichier {DONNEES_SONNERIES_FILE} non trouvé (update_sound_display_name).")
            return jsonify({"error": "Fichier de configuration principal manquant."}), 500

        if "sonneries" not in donnees_sonneries or not isinstance(donnees_sonneries["sonneries"], dict):
            donnees_sonneries["sonneries"] = {} # Init si manquant, mais devrait pas arriver

        # Trouver l'ancien nom convivial basé sur le file_name
        old_display_name = None
        for disp_name, f_name in donnees_sonneries["sonneries"].items():
            if f_name == file_name:
                old_display_name = disp_name
                break

        if old_display_name is None:
            logger.warning(f"Fichier son '{file_name}' non trouvé dans la configuration pour modification de nom convivial.")
            return jsonify({"error": f"Le fichier son '{file_name}' n'est pas dans la configuration."}), 404

        if old_display_name == new_display_name:
            return jsonify({"message": "Aucun changement, les noms conviviaux sont identiques."}), 200

        # Vérifier si le nouveau nom convivial est déjà utilisé (par un AUTRE fichier)
        if new_display_name in donnees_sonneries["sonneries"] and donnees_sonneries["sonneries"][new_display_name] != file_name :
            logger.warning(f"Conflit: Nom convivial '{new_display_name}' déjà utilisé pour un autre fichier.")
            return jsonify({"error": f"Le nom convivial '{new_display_name}' est déjà utilisé."}), 409

        # Mettre à jour: supprimer l'ancienne clé (ancien nom convivial) et ajouter la nouvelle
        # avec le même nom de fichier.
        updated_sonneries = donnees_sonneries["sonneries"].copy()
        if old_display_name in updated_sonneries: # Devrait toujours être vrai
            del updated_sonneries[old_display_name]
        updated_sonneries[new_display_name] = file_name

        donnees_sonneries["sonneries"] = updated_sonneries

        with open(donnees_path, 'w', encoding='utf-8') as f:
            json.dump(donnees_sonneries, f, indent=2, ensure_ascii=False)

        logger.info(f"Nom convivial pour '{file_name}' mis à jour de '{old_display_name}' à '{new_display_name}'.")
        return jsonify({
            "message": "Nom convivial mis à jour avec succès.",
            "file_name": file_name,
            "new_display_name": new_display_name,
            "old_display_name": old_display_name
        }), 200

    except Exception as e:
        logger.error(f"Erreur API PUT /api/config/sounds/display_name/{file_name}: {e}", exc_info=True)
        return jsonify({"error": f"Erreur serveur: {str(e)}"}), 500

@app.route('/api/config/sounds/<path:file_name>', methods=['DELETE'])
@login_required
def delete_sound_association(file_name):
    """
    Supprime l'association nom convivial <-> nom de fichier pour un fichier MP3 donné.
    Ne supprime PAS le fichier MP3 du disque.
    Le nom de fichier MP3 (file_name dans l'URL) est la clé pour retrouver la sonnerie.
    """
    user_id = current_user.id
    logger.info(f"User '{user_id}' - DELETE /api/config/sounds/{file_name}: Tentative de suppression d'association.")

    donnees_path = os.path.join(CONFIG_PATH, DONNEES_SONNERIES_FILE)
    try:
        donnees_sonneries = {}
        if os.path.exists(donnees_path):
            with open(donnees_path, 'r', encoding='utf-8') as f:
                donnees_sonneries = json.load(f)
        else:
            logger.error(f"Fichier {DONNEES_SONNERIES_FILE} non trouvé (delete_sound_association).")
            return jsonify({"error": "Fichier de configuration principal manquant."}), 500

        if "sonneries" not in donnees_sonneries or not isinstance(donnees_sonneries["sonneries"], dict):
            return jsonify({"error": f"Aucune sonnerie configurée (fichier '{file_name}' non trouvé)."}), 404

        display_name_to_delete = None
        for disp_name, f_name in donnees_sonneries["sonneries"].items():
            if f_name == file_name:
                display_name_to_delete = disp_name
                break

        if display_name_to_delete is None:
            logger.warning(f"Fichier son '{file_name}' non trouvé dans la configuration pour suppression d'association.")
            return jsonify({"error": f"Le fichier son '{file_name}' n'est pas dans la configuration."}), 404

        # Supprimer l'entrée
        del donnees_sonneries["sonneries"][display_name_to_delete]

        # Important: Nettoyer les références à ce fichier son dans les journées types et les paramètres d'alerte
        # 1. Journées Types
        if "journees_types" in donnees_sonneries:
            for jt_name, jt_data in donnees_sonneries["journees_types"].items():
                if "periodes" in jt_data:
                    for periode in jt_data["periodes"]:
                        if periode.get("sonnerie_debut") == file_name:
                            periode["sonnerie_debut"] = None
                            logger.info(f"Nettoyage: Sonnerie début '{file_name}' retirée de JT '{jt_name}', période '{periode.get('nom')}'.")
                        if periode.get("sonnerie_fin") == file_name:
                            periode["sonnerie_fin"] = None
                            logger.info(f"Nettoyage: Sonnerie fin '{file_name}' retirée de JT '{jt_name}', période '{periode.get('nom')}'.")

        # 2. Paramètres d'alerte (dans parametres_college.json)
        params_path = os.path.join(CONFIG_PATH, PARAMS_FILE)
        params_modified = False
        if os.path.exists(params_path):
            with open(params_path, 'r', encoding='utf-8') as f_params:
                college_params = json.load(f_params)

            alert_keys_to_check = ["sonnerie_ppms", "sonnerie_attentat", "sonnerie_fin_alerte"]
            for key in alert_keys_to_check:
                if college_params.get(key) == file_name:
                    college_params[key] = None
                    params_modified = True
                    logger.info(f"Nettoyage: Sonnerie alerte '{key}' utilisant '{file_name}' mise à None.")

            if params_modified:
                with open(params_path, 'w', encoding='utf-8') as f_params:
                    json.dump(college_params, f_params, indent=2, ensure_ascii=False)
                logger.info(f"Fichier {PARAMS_FILE} mis à jour après nettoyage des références à '{file_name}'.")

        # Sauvegarder donnees_sonneries.json
        with open(donnees_path, 'w', encoding='utf-8') as f:
            json.dump(donnees_sonneries, f, indent=2, ensure_ascii=False)

        logger.info(f"Association pour le fichier son '{file_name}' (nom convivial '{display_name_to_delete}') supprimée.")
        return jsonify({"message": f"Sonnerie '{display_name_to_delete}' (fichier: {file_name}) retirée de la configuration."}), 200

    except Exception as e:
        logger.error(f"Erreur API DELETE /api/config/sounds/{file_name}: {e}", exc_info=True)
        return jsonify({"error": f"Erreur serveur: {str(e)}"}), 500

# Route pour uploader un nouveau fichier son
@app.route('/api/config/sounds/upload', methods=['POST'])
@login_required
def upload_sound_file():
    """
    Reçoit un fichier MP3 uploadé, le sauvegarde dans MP3_PATH,
    et l'ajoute à la configuration des sonneries.
    """
    user_id = current_user.id
    logger.info(f"User '{user_id}' - POST /api/config/sounds/upload: Tentative d'upload de fichier son.")

    if not MP3_PATH or not os.path.isdir(MP3_PATH):
        logger.error(f"Upload échoué: MP3_PATH ('{MP3_PATH}') non configuré ou inaccessible.")
        return jsonify({"error": "Répertoire de destination des MP3 non configuré sur le serveur."}), 500

    if 'soundfile' not in request.files:
        logger.warning("Upload: Aucune partie 'soundfile' dans la requête.")
        return jsonify({"error": "Aucun fichier sélectionné pour l'upload."}), 400

    file = request.files['soundfile']

    if file.filename == '':
        logger.warning("Upload: Nom de fichier vide soumis.")
        return jsonify({"error": "Aucun fichier sélectionné (nom de fichier vide)."}), 400

    # Valider l'extension et le type MIME (basique)
    allowed_extensions = {'mp3'}
    filename = secure_filename(file.filename) # Sécuriser le nom du fichier

    if '.' not in filename or filename.rsplit('.', 1)[1].lower() not in allowed_extensions:
        logger.warning(f"Upload: Type de fichier non autorisé pour '{filename}'.")
        return jsonify({"error": "Type de fichier non autorisé. Seuls les .mp3 sont acceptés."}), 400

    # Optionnel: Vérifier le type MIME si possible (plus robuste)
    # if file.mimetype != 'audio/mpeg':
    #     logger.warning(f"Upload: Type MIME incorrect '{file.mimetype}' pour '{filename}'.")
    #     return jsonify({"error": "Contenu du fichier incorrect (doit être audio/mpeg)."}), 400

    # Vérifier si un fichier avec le même nom existe déjà pour éviter l'écrasement
    # ou gérer le renommage. Pour l'instant, on refuse si le nom existe.
    destination_path = os.path.join(MP3_PATH, filename)
    if os.path.exists(destination_path):
        logger.warning(f"Upload: Fichier '{filename}' existe déjà à destination.")
        return jsonify({"error": f"Un fichier nommé '{filename}' existe déjà sur le serveur. Veuillez renommer votre fichier ou supprimer l'existant."}), 409 # Conflict

    try:
        # Sauvegarder le fichier uploadé
        file.save(destination_path)
        logger.info(f"Upload: Fichier '{filename}' sauvegardé avec succès dans '{MP3_PATH}'.")

        # Mettre à jour donnees_sonneries.json
        donnees_path = os.path.join(CONFIG_PATH, DONNEES_SONNERIES_FILE)
        donnees_sonneries = {}
        if os.path.exists(donnees_path):
            with open(donnees_path, 'r', encoding='utf-8') as f:
                donnees_sonneries = json.load(f)
        else:
            donnees_sonneries = {"sonneries": {}, "journees_types": {}, "planning_hebdomadaire": {}, "exceptions_planning": {}, "vacances": {}}

        if "sonneries" not in donnees_sonneries or not isinstance(donnees_sonneries["sonneries"], dict):
            donnees_sonneries["sonneries"] = {}

        # Générer un nom convivial unique pour ce nouveau fichier
        base_name = os.path.splitext(filename)[0]
        display_name = base_name
        counter = 1
        current_display_names = set(donnees_sonneries["sonneries"].keys())
        while display_name in current_display_names:
            display_name = f"{base_name}_{counter}"
            counter += 1

        donnees_sonneries["sonneries"][display_name] = filename

        with open(donnees_path, 'w', encoding='utf-8') as f:
            json.dump(donnees_sonneries, f, indent=2, ensure_ascii=False)

        logger.info(f"Upload: Fichier '{filename}' ajouté à la config avec nom convivial '{display_name}'.")

        return jsonify({
            "message": f"Sonnerie '{filename}' uploadée et ajoutée avec succès sous le nom '{display_name}'.",
            "fileName": filename,
            "displayName": display_name,
            "configured_sounds": donnees_sonneries["sonneries"] # Renvoyer la liste mise à jour
        }), 201 # Created

    except Exception as e:
        logger.error(f"Erreur lors de l'upload ou de la mise à jour config pour '{filename}': {e}", exc_info=True)
        # Essayer de supprimer le fichier partiellement uploadé en cas d'erreur après la sauvegarde
        if os.path.exists(destination_path):
            try:
                os.remove(destination_path)
                logger.info(f"Nettoyage: Fichier '{destination_path}' supprimé suite à une erreur post-upload.")
            except Exception as e_remove:
                logger.error(f"Erreur lors de la suppression du fichier '{destination_path}' après erreur: {e_remove}")
        return jsonify({"error": f"Erreur serveur lors de l'upload: {str(e)}"}), 500

        # --- FIN NOUVELLES ROUTES POUR LA CONFIGURATION WEB ---

# ==============================================================================
# Point d'Entrée et Lancement du Serveur / Gestionnaire de Son
# ==============================================================================

def run_sound_cli():
    import os
    # SDL_AUDIODRIVER n'est PAS défini ici pour ce test.

    import pygame
    import time
    import argparse
    import traceback
    import sys # Nécessaire pour sys.argv et sys.exit

    # --- Argument Parsing ---
    parser = argparse.ArgumentParser(description="CLI for playing sounds with Pygame")
    parser.add_argument("sound_file", help="Path to the sound file")
    parser.add_argument("--loop", action='store_true', help="Loop the sound continuously")
    parser.add_argument("--device", help="Name of the audio output device for Pygame")

    # Log initial pour voir les arguments bruts passés au script
    raw_args_for_log = sys.argv[2:] # sys.argv[0] is script name, sys.argv[1] is '--play-sound'
    # print(f"[SoundCLI] Raw CLI arguments: {raw_args_for_log}") # Optionnel, pour débogage avancé

    cli_args = parser.parse_args(raw_args_for_log)

    sound_path = cli_args.sound_file
    loop_flag = cli_args.loop
    loops = -1 if loop_flag else 0
    device_name_arg = cli_args.device

    # Log initial amélioré
    print(f"[SoundCLI] PID:{os.getpid()} Play:'{os.path.basename(sound_path)}' Loop:{loop_flag} Device Requested:'{device_name_arg if device_name_arg else 'Default'}'")

    if not os.path.isfile(sound_path):
        print(f"[SoundCLI] ERR: Sound file not found: {sound_path}")
        sys.exit(1)

    pg_ok = False
    mix_ok = False # Sera mis à True seulement si pygame.mixer.init() réussit
    sound = None

    # --- pre_init ---
    try:
        if device_name_arg:
            print(f"[SoundCLI] Attempting to pre-initialize mixer with device: '{device_name_arg}'")
            pygame.mixer.pre_init(devicename=device_name_arg, frequency=44100, size=-16, channels=2, buffer=2048)
            print(f"[SoundCLI] Mixer pre_init called for device: '{device_name_arg}'")
        else:
            print("[SoundCLI] No specific audio device for pre_init. Calling pre_init with default audio parameters.")
            pygame.mixer.pre_init(frequency=44100, size=-16, channels=2, buffer=2048)
            print("[SoundCLI] Mixer pre_init called with default audio parameters.")
    except pygame.error as pre_init_err:
        # Si pre_init échoue (par exemple, format audio non supporté par le device même en pre_init)
        # Ce n'est pas toujours fatal pour init() qui pourrait avoir d'autres mécanismes.
        print(f"[SoundCLI] WARNING Pygame during pre_init: {pre_init_err}. Will proceed to full init.")


    # --- Main Initialization Block ---
    try:
        print("[SoundCLI] Init Pygame module..."); pygame.init(); pg_ok = True

        # Tentative d'initialisation du mixer
        if device_name_arg:
            print(f"[SoundCLI] Attempting to initialize mixer with device: '{device_name_arg}'")
            try:
                pygame.mixer.init(devicename=device_name_arg, frequency=44100, size=-16, channels=2, buffer=2048)
                mix_ok = True
                print(f"[SoundCLI] Mixer initialized successfully with device: '{device_name_arg}'")
            except pygame.error as pg_err_device:
                print(f"[SoundCLI] ERR Pygame: Failed to initialize mixer with device '{device_name_arg}'. Error: {pg_err_device}")
                print("[SoundCLI] Attempting to initialize mixer with default device as fallback...")
                try:
                    pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=2048)
                    mix_ok = True
                    print("[SoundCLI] Mixer initialized successfully with default device (fallback).")
                except pygame.error as pg_err_default_fallback:
                    print(f"[SoundCLI] ERR Pygame: Failed to initialize mixer with default device (fallback). Error: {pg_err_default_fallback}")
                    # Laisser l'erreur se propager au bloc except extérieur principal pour ce script.
                    raise pg_err_default_fallback # Relancer pour être attrapé par le bloc pg_err général
        else:
            print("[SoundCLI] No specific audio device requested. Initializing mixer with default device...")
            try:
                pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=2048)
                mix_ok = True
                print("[SoundCLI] Mixer initialized successfully with default device.")
            except pygame.error as pg_err_default:
                print(f"[SoundCLI] ERR Pygame: Failed to initialize mixer with default device. Error: {pg_err_default}")
                raise pg_err_default # Relancer pour être attrapé

        if not mix_ok: # Si aucune tentative d'initialisation du mixer n'a réussi
            # Ce cas ne devrait pas être atteint si les exceptions ci-dessus sont bien relancées et attrapées par le bloc pg_err général.
            # Mais par sécurité :
            print("[SoundCLI] CRITICAL: Mixer could not be initialized. Sound playback aborted.")
            raise pygame.error("Mixer initialization failed after all attempts.")


        # --- Load and Play Sound ---
        print(f"[SoundCLI] Loading sound: '{sound_path}'")
        try:
            sound = pygame.mixer.Sound(sound_path)
            print(f"[SoundCLI] Sound loaded successfully: '{os.path.basename(sound_path)}'")
        except pygame.error as load_err:
            print(f"[SoundCLI] ERR Pygame during sound load: {load_err}")
            raise load_err # Relancer pour être attrapé

        print(f"[SoundCLI] Playing sound (loops={loops})...")
        channel = sound.play(loops=loops)
        if channel is None:
            mixer_err_msg = pygame.mixer.get_error() # Obtenir l'erreur spécifique du mixer si disponible
            print(f"[SoundCLI] ERR: Failed to play sound. play() returned None. Mixer error: '{mixer_err_msg if mixer_err_msg else 'Unknown'}'")
            # Pas besoin de 'raise' ici, car l'absence de channel est une condition d'erreur que nous gérons.
            # Le script va quand même se terminer proprement via le bloc finally.
        else:
            print(f"[SoundCLI] Sound playing on channel: {channel}. Waiting for playback to finish...")
            while channel.get_busy(): # pygame.mixer.get_busy() n'est pas suffisant, il faut vérifier le channel spécifique.
                time.sleep(0.1)
            print("[SoundCLI] Sound playback finished.")

    except pygame.error as pg_err: # Attrape les erreurs Pygame relancées (init, load) ou nouvelles
        print(f"[SoundCLI] ERR Pygame (General): {pg_err}")
        traceback.print_exc(file=sys.stderr) # Imprimer la trace sur stderr
    except Exception as e: # Attrape toutes les autres exceptions
        print(f"[SoundCLI] ERR Unexpected: {e}")
        traceback.print_exc(file=sys.stderr)
    finally:
        # --- Cleanup ---
        # Quitter le mixer seulement s'il a été initialisé (mix_ok est True) ET s'il est toujours initialisé
        if mix_ok and pygame.mixer.get_init():
            print("[SoundCLI] Quitting mixer...")
            pygame.mixer.quit()
        # Quitter Pygame seulement s'il a été initialisé (pg_ok est True) ET s'il est toujours initialisé
        if pg_ok and pygame.get_init():
            print("[SoundCLI] Quitting Pygame module...")
            pygame.quit()
        print("[SoundCLI] Exiting cli_sound process.")
        # Toujours sortir avec 0 pour ne pas causer de panique au scheduler,
        # les erreurs sont logguées et visibles.
        sys.exit(0)

if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--play-sound':
        run_sound_cli() # Exécuter le lecteur de son si argument présent
    else:
        # --- Démarrage du serveur Web Backend ---
        logger.info("Démarrage application backend...")
        try:
            logger.info("Chargement config initiale...")
            if not load_all_configs():
                logger.critical("ERREUR chargement config initiale. Vérifier fichiers JSON et chemins.")
                # Continuer ? Ou arrêter ? Préférable d'arrêter si config échoue.
                sys.exit("Arrêt dû à une erreur de configuration.")
            else: logger.info("Config initiale chargée.")

            logger.info("Tentative démarrage scheduler...")
            if start_scheduler_thread():
                if schedule_manager: logger.info("Activation scheduler par défaut..."); schedule_manager.start()
                else: logger.error("Incohérence: start_scheduler_thread OK mais manager None?")
            else: logger.error("Échec démarrage scheduler (voir logs).")

            logger.info(f"Lancement serveur sur 0.0.0.0:5000...")
            try:
                 from waitress import serve
                 logger.info("Utilisation serveur: Waitress"); serve(app, host='0.0.0.0', port=5000, threads=8) # Augmenter threads?
            except ImportError:
                 logger.warning("Serveur: Flask dev (Waitress non trouvé - NON RECOMMANDÉ PROD)"); app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)

        except Exception as e: logger.critical(f"Erreur critique démarrage/exécution: {e}", exc_info=True)
        finally:
            logger.info("Arrêt application demandé.")
            # --- Nettoyage ---
            if alert_process and alert_process.poll() is None:
                 pid = alert_process.pid; logger.warning(f"Arrêt alerte active (PID {pid})...");
                 try: alert_process.kill(); alert_process.wait(timeout=2); logger.info(f"Alerte (PID {pid}) tuée.")
                 except Exception as kill_e: logger.error(f"Erreur kill alerte: {kill_e}")
                 alert_process = None
            if schedule_manager: logger.info("Arrêt scheduler..."); schedule_manager.shutdown()
            if scheduler_thread and scheduler_thread.is_alive():
                logger.info("Attente fin scheduler thread (max 5s)..."); scheduler_thread.join(timeout=5)
                if scheduler_thread.is_alive(): logger.warning("Scheduler thread n'a pas terminé.")
                else: logger.info("Scheduler thread terminé.")
            logging.shutdown(); print("Application Sonnerie Backend terminée.")