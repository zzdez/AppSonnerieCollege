# backend_server.py

import os
import json
import threading
import time
import subprocess
from datetime import datetime, date, timedelta
import calendar # Ajouté pour calendar.monthrange
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
import copy # Added import

# --- Import des modules locaux ---
# Utilisation d'un bloc try/except pour une erreur de démarrage plus claire
try:
    from scheduler import SchedulerManager
    from constants import (CONFIG_PATH, MP3_PATH, USERS_FILE, PARAMS_FILE,
                           DONNEES_SONNERIES_FILE, ROLES_CONFIG_FILE,
                           DEPARTEMENTS_ZONES, LISTE_DEPARTEMENTS, JOURS_SEMAINE_ASSIGNATION, AUCUNE_SONNERIE,
                           AVAILABLE_PERMISSIONS, DEFAULT_ROLE_PERMISSIONS, FRIENDLY_PERMISSION_NAMES, PERMISSIONS_MODEL) # Ajout des nouvelles constantes
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

@app.context_processor
def utility_processor():
    """Injecte des fonctions utilitaires dans le contexte des templates Jinja2."""
    def check_permission(permission_name):
        # S'assurer que current_user est disponible et authentifié
        if hasattr(current_user, 'is_authenticated') and current_user.is_authenticated:
            return user_has_permission(current_user, permission_name)
        return False
    return dict(user_has_permission=check_permission)

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

# ==============================================================================
# Mécanisme de Vérification des Permissions Granulaires
# ==============================================================================

def user_has_permission(user, permission_name: str) -> bool:
    """
    Vérifie si un utilisateur possède une permission spécifique.
    Prend en compte la permission spéciale 'admin:has_all_permissions'.
    """
    if not user or not hasattr(user, 'is_authenticated') or not user.is_authenticated:
        return False # Utilisateur non valide ou non authentifié

    user_permissions = getattr(user, 'permissions', {}) # Devrait être un dict
    if not isinstance(user_permissions, dict):
        logger.error(f"Attribut 'permissions' invalide pour l'utilisateur '{user.id}'. Attendu: dict, Reçu: {type(user_permissions)}")
        return False # Traiter comme si aucune permission pour la sécurité

    # Vérification de la permission spéciale 'admin:has_all_permissions'
    if user_permissions.get("admin:has_all_permissions") is True:
        logger.debug(f"Permission accordée à '{user.id}' via 'admin:has_all_permissions' pour '{permission_name}'.")
        return True

    # Logique principale de vérification
    if ":" in permission_name:
        section, action = permission_name.split(":", 1)
        if section == "page":
            # Les permissions de type "page:..." sont des clés directes
            has_perm = user_permissions.get(permission_name) is True
            logger.debug(f"Check perm page: '{permission_name}' for user '{user.id}'. Result: {has_perm}. User perms: {user_permissions}")
            return has_perm
        else:
            # Permissions granulaires (ex: "sound:upload")
            section_permissions = user_permissions.get(section)
            if isinstance(section_permissions, dict):
                has_perm = section_permissions.get(action) is True
                logger.debug(f"Check perm granular: '{permission_name}' (section '{section}', action '{action}') for user '{user.id}'. Result: {has_perm}. Section perms: {section_permissions}")
                return has_perm
            else:
                # Si la section n'est pas un dict (ou n'existe pas, get renverra None), l'action n'est pas autorisée
                logger.debug(f"Check perm granular: Section '{section}' non trouvée ou invalide pour '{permission_name}' for user '{user.id}'. User perms: {user_permissions}")
                return False
    else:
        # Fallback pour permissions sans ':' (ancien style, ne devrait plus arriver)
        # Ou pour des permissions de premier niveau qui ne sont pas des pages (ex: "admin:has_all_permissions" déjà géré)
        has_perm = user_permissions.get(permission_name) is True
        logger.debug(f"Check perm direct: '{permission_name}' for user '{user.id}'. Result: {has_perm}. User perms: {user_permissions}")
        return has_perm

def permission_access_denied(permission_name: str):
    """
    Action commune en cas de refus d'accès basé sur une permission manquante.
    """
    user_display_name = current_user.id if current_user.is_authenticated else 'Anonyme'
    user_actual_role = getattr(current_user, 'role', 'N/A') if current_user.is_authenticated else 'N/A'

    logger.warning(f"Accès refusé pour l'utilisateur '{user_display_name}' (rôle: {user_actual_role}) à une ressource nécessitant la permission '{permission_name}'.")
    flash(f"Accès refusé. La permission '{permission_name}' est requise pour accéder à cette ressource.", "error")
    return redirect(url_for('index'))

def require_permission(permission_name: str):
    """
    Décorateur pour vérifier si l'utilisateur courant a une permission spécifique.
    Doit être utilisé APRÈS @login_required.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                # Normalement géré par @login_required, mais double sécurité.
                # Flask-Login redirigera vers la page de login.
                # On pourrait aussi appeler login_manager.unauthorized()
                logger.warning(f"Tentative d'accès à une ressource protégée par permission ('{permission_name}') par un utilisateur non authentifié.")
                return login_manager.unauthorized()


            if not user_has_permission(current_user, permission_name):
                return permission_access_denied(permission_name)
            return f(*args, **kwargs)
        return decorated_function
    return decorator

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

def _merge_permissions(base_perms, override_perms):
    """
    Merges override_perms into base_perms.
    Override_perms takes precedence.
    This is a deep merge for nested dictionaries.
    """
    if not isinstance(override_perms, dict) or not override_perms:
        # If override_perms is None, empty, or not a dict, return a deep copy of base_perms
        return copy.deepcopy(base_perms)

    merged = copy.deepcopy(base_perms)

    for key, override_value in override_perms.items():
        if isinstance(override_value, dict) and isinstance(merged.get(key), dict):
            # If both current value in merged and override_value are dicts, recurse
            merged[key] = _merge_permissions(merged[key], override_value)
        else:
            # Otherwise, override_value takes precedence (it could be a boolean or a new sub-dict)
            merged[key] = copy.deepcopy(override_value) # deepcopy override_value as well for safety
    return merged

# --- Classe Utilisateur pour Flask-Login ---
class User(UserMixin):
    """Représente un utilisateur connecté."""
    def __init__(self, id, role="lecteur", nom_complet="", permissions=None):
        self.id = id
        self.role = role
        self.nom_complet = nom_complet
        self.permissions = permissions if permissions is not None else {}

@login_manager.user_loader
def load_user(user_id):
    """Charge un utilisateur à partir de l'ID stocké dans la session."""
    user_info = users_data.get(user_id)
    if user_info and isinstance(user_info, dict):
        user_role = user_info.get("role", "lecteur")
        user_nom_complet = user_info.get("nom_complet", "")
        # Récupérer les permissions pour ce rôle depuis roles_config_data
        role_permissions_base = roles_config_data.get("roles", {}).get(user_role, {}).get("permissions", {})
        custom_permissions_override = user_info.get("custom_permissions") # This could be None or {}

        if custom_permissions_override is not None and isinstance(custom_permissions_override, dict) and custom_permissions_override: # Check if it's a non-empty dict
            logger.debug(f"User '{user_id}' has custom permissions. Merging with role '{user_role}' permissions. Custom: {custom_permissions_override}")
            effective_permissions = _merge_permissions(role_permissions_base, custom_permissions_override)
        else:
            logger.debug(f"User '{user_id}' using role '{user_role}' permissions directly (no valid/non-empty custom overrides found).")
            effective_permissions = copy.deepcopy(role_permissions_base) # Make a copy

        logger.debug(f"Effective permissions for user '{user_id}' (role '{user_role}'): {effective_permissions}")
        return User(user_id, role=user_role, nom_complet=user_nom_complet, permissions=effective_permissions)
    elif isinstance(user_info, str): # Fallback pour ancien format (juste le hash, users.json ne devrait plus en avoir)
         logger.warning(f"Utilisateur '{user_id}' avec ancien format de données (hash seul). Rôle 'lecteur' par défaut.")
         # Pour le fallback, on assigne les permissions du rôle "lecteur" depuis roles_config_data
         # Pas de gestion de custom_permissions pour l'ancien format, on utilise directement les permissions du rôle 'lecteur'.
         fallback_role_permissions = roles_config_data.get("roles", {}).get("lecteur", {}).get("permissions", {})
         logger.debug(f"Effective permissions for legacy user '{user_id}' (role 'lecteur'): {fallback_role_permissions}")
         return User(user_id, role="lecteur", nom_complet="Utilisateur (format obsolète)", permissions=copy.deepcopy(fallback_role_permissions))
    logger.warning(f"Tentative chargement utilisateur inexistant ou format invalide: {user_id}")
    return None # Indique à Flask-Login que l'utilisateur n'est plus valide

# --- Variables globales pour stocker la configuration chargée ---
users_data = {}
college_params = {}
day_types = {}
weekly_planning = {}
planning_exceptions = {}
roles_config_data = {}

# --- Instances des gestionnaires ---
# Initialiser HolidayManager (passe le logger et le dossier cache)
holiday_manager = HolidayManager(logger, cache_dir=CONFIG_PATH)

# Le scheduler sera initialisé après chargement config dans le bloc __main__
scheduler_thread = None
schedule_manager = None
alert_process = None # Référence au subprocess de l'alerte active
current_alert_filename = None # Nom du fichier de l'alerte active


# ==============================================================================
# Fonctions de Chargement / Rechargement de la Configuration
# ==============================================================================

def load_users(filename=USERS_FILE):
    """Charge le fichier des utilisateurs (users.json) et migre les anciens formats."""
    global users_data
    path = os.path.join(CONFIG_PATH, filename)
    logger.info(f"Chargement du fichier utilisateurs: {path}")

    users_data_from_file = {}
    needs_saving = False

    try:
        with open(path, 'r', encoding='utf-8') as f:
            users_data_from_file = json.load(f)
        logger.info(f"Fichier utilisateurs '{filename}' chargé ({len(users_data_from_file)} entrées).")
    except FileNotFoundError:
        logger.info(f"Fichier utilisateurs '{filename}' non trouvé. Initialisation avec un dictionnaire vide.")
        users_data_from_file = {}
        # Le fichier sera créé si save_users_data() est appelé plus tard (par exemple après une migration ou création d'utilisateur)
    except json.JSONDecodeError:
        logger.error(f"Erreur de décodage JSON dans '{filename}'. Le fichier est peut-être corrompu. Initialisation avec un dictionnaire vide.")
        users_data_from_file = {} # Réinitialiser pour éviter d'utiliser des données corrompues
        needs_saving = True # Forcer la sauvegarde pour écraser le fichier corrompu avec une structure vide (ou migrée)
    except Exception as e:
        logger.error(f"Erreur inattendue lors du chargement de '{filename}': {e}", exc_info=True)
        users_data_from_file = {} # Sécurité
        # On ne force pas la sauvegarde ici, car l'erreur est inconnue.
        users_data = users_data_from_file # Assigner quand même pour que le reste du système voie la structure vide.
        return False # Indiquer un échec de chargement partiel/total

    # Vérification et normalisation des données utilisateur
    default_role_for_migration = "lecteur" # Rôle par défaut si manquant ou invalide

    for username, user_info in list(users_data_from_file.items()):
        made_change = False
        if isinstance(user_info, str): # Ancien format (hash direct)
            logger.warning(f"Utilisateur '{username}': Ancien format détecté (hash seul). Migration vers structure dictionnaire.")
            users_data_from_file[username] = {
                "hash": user_info,
                "nom_complet": "Utilisateur (migré)",
                "role": default_role_for_migration
                # Pas de champ 'permissions' ici, il sera chargé dynamiquement via le rôle
            }
            made_change = True
        elif isinstance(user_info, dict):
            # 1. Vérifier et assigner 'nom_complet'
            if "nom_complet" not in user_info or not user_info["nom_complet"]: # Aussi si vide
                user_info["nom_complet"] = f"Utilisateur {username}" # Nom par défaut
                logger.info(f"Utilisateur '{username}': Champ 'nom_complet' manquant ou vide. Ajout d'une valeur par défaut.")
                made_change = True

            # 2. Vérifier et assigner 'role' (case-insensitive validation)
            user_role_from_file = user_info.get("role")
            defined_roles_map_lower_to_original = {r.lower(): r for r in roles_config_data.get("roles", {}).keys()}

            if not user_role_from_file:
                logger.warning(f"Utilisateur '{username}': Champ 'role' manquant. Assignation du rôle par défaut '{default_role_for_migration}'.")
                user_info["role"] = default_role_for_migration
                made_change = True
            else:
                user_role_from_file_lower = user_role_from_file.lower()
                if user_role_from_file_lower in defined_roles_map_lower_to_original:
                    correctly_cased_role = defined_roles_map_lower_to_original[user_role_from_file_lower]
                    if user_info["role"] != correctly_cased_role: # Check if casing is different
                        logger.info(f"Utilisateur '{username}': Rôle '{user_info['role']}' normalisé en '{correctly_cased_role}' (casse corrigée).")
                        user_info["role"] = correctly_cased_role
                        made_change = True
                    # Si la casse était déjà correcte, made_change reste false pour cette partie, ce qui est OK.
                else:
                    logger.warning(f"Utilisateur '{username}': Rôle '{user_role_from_file}' invalide (non défini dans roles_config.json, même insensible à la casse). Assignation du rôle par défaut '{default_role_for_migration}'.")
                    user_info["role"] = default_role_for_migration
                    made_change = True

            # 3. Supprimer l'ancien champ 'permissions' s'il existe
            if "permissions" in user_info:
                logger.info(f"Utilisateur '{username}': Suppression de l'ancien champ 'permissions' stocké dans users.json.")
                del user_info["permissions"]
                made_change = True
        else:
            logger.error(f"Format de données utilisateur inconnu pour '{username}': {type(user_info)}. Ignoré.")
            # On pourrait le supprimer ou tenter une migration plus agressive. Pour l'instant, on logue et ignore.
            continue # Passer au suivant

        if made_change:
            needs_saving = True

    users_data = users_data_from_file # Affecter les données (potentiellement migrées/nettoyées) à la variable globale

    if needs_saving:
        logger.info("Modifications détectées lors du chargement des utilisateurs (migration ou correction). Sauvegarde du fichier users.json...")
        if save_users_data(): # save_users_data utilise la variable globale users_data
            logger.info("Fichier users.json mis à jour avec succès après migration/correction.")
        else:
            logger.error("Échec de la sauvegarde du fichier users.json après migration/correction.")
            return False # Indiquer un problème de sauvegarde

    logger.info(f"Chargement utilisateurs terminé. {len(users_data)} utilisateurs en mémoire.")
    return True

def load_roles_config(filename=ROLES_CONFIG_FILE):
    """Charge la configuration des rôles (roles_config.json)."""
    global VALID_ROLES # Moved to the top of the function
    global roles_config_data
    path = os.path.join(CONFIG_PATH, filename)
    logger.info(f"Chargement du fichier de configuration des rôles: {path}")

    default_roles_config = {"roles": {}} # Structure par défaut minimale

    try:
        with open(path, 'r', encoding='utf-8') as f:
            loaded_data = json.load(f)
        # Valider la structure de base (doit contenir une clé "roles" qui est un dict)
        if isinstance(loaded_data, dict) and isinstance(loaded_data.get("roles"), dict):
            roles_config_data = loaded_data
            logger.info(f"Fichier de configuration des rôles '{filename}' chargé ({len(roles_config_data.get('roles', {}))} rôles).")
        else:
            logger.error(f"Structure JSON invalide dans '{filename}'. La clé 'roles' doit être un dictionnaire. Utilisation de la configuration par défaut.")
            roles_config_data = default_roles_config
            # On pourrait considérer cela comme une erreur et retourner False si la structure est critique
            # Pour l'instant, on initialise par défaut et on continue (True)

        # Update VALID_ROLES based on the loaded roles_config_data
        # global VALID_ROLES # This declaration is now at the function top
        if isinstance(roles_config_data, dict) and isinstance(roles_config_data.get("roles"), dict) and roles_config_data.get("roles"):
            VALID_ROLES = list(roles_config_data["roles"].keys())
            logger.info(f"VALID_ROLES dynamically updated from roles_config.json: {VALID_ROLES}")
        else:
            # Fallback if roles_config_data is not as expected or "roles" is empty
            VALID_ROLES = ["Administrateur", "Collaborateur", "Lecteur"] # Hardcoded fallback
            logger.warning(f"Failed to dynamically update VALID_ROLES from roles_config.json. Using hardcoded fallback: {VALID_ROLES}")
        return True
    except FileNotFoundError:
        logger.info(f"Fichier de configuration des rôles '{filename}' non trouvé. Initialisation avec une structure par défaut vide.")
        roles_config_data = default_roles_config

        # Update VALID_ROLES based on the loaded roles_config_data (even if default)
        # global VALID_ROLES # This declaration is now at the function top
        if isinstance(roles_config_data, dict) and isinstance(roles_config_data.get("roles"), dict) and roles_config_data.get("roles"): # This will be false if roles is empty
            VALID_ROLES = list(roles_config_data["roles"].keys())
            logger.info(f"VALID_ROLES dynamically updated from default roles_config.json (FileNotFound): {VALID_ROLES}")
        else:
            VALID_ROLES = ["Administrateur", "Collaborateur", "Lecteur"] # Hardcoded fallback
            logger.warning(f"Failed to dynamically update VALID_ROLES from default roles_config.json (FileNotFound). Using hardcoded fallback: {VALID_ROLES}")
        return True # Pas une erreur fatale, on utilise la structure par défaut
    except json.JSONDecodeError:
        logger.error(f"Erreur de décodage JSON dans '{filename}'. Le fichier est peut-être corrompu. Initialisation avec une structure par défaut.")
        roles_config_data = default_roles_config
        # Update VALID_ROLES with fallback on JSONDecodeError
        # global VALID_ROLES # This declaration is now at the function top
        VALID_ROLES = ["Administrateur", "Collaborateur", "Lecteur"] # Hardcoded fallback
        logger.warning(f"Failed to parse roles_config.json (JSONDecodeError). Using hardcoded fallback for VALID_ROLES: {VALID_ROLES}")
        return False # Erreur de parsing est plus sérieuse
    except Exception as e:
        logger.error(f"Erreur inattendue lors du chargement de '{filename}': {e}", exc_info=True)
        roles_config_data = default_roles_config # Sécurité
        # Update VALID_ROLES with fallback on other exceptions
        # global VALID_ROLES # This declaration is now at the function top
        VALID_ROLES = ["Administrateur", "Collaborateur", "Lecteur"] # Hardcoded fallback
        logger.warning(f"Unexpected error loading roles_config.json. Using hardcoded fallback for VALID_ROLES: {VALID_ROLES}")
        return False # Autre erreur majeure

def save_roles_config(filename=ROLES_CONFIG_FILE):
    """Sauvegarde la configuration des rôles (roles_config_data) dans le fichier JSON."""
    path = os.path.join(CONFIG_PATH, filename)
    logger.info(f"Sauvegarde de la configuration des rôles dans: {path}")
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(roles_config_data, f, indent=2, ensure_ascii=False)
        logger.info(f"Configuration des rôles sauvegardée avec succès ({len(roles_config_data.get('roles', {}))} rôles).")
        return True
    except Exception as e:
        logger.error(f"Erreur lors de la sauvegarde du fichier de configuration des rôles {path}: {e}", exc_info=True)
        return False

def save_users_data(filename=USERS_FILE):
    """Sauvegarde la configuration des utilisateurs (users_data) dans le fichier JSON."""
    path = os.path.join(CONFIG_PATH, filename)
    logger.info(f"Sauvegarde des données utilisateurs dans: {path}")
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(users_data, f, indent=2, ensure_ascii=False)
        logger.info(f"Données utilisateurs sauvegardées avec succès ({len(users_data)} utilisateurs).")
        return True
    except Exception as e:
        logger.error(f"Erreur lors de la sauvegarde du fichier utilisateurs {path}: {e}", exc_info=True)
        return False

def load_college_params(filename=PARAMS_FILE):
    """Charge les paramètres généraux (parametres_college.json) et lance le chargement API fériés."""
    global college_params, holiday_manager
    path = os.path.join(CONFIG_PATH, filename)
    logger.info(f"Chargement des paramètres généraux depuis : {path}")
    default_params = {
        "alert_click_mode": "double",
        "status_refresh_interval_seconds": 15
        # Ajoutez ici d'autres valeurs par défaut si nécessaire pour d'autres clés
    }
    try:
        with open(path, 'r', encoding='utf-8') as f:
            loaded_params_from_file = json.load(f)

        # Fusionner les paramètres chargés avec les valeurs par défaut
        # Les valeurs du fichier écrasent les valeurs par défaut si elles existent
        college_params = {**default_params, **loaded_params_from_file}

        # Vérifier si des clés par défaut ont été ajoutées (si elles n'étaient pas dans le fichier)
        # et si oui, potentiellement sauvegarder le fichier pour persister ces défauts.
        # Pour l'instant, on ne resauvegarde pas ici, on attend une action explicite de sauvegarde.
        # if any(key not in loaded_params_from_file for key in default_params):
        #     logger.info(f"Des valeurs par défaut ont été appliquées à parametres_college.json. Envisagez de sauvegarder.")

        logger.info(f"Paramètres généraux chargés : {college_params}")

        api_url = college_params.get('api_holidays_url')
        country = college_params.get('country_code_holidays', 'FR')
        if holiday_manager:
            holiday_manager.load_holidays_from_api(api_url, country) # Log interne à HM
        else:
            logger.error("HolidayManager non initialisé, impossible de charger les jours fériés.")
        return True
    except FileNotFoundError:
        logger.info(f"Fichier de paramètres '{path}' non trouvé. Initialisation avec les valeurs par défaut uniquement.")
        college_params = default_params.copy() # Utiliser une copie des défauts
        # Il serait bien de sauvegarder le fichier avec les défauts ici, mais load_all_configs ne gère pas la sauvegarde.
        # Cela sera fait lors de la première sauvegarde via l'interface.
        return True # Pas une erreur fatale, on continue avec les défauts
    except json.JSONDecodeError:
        logger.error(f"Erreur de décodage JSON dans le fichier de paramètres '{path}'. Utilisation des valeurs par défaut.")
        college_params = default_params.copy()
        return False # Erreur de formatage est plus sérieuse
    except Exception as e:
        logger.error(f"Erreur inattendue lors du chargement du fichier de paramètres '{path}': {e}", exc_info=True)
        college_params = default_params.copy()
        return False

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
    # Ordre: Params (pour zone/URL) -> Sonneries (utilise params) -> Roles -> Users (utilise roles)
    success_params = load_college_params()
    success_sonneries = load_sonneries_data()
    success_roles = load_roles_config()
    success_users = load_users() # load_users peut dépendre de roles_config pour la validation des rôles
    all_ok = success_params and success_sonneries and success_roles and success_users
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
@require_permission("page:view_config_general")
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
@require_permission("page:view_config_weekly")
def config_weekly_page():
    """Sert la page de configuration du planning hebdomadaire."""
    user_id = current_user.id
    logger.info(f"User '{user_id}' accède à la page /config/weekly")
    # On passe current_user pour la barre de navigation du template
    return render_template('config_weekly.html', current_user=current_user)

@app.route('/config/day_types')
@login_required
@require_permission("page:view_config_day_types")
def config_day_types_page():
    """Sert la page de configuration des journées types."""
    user_id = current_user.id
    logger.info(f"User '{user_id}' accède à la page /config/day_types")
    # On pourrait passer la liste des noms de JT directement ici pour éviter un appel API initial,
    # mais pour l'instant on laisse le JS faire l'appel API.
    return render_template('config_day_types.html', current_user=current_user)

@app.route('/config/exceptions')
@login_required
@require_permission("page:view_config_exceptions")
def config_exceptions_page():
    """Sert la page de configuration des exceptions de planning."""
    user_id = current_user.id
    logger.info(f"User '{user_id}' accède à la page /config/exceptions")
    # On pourrait passer la liste des JT ici pour le select, mais le JS la chargera.
    return render_template('config_exceptions.html', current_user=current_user)
@app.route('/api/config/sounds', methods=['GET'])
@login_required
@require_permission("page:view_config_sounds")
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
@require_permission("page:view_config_sounds")
def config_sounds_page():
    """Sert la page de configuration des sonneries disponibles."""
    user_id = current_user.id
    logger.info(f"User '{user_id}' accède à la page /config/sounds")
    return render_template('config_sounds.html', current_user=current_user)

@app.route('/config/users')
@login_required
@require_permission("page:view_config_users")
def config_users_page():
    """Sert la page de gestion des utilisateurs."""
    user_id = current_user.id
    logger.info(f"User '{user_id}' (admin) accède à la page /config/users")
    return render_template('config_users.html',
                           current_user=current_user,
                           available_permissions=AVAILABLE_PERMISSIONS,
                           default_role_permissions=DEFAULT_ROLE_PERMISSIONS,
                           friendly_permission_names=FRIENDLY_PERMISSION_NAMES)

@app.route('/')
@login_required
@require_permission("page:view_control")
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
    logger.debug("Requête /api/status")
    global current_alert_filename, alert_process # Accéder aux variables globales

    sch_running = False
    next_time = None
    next_label = None
    last_err = "Scheduler non initialisé"

    alert_is_truly_active = False # Sera déterminée ci-dessous

    # Vérifier l'état du processus d'alerte
    if alert_process is not None:
        if alert_process.poll() is None: # Le processus est toujours en cours
            alert_is_truly_active = True
            # current_alert_filename devrait déjà être correct s'il est en cours
        else: # Le processus s'est terminé (ou n'a pas pu démarrer correctement)
            logger.info(f"Le processus d'alerte pour '{current_alert_filename}' (PID: {alert_process.pid if alert_process else 'N/A'}) semble terminé. Nettoyage de l'état.")
            # Appeler stop_current_alert_process pour nettoyer current_alert_filename et alert_process
            # Cela mettra current_alert_filename à None et alert_is_truly_active restera False.
            stop_current_alert_process()
            alert_is_truly_active = False # Explicitement False après nettoyage
    else:
        # S'il n'y a pas de processus, current_alert_filename devrait être None.
        # On s'en assure au cas où.
        if current_alert_filename is not None:
            logger.warning(f"alert_process est None, mais current_alert_filename ('{current_alert_filename}') ne l'est pas. Réinitialisation.")
            current_alert_filename = None


    if schedule_manager:
        sch_running = schedule_manager.is_running()
        next_time = schedule_manager.get_next_ring_time_iso()
        next_label = schedule_manager.get_next_ring_label()
        last_err = schedule_manager.get_last_error()

    status = {
        "scheduler_running": sch_running,
        "next_ring_time": next_time,
        "next_ring_label": next_label,
        "last_error": last_err or "Aucune",
        "alert_active": alert_is_truly_active,
        "alert_type": current_alert_filename if alert_is_truly_active else None, # AJOUT DE LA CLÉ alert_type
        "current_time": datetime.now().isoformat()
    }
    logger.debug(f"Statut renvoyé par /api/status: {status}")
    return jsonify(status)

@app.route('/api/planning/activate', methods=['POST'])
@login_required
@require_permission("control:scheduler_activate")
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
@require_permission("control:scheduler_deactivate")
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
    """Tente d'arrêter le processus d'alerte courant et réinitialise current_alert_filename."""
    global alert_process, current_alert_filename # Déclaration des globales
    action_taken = False
    if alert_process and alert_process.poll() is None: # Si le processus existe et est en cours
        pid = alert_process.pid
        logger.warning(f"Arrêt processus alerte (PID: {pid}) pour l'alerte '{current_alert_filename}'...")
        try:
            alert_process.terminate()
            alert_process.wait(timeout=2) # Attendre que le processus se termine
            logger.info(f"Alerte (PID: {pid}, type: '{current_alert_filename}') arrêtée (terminate).")
        except subprocess.TimeoutExpired:
            logger.error(f"Timeout arrêt alerte (PID:{pid}, type: '{current_alert_filename}'), tentative de kill.")
            alert_process.kill()
            alert_process.wait() # Attendre après kill
            logger.info(f"Alerte (PID: {pid}, type: '{current_alert_filename}') arrêtée (kill).")
        except Exception as e:
            logger.error(f"Erreur lors de l'arrêt de l'alerte (PID:{pid}, type: '{current_alert_filename}'): {e}", exc_info=True)
        finally:
            alert_process = None
            current_alert_filename = None # RÉINITIALISER ICI
            action_taken = True # Une action a été tentée
    else: # Pas de processus actif ou processus déjà terminé
        if alert_process: # Le processus existe mais n'est plus en cours (poll() is not None)
             logger.debug(f"stop_current_alert_process: Processus d'alerte (type: '{current_alert_filename}') déjà terminé. Nettoyage.")
             alert_process = None # Nettoyer la référence au processus
        else:
             logger.debug("stop_current_alert_process: Pas de processus d'alerte actif à arrêter.")
        current_alert_filename = None # S'ASSURER QUE C'EST RÉINITIALISÉ AUSSI DANS CE CAS
        # action_taken reste False si aucun processus n'était activement en cours
    return action_taken

@app.route('/api/alert/trigger/<filename>', methods=['POST'])
@login_required
@require_permission("control:alert_trigger_any")
def trigger_alert(filename):
    """Déclenche une alerte (arrête la précédente si besoin)."""
    user = current_user.id
    global alert_process, current_alert_filename # Déclaration des globales
    logger.info(f"User '{user}': Trigger alert: {filename}")

    logger.info("Vérification et arrêt alerte précédente...");
    stop_current_alert_process() # Cela va aussi mettre current_alert_filename à None

    if not MP3_PATH or not os.path.isdir(MP3_PATH):
        logger.error(f"Trigger alert échoué: MP3_PATH invalide: {MP3_PATH}")
        return jsonify({"error": "Config MP3 invalide."}), 500

    sound_path = os.path.join(MP3_PATH, filename)
    if not os.path.isfile(sound_path):
        logger.error(f"Alert file not found: {sound_path}")
        return jsonify({"error": f"Fichier alerte '{filename}' introuvable."}), 404

    try:
        # Vérifications de permission spécifiques pour PPMS et Attentat
        is_ppms = (filename == college_params.get("sonnerie_ppms"))
        is_attentat = (filename == college_params.get("sonnerie_attentat"))

        if is_ppms and not user_has_permission(current_user, "control:alert_trigger_ppms"):
            return permission_access_denied("control:alert_trigger_ppms")
        if is_attentat and not user_has_permission(current_user, "control:alert_trigger_attentat"):
            return permission_access_denied("control:alert_trigger_attentat")
        # Si ce n'est ni PPMS ni Attentat, la permission "control:alert_trigger_any" est suffisante (déjà vérifiée par le décorateur).

        logger.info(f"Lancement processus alerte '{filename}' (non-boucle)...")
        audio_device_name = college_params.get("nom_peripherique_audio_sonneries")
        cmd = [sys.executable, __file__, '--play-sound', sound_path]
        if audio_device_name:
            cmd.extend(['--device', audio_device_name])
            logger.info(f"Triggering alert '{filename}' on device: {audio_device_name}")
        else:
            logger.info(f"Triggering alert '{filename}' on default device.")

        logger.debug(f"Cmd: {' '.join(cmd)}")
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        alert_process = subprocess.Popen(cmd, creationflags=flags)
        current_alert_filename = filename  # STOCKER LE NOM DU FICHIER
        logger.info(f"Nouveau processus alerte démarré (PID: {alert_process.pid}) pour '{current_alert_filename}'.")
        return jsonify({"message": f"Alerte '{filename}' déclenchée."}), 200
    except Exception as e:
        logger.error(f"Erreur lancement processus alerte: {e}", exc_info=True)
        alert_process = None
        current_alert_filename = None # RÉINITIALISER EN CAS D'ERREUR
        return jsonify({"error": f"Erreur serveur: {e}"}), 500

@app.route('/api/alert/stop', methods=['POST'])
@login_required
@require_permission("control:alert_stop")
def stop_alert():
    """Arrête l'alerte active."""
    user = current_user.id; logger.info(f"User '{user}': Stop alert via API")
    stopped = stop_current_alert_process()
    return jsonify({"message": "Tentative d'arrêt effectuée." if stopped else "Aucune alerte active."}), 200

@app.route('/api/alert/end', methods=['POST'])
@login_required
@require_permission("control:alert_end")
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
@require_permission("control:config_reload")
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
@require_permission("page:view_control")
def api_config_settings():
    """Retourne les paramètres utiles à l'UI (noms fichiers alerte)."""
    user = current_user.id; logger.debug(f"User '{user}' requête /api/config/settings")
    try:
        # Assurer que college_params est chargé et contient les valeurs (avec défauts si besoin)
        # Les valeurs par défaut sont appliquées dans load_college_params
        settings = {
            "alert_files": {
                "ppms": college_params.get("sonnerie_ppms"),
                "attentat": college_params.get("sonnerie_attentat")
            },
            "alert_click_mode": college_params.get("alert_click_mode", "double"), # Défaut ici aussi par sécurité
            "status_refresh_interval_seconds": college_params.get("status_refresh_interval_seconds", 15) # Défaut ici aussi
        }
        logger.debug(f"API /api/config/settings renvoie: {settings}")
        return jsonify(settings)
    except Exception as e:
        logger.error(f"Erreur API /api/config/settings: {e}", exc_info=True)
        # Renvoyer des valeurs par défaut en cas d'erreur pour ne pas bloquer le front-end
        # mais loguer l'erreur pour investigation.
        default_settings_on_error = {
            "alert_files": {"ppms": None, "attentat": None},
            "alert_click_mode": "double",
            "status_refresh_interval_seconds": 15
        }
        return jsonify(default_settings_on_error), 500

@app.route('/api/audio_devices', methods=['GET'])
@login_required
@require_permission("page:view_config_general") # Utilisé dans config_general.html
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
@login_required
@require_permission("page:view_control")
def api_calendar_view():
    """Fournit les données pour le calendrier, supportant une vue annuelle ou mensuelle."""
    user = current_user.id
    academic_year_str = request.args.get('year')
    view_type = request.args.get('view_type', default='year').lower()
    target_month_num = request.args.get('month', default=None, type=int)
    target_trimester_num = request.args.get('trimester', default=None, type=int)
    target_semester_num = request.args.get('semester', default=None, type=int) # Nouveau paramètre pour semestre

    logger.debug(f"User '{user}' requête /api/calendar_view with params: year='{academic_year_str}', view_type='{view_type}', month='{target_month_num}', trimester='{target_trimester_num}', semester='{target_semester_num}'")

    if not academic_year_str:
        logger.warning("API /api/calendar_view: Paramètre 'year' (année scolaire YYYY-YYYY) manquant.")
        return jsonify({"error": "Paramètre 'year' (année scolaire YYYY-YYYY) manquant."}), 400

    try:
        # L'année de référence est toujours une année scolaire pour déterminer le contexte.
        start_acad_year, end_acad_year = map(int, academic_year_str.split('-'))
        if end_acad_year != start_acad_year + 1:
            raise ValueError("L'année de fin doit être l'année de début + 1.")
    except ValueError as e:
        logger.warning(f"API /api/calendar_view: Format année scolaire 'year' invalide: {academic_year_str}. Erreur: {e}")
        return jsonify({"error": f"Format année scolaire 'year' invalide. Attendu YYYY-YYYY. Détail: {e}"}), 400

    # Initialiser calendar_data pour s'assurer qu'elle est toujours définie
    calendar_data = {}
    start_date, end_date = None, None # Pour les vues qui utilisent get_calendar_view_data_range

    if view_type == 'month':
        if target_month_num is None or not (1 <= target_month_num <= 12):
            logger.warning(f"API /api/calendar_view (month view): Paramètre 'month' invalide ou manquant: {target_month_num}")
            return jsonify({"error": "Paramètre 'month' invalide ou manquant pour la vue mensuelle."}), 400

        current_calendar_year_for_month = start_acad_year if target_month_num >= 9 else end_acad_year

        import calendar # Assurer l'import
        try:
            _, num_days_in_month = calendar.monthrange(current_calendar_year_for_month, target_month_num)
            start_date = date(current_calendar_year_for_month, target_month_num, 1)
            end_date = date(current_calendar_year_for_month, target_month_num, num_days_in_month)
            logger.info(f"Calcul calendrier pour vue mensuelle: {start_date.strftime('%B %Y')} (Année scolaire: {academic_year_str})")
        except ValueError as e_date: # Mois invalide pour l'année (ne devrait pas arriver avec 1-12)
             logger.error(f"Erreur création date pour mois {target_month_num}, année {current_calendar_year_for_month}: {e_date}", exc_info=True)
             return jsonify({"error": "Erreur interne lors de la détermination des dates du mois."}), 500

        # Appel de la fonction existante qui génère les données pour une plage de dates
        calendar_data = get_calendar_view_data_range(start_date, end_date)
        if 'error' in calendar_data:
            logger.error(f"Erreur lors de la génération des données calendrier (month view): {calendar_data['error']}")
            return jsonify({"error": f"Erreur génération calendrier: {calendar_data['error']}"}), 500

    elif view_type == 'semester':
        if target_semester_num is None or not (1 <= target_semester_num <= 2):
            logger.warning(f"API /api/calendar_view (semester view): Paramètre 'semester' invalide ou manquant: {target_semester_num}")
            return jsonify({"error": "Paramètre 'semester' invalide ou manquant pour la vue semestrielle."}), 400

        semester_months_config = {
            1: [ # S1: Sept, Oct, Nov, Déc (start_acad_year), Jan (end_acad_year)
                (9, start_acad_year), (10, start_acad_year), (11, start_acad_year), (12, start_acad_year),
                (1, end_acad_year)
            ],
            2: [ # S2: Fev, Mar, Avr, Mai, Jui, Jul, Aoû (end_acad_year)
                (2, end_acad_year), (3, end_acad_year), (4, end_acad_year), (5, end_acad_year),
                (6, end_acad_year), (7, end_acad_year), (8, end_acad_year)
            ]
        }
        if target_semester_num not in semester_months_config: # Double check, ne devrait pas être atteint
             logger.error(f"Numéro de semestre '{target_semester_num}' non valide.")
             return jsonify({"error": f"Numéro de semestre '{target_semester_num}' non valide."}), 400

        months_in_semester = semester_months_config[target_semester_num]

        days_data_semester = {}
        import calendar # Assurer l'import localement
        from datetime import datetime # Assurer l'import localement

        for month_num, year_for_month in months_in_semester:
            num_days_in_month = calendar.monthrange(year_for_month, month_num)[1]
            for day_num in range(1, num_days_in_month + 1):
                current_date_obj = datetime(year_for_month, month_num, day_num).date()
                date_str_key = current_date_obj.strftime('%Y-%m-%d')

                if holiday_manager:
                    # Utilisation de get_day_type_and_desc comme pour trimester et get_calendar_view_data_range
                    day_info = holiday_manager.get_day_type_and_desc(current_date_obj, weekly_planning, planning_exceptions)
                    days_data_semester[date_str_key] = {"type": day_info.get('type', 'Erreur'), "description": day_info.get('description', 'N/A')}
                else:
                    logger.error("HolidayManager non disponible dans api_calendar_view (semester).")
                    days_data_semester[date_str_key] = {"type": "ErreurHM", "description": "HolidayManager non initialisé"}

        calendar_data = {"days": days_data_semester}
        logger.info(f"Calcul calendrier pour vue semestrielle: S{target_semester_num} de {academic_year_str}")

    elif view_type == 'year':
        # Logique pour l'année scolaire complète (Septembre à Août)
        start_date = date(start_acad_year, 9, 1)
        end_date = date(end_acad_year, 8, 31)
        logger.info(f"Calcul calendrier pour vue annuelle: {start_date} à {end_date} (Année scolaire: {academic_year_str})")

        # Appel de la fonction existante qui génère les données pour une plage de dates
        calendar_data = get_calendar_view_data_range(start_date, end_date)
        if 'error' in calendar_data:
            logger.error(f"Erreur lors de la génération des données calendrier (year view): {calendar_data['error']}")
            return jsonify({"error": f"Erreur génération calendrier: {calendar_data['error']}"}), 500

    elif view_type == 'trimester':
        if target_trimester_num is None or not (1 <= target_trimester_num <= 3):
            logger.warning(f"API /api/calendar_view (trimester view): Paramètre 'trimester' invalide ou manquant: {target_trimester_num}")
            return jsonify({"error": "Paramètre 'trimester' invalide ou manquant pour la vue trimestrielle."}), 400

        trimester_months_config = {
            1: [(9, start_acad_year), (10, start_acad_year), (11, start_acad_year), (12, start_acad_year)], # T1: Sept-Déc
            2: [(1, end_acad_year), (2, end_acad_year), (3, end_acad_year), (4, end_acad_year)],       # T2: Jan-Avr
            3: [(5, end_acad_year), (6, end_acad_year), (7, end_acad_year), (8, end_acad_year)]        # T3: Mai-Août
        }

        if target_trimester_num not in trimester_months_config:
             logger.error(f"Numéro de trimestre '{target_trimester_num}' non valide (ne devrait pas arriver si validation OK).") # Ne devrait pas être atteint
             return jsonify({"error": f"Numéro de trimestre '{target_trimester_num}' non valide."}), 400

        months_in_trimester = trimester_months_config[target_trimester_num]

        days_data_trimester = {}
        # S'assurer que calendar et datetime sont importés (normalement déjà fait en haut du fichier)
        import calendar
        from datetime import datetime # Bien que datetime soit global, c'est plus propre ici aussi

        for month_num, calendar_year_for_month in months_in_trimester:
            num_days_in_month = calendar.monthrange(calendar_year_for_month, month_num)[1]
            for day_num in range(1, num_days_in_month + 1):
                current_date_obj = datetime(calendar_year_for_month, month_num, day_num).date() # Utiliser .date() pour être consistent avec holiday_manager
                date_str_key = current_date_obj.strftime('%Y-%m-%d')

                if holiday_manager:
                    # Utiliser la même logique que get_calendar_view_data_range
                    day_info = holiday_manager.get_day_type_and_desc(current_date_obj, weekly_planning, planning_exceptions)
                    days_data_trimester[date_str_key] = {"type": day_info.get('type', 'Erreur'), "description": day_info.get('description', 'N/A')}
                else:
                    logger.error("HolidayManager non disponible dans api_calendar_view (trimester).")
                    days_data_trimester[date_str_key] = {"type": "ErreurHM", "description": "HolidayManager non initialisé"}

        calendar_data = {"days": days_data_trimester}
        # Pour la vue trimestrielle, on ne peuple pas 'vacations' et 'holidays' globaux pour l'instant,
        # car get_calendar_view_data_range n'est pas appelée. Si besoin, il faudrait le faire ici.
        logger.info(f"Calcul calendrier pour vue trimestrielle: T{target_trimester_num} de {academic_year_str}")

    else:
        logger.warning(f"API /api/calendar_view: Type de vue '{view_type}' non supporté.")
        return jsonify({"error": f"Type de vue '{view_type}' non supporté."}), 400

    # Ajouter les paramètres de debug au retour pour faciliter le développement frontend
    base_debug_params = {
        "requested_academic_year": academic_year_str,
        "requested_view_type": view_type,
        "requested_month": target_month_num if view_type == 'month' else None,
        "requested_trimester": target_trimester_num if view_type == 'trimester' else None,
        "requested_semester": target_semester_num if view_type == 'semester' else None
    }

    if view_type == 'year' or view_type == 'month':
        # Ces vues utilisent get_calendar_view_data_range et définissent start_date, end_date
        # calendar_data est déjà peuplé par get_calendar_view_data_range
        if start_date:
            base_debug_params["calculated_start_date"] = start_date.isoformat()
        if end_date:
            base_debug_params["calculated_end_date"] = end_date.isoformat()
        calendar_data["debug_params"] = base_debug_params

    elif view_type == 'trimester':
        # Cette vue peuple calendar_data['days'] directement.
        # months_in_trimester est défini dans la portée de ce bloc 'trimester'
        if 'months_in_trimester' in locals() and months_in_trimester:
            first_month_info = months_in_trimester[0]
            last_month_info = months_in_trimester[-1]
            trimester_start_d = date(first_month_info[1], first_month_info[0], 1)
            num_days_last_m = calendar.monthrange(last_month_info[1], last_month_info[0])[1]
            trimester_end_d = date(last_month_info[1], last_month_info[0], num_days_last_m)
            base_debug_params["calculated_trimester_start_date"] = trimester_start_d.isoformat()
            base_debug_params["calculated_trimester_end_date"] = trimester_end_d.isoformat()
        calendar_data["debug_params"] = base_debug_params

    elif view_type == 'semester':
        # Cette vue peuple calendar_data['days'] directement.
        # months_in_semester est défini dans la portée de ce bloc 'semester'
        if 'months_in_semester' in locals() and months_in_semester:
            first_month_info = months_in_semester[0]
            last_month_info = months_in_semester[-1]
            semester_start_d = date(first_month_info[1], first_month_info[0], 1)
            # Réimporter calendar si ce n'est pas déjà fait dans cette portée (c'est fait dans le bloc semester)
            num_days_last_m = calendar.monthrange(last_month_info[1], last_month_info[0])[1]
            semester_end_d = date(last_month_info[1], last_month_info[0], num_days_last_m)
            base_debug_params["calculated_semester_start_date"] = semester_start_d.isoformat()
            base_debug_params["calculated_semester_end_date"] = semester_end_d.isoformat()
        calendar_data["debug_params"] = base_debug_params

    # else: # Cas où view_type n'est pas géré, déjà couvert plus haut, calendar_data serait vide.
          # Mais on peut ajouter les debug_params de base même là.
          # if not calendar_data: calendar_data = {} # S'assurer que calendar_data existe.
          # calendar_data["debug_params"] = base_debug_params

    return jsonify(calendar_data)

def get_calendar_view_data_range(start_date, end_date):
    """Prépare les données calendrier pour une période. (Fonction existante, inchangée)"""
    logger.debug(f"Prep données calendrier: {start_date} -> {end_date}"); data = {'days': {}, 'vacations': [], 'holidays': []}
    try:
        # S'assurer que holiday_manager, weekly_planning, planning_exceptions sont accessibles
        # Elles sont globales dans ce module.
        curr = start_date
        while curr <= end_date:
            date_str = curr.strftime('%Y-%m-%d')
            # La fonction get_day_type_and_desc doit être robuste et retourner des valeurs même si HM n'est pas init.
            if holiday_manager:
                day_info = holiday_manager.get_day_type_and_desc(curr, weekly_planning, planning_exceptions)
            else:
                logger.error("HolidayManager non disponible dans get_calendar_view_data_range.")
                day_info = {"type": "ErreurHM", "description": "HolidayManager non initialisé", "schedule_name": None}

            data['days'][date_str] = {"type": day_info.get('type', 'Erreur'), "description": day_info.get('description', 'N/A')}
            curr += timedelta(days=1)

        if holiday_manager:
            # Ces informations sont-elles pertinentes pour une vue mensuelle seule ?
            # Pour l'instant, on les laisse, le front-end pourra les ignorer si besoin.
            data['vacations'] = holiday_manager.get_vacation_periods()
            data['holidays'] = [{"date": d.strftime('%Y-%m-%d'), "description": desc} for d, desc in holiday_manager.get_holidays()]
        else:
            logger.warning("HolidayManager non disponible pour récupérer vacances/fériés dans get_calendar_view_data_range.")

    except Exception as e:
        logger.error(f"Erreur get_calendar_view_data_range pour {start_date}-{end_date}: {e}", exc_info=True)
        data['error'] = str(e)

    logger.debug(f"Données calendrier prêtes ({len(data['days'])} jours).");
    return data

@app.route('/api/daily_schedule')
@login_required
@require_permission("page:view_control")
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
@require_permission("page:view_config_general")
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
            "sonnerie_fin_alerte": current_params.get("sonnerie_fin_alerte"),
            "nom_peripherique_audio_sonneries": current_params.get("nom_peripherique_audio_sonneries"),
            "alert_click_mode": current_params.get("alert_click_mode", "double"), # Ajout pour config_general
            "status_refresh_interval_seconds": current_params.get("status_refresh_interval_seconds", 15), # Ajout pour config_general
            "available_ringtones": available_ringtones
        }
        # api_holidays_url et country_code_holidays ne sont pas modifiables via cette page pour l'instant.

        logger.debug(f"Config générale, alertes et interactions renvoyée: {config_data_to_return}")
        return jsonify(config_data_to_return), 200

    except json.JSONDecodeError as e_json:
        logger.error(f"Erreur JSON lecture config pour API /api/config/general_and_alerts: {e_json}", exc_info=True)
        return jsonify({"error": "Erreur de format dans un fichier de configuration."}), 500
    except Exception as e:
        logger.error(f"Erreur API GET /api/config/general_and_alerts: {e}", exc_info=True)
        return jsonify({"error": f"Erreur serveur: {str(e)}"}), 500

@app.route('/api/config/general_and_alerts', methods=['POST'])
@login_required
@require_permission("config_general:edit_settings")
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

        # Ajout pour alert_click_mode
        if "alert_click_mode" in data_from_client:
            click_mode = data_from_client["alert_click_mode"]
            if click_mode in ["single", "double"]:
                current_params["alert_click_mode"] = click_mode
                logger.info(f"Mode de clic des alertes configuré à : {click_mode}")
            else:
                logger.warning(f"Valeur invalide pour alert_click_mode reçue : {click_mode}. Conservations de l'ancienne valeur ou du défaut.")
                # Optionnel: retourner une erreur au client si la valeur est invalide
                # return jsonify({"error": f"Valeur invalide pour alert_click_mode: {click_mode}"}), 400


        # Ajout pour status_refresh_interval_seconds
        if "status_refresh_interval_seconds" in data_from_client:
            try:
                interval = int(data_from_client["status_refresh_interval_seconds"])
                # Ajouter des bornes de validation si nécessaire, ex: if 5 <= interval <= 300:
                if interval >= 1: # Doit être au moins 1 seconde
                    current_params["status_refresh_interval_seconds"] = interval
                    logger.info(f"Intervalle de rafraîchissement du statut configuré à : {interval} secondes")
                else:
                    logger.warning(f"Valeur invalide pour status_refresh_interval_seconds reçue : {interval}. Doit être >= 1.")
            except ValueError:
                logger.warning(f"Valeur non entière reçue pour status_refresh_interval_seconds : {data_from_client['status_refresh_interval_seconds']}.")
                # Optionnel: retourner une erreur
                # return jsonify({"error": "status_refresh_interval_seconds doit être un entier."}), 400

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
# Utilisé pour la pré-écoute sur config_sounds et config_general
# On pourrait créer une permission "sound:preview" ou utiliser une des pages
@require_permission("page:view_config_sounds") # Ou une permission plus générique si besoin
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
@require_permission("page:view_config_weekly")
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
@require_permission("config_weekly:edit_planning")
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
@require_permission("page:view_config_day_types")
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
@require_permission("page:view_config_day_types")
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
@require_permission("day_type:create")
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
@require_permission("day_type:delete")
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
@require_permission("day_type:edit_periods") # Base permission
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
    # Vérification permission renommage si applicable
    if 'new_name' in data_from_client and data_from_client['new_name'].strip() != day_type_name_url:
        if not user_has_permission(current_user, "day_type:rename"):
            return permission_access_denied("day_type:rename")

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
@require_permission("page:view_config_exceptions")
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
@require_permission("exception:edit")
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
@require_permission("exception:delete")
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
@require_permission("exception:create")
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
@require_permission("sound:scan_folder")
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
@require_permission("sound:edit_display_name")
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

# Fonction pour DÉSASSOCIATION SEULE (garde le fichier physique)
@app.route('/api/config/sounds/<path:file_name>/dissociate_only', methods=['DELETE'])
@login_required
@require_permission("sound:disassociate")
def dissociate_sound_only(file_name):
    user_id = current_user.id
    logger.info(f"User '{user_id}' - DELETE /api/config/sounds/{file_name}/dissociate_only: Tentative de DÉSASSOCIATION seule.")

    donnees_path = os.path.join(CONFIG_PATH, DONNEES_SONNERIES_FILE)
    params_path = os.path.join(CONFIG_PATH, PARAMS_FILE)
    try:
        donnees_sonneries = {}
        if os.path.exists(donnees_path):
            with open(donnees_path, 'r', encoding='utf-8') as f:
                donnees_sonneries = json.load(f)
        else:
            logger.error(f"Fichier de configuration des sonneries {DONNEES_SONNERIES_FILE} non trouvé.")
            return jsonify({"error": "Fichier de configuration principal des sonneries manquant."}), 500
        if "sonneries" not in donnees_sonneries or not isinstance(donnees_sonneries["sonneries"], dict):
            logger.warning(f"Section 'sonneries' non trouvée ou invalide dans {DONNEES_SONNERIES_FILE}.")
            return jsonify({"error": f"Aucune sonnerie configurée. Impossible de trouver l'association pour '{file_name}'."}), 404
        display_name_to_delete = None
        for disp_name, f_name_in_config in donnees_sonneries["sonneries"].items():
            if f_name_in_config == file_name:
                display_name_to_delete = disp_name
                break
        if display_name_to_delete is None:
            logger.warning(f"Fichier son '{file_name}' non trouvé dans les associations de {DONNEES_SONNERIES_FILE}.")
            return jsonify({"error": f"L'association pour le fichier son '{file_name}' n'existe pas dans la configuration."}), 404

        # La suppression physique du fichier (os.remove) EST ABSENTE ICI

        del donnees_sonneries["sonneries"][display_name_to_delete]
        logger.info(f"Association pour '{file_name}' (nom convivial '{display_name_to_delete}') supprimée de la configuration. Le fichier physique est conservé.")

        # Nettoyage des références
        if "journees_types" in donnees_sonneries:
            for jt_name, jt_data in donnees_sonneries["journees_types"].items():
                if isinstance(jt_data.get("periodes"), list):
                    for periode in jt_data["periodes"]:
                        if periode.get("sonnerie_debut") == file_name:
                            periode["sonnerie_debut"] = None
                        if periode.get("sonnerie_fin") == file_name:
                            periode["sonnerie_fin"] = None
        params_modified = False
        current_college_params_on_disk = {}
        if os.path.exists(params_path):
            with open(params_path, 'r', encoding='utf-8') as f_params:
                current_college_params_on_disk = json.load(f_params)
            alert_keys_to_check = ["sonnerie_ppms", "sonnerie_attentat", "sonnerie_fin_alerte"]
            for key in alert_keys_to_check:
                if current_college_params_on_disk.get(key) == file_name:
                    current_college_params_on_disk[key] = None
                    params_modified = True
            if params_modified:
                with open(params_path, 'w', encoding='utf-8') as f_params:
                    json.dump(current_college_params_on_disk, f_params, indent=2, ensure_ascii=False)
                logger.info(f"Fichier des paramètres ({PARAMS_FILE}) mis à jour après nettoyage des références à '{file_name}'.")
                global college_params
                college_params = current_college_params_on_disk.copy()
        with open(donnees_path, 'w', encoding='utf-8') as f:
            json.dump(donnees_sonneries, f, indent=2, ensure_ascii=False)
        final_message = f"L'association pour la sonnerie '{display_name_to_delete}' (fichier: {file_name}) a été retirée de la configuration. Le fichier MP3 est conservé sur le disque."
        return jsonify({"message": final_message}), 200
    except Exception as e:
        logger.error(f"Erreur API DELETE .../{file_name}/dissociate_only: {e}", exc_info=True)
        return jsonify({"error": f"Erreur serveur inattendue: {str(e)}"}), 500

@app.route('/api/config/sounds/<path:file_name>', methods=['DELETE'])
@login_required
@require_permission("sound:delete_file")
def delete_sound_association_and_file(file_name):
    user_id = current_user.id
    logger.info(f"User '{user_id}' - DELETE /api/config/sounds/{file_name}: Tentative de suppression d'association ET du fichier physique.")

    donnees_path = os.path.join(CONFIG_PATH, DONNEES_SONNERIES_FILE)
    params_path = os.path.join(CONFIG_PATH, PARAMS_FILE)

    try:
        donnees_sonneries = {}
        if os.path.exists(donnees_path):
            with open(donnees_path, 'r', encoding='utf-8') as f:
                donnees_sonneries = json.load(f)
        else:
            logger.error(f"Fichier de configuration des sonneries {DONNEES_SONNERIES_FILE} non trouvé.")
            return jsonify({"error": "Fichier de configuration principal des sonneries manquant."}), 500

        if "sonneries" not in donnees_sonneries or not isinstance(donnees_sonneries["sonneries"], dict):
            logger.warning(f"Section 'sonneries' non trouvée ou invalide dans {DONNEES_SONNERIES_FILE}.")
            return jsonify({"error": f"Aucune sonnerie configurée. Impossible de trouver l'association pour '{file_name}'."}), 404

        display_name_to_delete = None
        for disp_name, f_name_in_config in donnees_sonneries["sonneries"].items():
            if f_name_in_config == file_name:
                display_name_to_delete = disp_name
                break

        if display_name_to_delete is None:
            logger.warning(f"Fichier son '{file_name}' non trouvé dans les associations de {DONNEES_SONNERIES_FILE}.")
            return jsonify({"error": f"L'association pour le fichier son '{file_name}' n'existe pas dans la configuration."}), 404

        file_deleted_physically = False
        action_on_physical_file_message = ""
        physical_file_path = os.path.join(MP3_PATH, file_name)

        if os.path.exists(physical_file_path):
            try:
                os.remove(physical_file_path)
                logger.info(f"Fichier MP3 '{physical_file_path}' supprimé physiquement avec succès.")
                file_deleted_physically = True
                action_on_physical_file_message = "Le fichier MP3 a également été supprimé du disque."
            except OSError as e_remove:
                logger.error(f"Erreur lors de la suppression physique du fichier '{physical_file_path}': {e_remove}", exc_info=True)
                action_on_physical_file_message = f"ATTENTION: Le fichier MP3 '{file_name}' n'a pas pu être supprimé du disque (Erreur: {e_remove.strerror}). L'association sera quand même retirée."
        else:
            logger.warning(f"Fichier MP3 '{physical_file_path}' non trouvé sur le disque pour suppression physique.")
            action_on_physical_file_message = "Le fichier MP3 n'a pas été trouvé sur le disque à l'emplacement attendu."

        del donnees_sonneries["sonneries"][display_name_to_delete]
        logger.info(f"Association pour '{file_name}' (nom convivial '{display_name_to_delete}') supprimée de la configuration.")

        if "journees_types" in donnees_sonneries:
            for jt_name, jt_data in donnees_sonneries["journees_types"].items():
                if isinstance(jt_data.get("periodes"), list):
                    for periode in jt_data["periodes"]:
                        if periode.get("sonnerie_debut") == file_name:
                            periode["sonnerie_debut"] = None
                        if periode.get("sonnerie_fin") == file_name:
                            periode["sonnerie_fin"] = None

        params_modified = False
        current_college_params_on_disk = {}
        if os.path.exists(params_path):
            with open(params_path, 'r', encoding='utf-8') as f_params:
                current_college_params_on_disk = json.load(f_params)
            alert_keys_to_check = ["sonnerie_ppms", "sonnerie_attentat", "sonnerie_fin_alerte"]
            for key in alert_keys_to_check:
                if current_college_params_on_disk.get(key) == file_name:
                    current_college_params_on_disk[key] = None
                    params_modified = True
            if params_modified:
                with open(params_path, 'w', encoding='utf-8') as f_params:
                    json.dump(current_college_params_on_disk, f_params, indent=2, ensure_ascii=False)
                logger.info(f"Fichier des paramètres ({PARAMS_FILE}) mis à jour après nettoyage des références à '{file_name}'.")
                global college_params
                college_params = current_college_params_on_disk.copy()

        with open(donnees_path, 'w', encoding='utf-8') as f:
            json.dump(donnees_sonneries, f, indent=2, ensure_ascii=False)

        final_message = f"L'association pour la sonnerie '{display_name_to_delete}' (fichier: {file_name}) a été retirée de la configuration."
        if action_on_physical_file_message:
            final_message += f" {action_on_physical_file_message}"

        return jsonify({"message": final_message}), 200

    except Exception as e:
        logger.error(f"Erreur API DELETE /api/config/sounds/{file_name}: {e}", exc_info=True)
        return jsonify({"error": f"Erreur serveur inattendue: {str(e)}"}), 500

# Route pour uploader un nouveau fichier son
@app.route('/api/config/sounds/upload', methods=['POST'])
@login_required
@require_permission("sound:upload")
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


# ==============================================================================
# Routes API pour la Gestion des Utilisateurs (CRUD)
# ==============================================================================

MIN_PASSWORD_LENGTH = 8
VALID_ROLES = [] # Will be populated after roles_config.json is loaded

@app.route('/api/users', methods=['GET'])
@login_required
@require_permission("user:view_list")
def get_users():
    """Retourne la liste de tous les utilisateurs avec leurs détails (sans hash)."""
    logger.info(f"User '{current_user.id}' (admin) requête GET /api/users.")
    users_list = []
    for username, data in users_data.items():
        custom_permissions = data.get("custom_permissions")
        # A user has custom permissions if the field exists, is a dict, and is not empty.
        has_custom_permissions_flag = bool(custom_permissions and isinstance(custom_permissions, dict) and len(custom_permissions) > 0)

        users_list.append({
            "username": username,
            "full_name": data.get("nom_complet", ""),
            "role": data.get("role", "lecteur"),
            "custom_permissions": custom_permissions,
            "has_custom_permissions": has_custom_permissions_flag
        })
    return jsonify(users_list), 200

@app.route('/api/users', methods=['POST'])
@login_required
@require_permission("user:create")
def create_user():
    """Crée un nouvel utilisateur."""
    logger.info(f"User '{current_user.id}' (admin) requête POST /api/users (create user).")
    data = request.get_json()

    if not data:
        return jsonify({"error": "Données JSON requises."}), 400

    username = data.get('username')
    password = data.get('password')
    full_name = data.get('full_name')
    role = data.get('role')

    if not all([username, password, full_name, role]):
        return jsonify({"error": "Champs manquants: username, password, full_name, et role sont requis."}), 400

    username = username.strip()
    if not username: # Après strip
        return jsonify({"error": "Le nom d'utilisateur ne peut pas être vide."}), 400

    if username in users_data:
        return jsonify({"error": f"Le nom d'utilisateur '{username}' existe déjà."}), 409 # Conflict

    if role not in VALID_ROLES:
        return jsonify({"error": f"Rôle invalide. Doit être l'un de: {', '.join(VALID_ROLES)}"}), 400

    if len(password) < MIN_PASSWORD_LENGTH:
        return jsonify({"error": f"Le mot de passe doit contenir au moins {MIN_PASSWORD_LENGTH} caractères."}), 400

    hashed_password = generate_password_hash(password)
    users_data[username] = {
        "hash": hashed_password,
        "nom_complet": full_name,
        "role": role
        # "permissions": data.get("permissions", []) # Ajout du champ permissions
    }

    if not save_users_data():
        # Revert in-memory change if save fails
        if username in users_data: del users_data[username]
        return jsonify({"error": "Erreur serveur lors de la sauvegarde des données utilisateur."}), 500

    logger.info(f"Utilisateur '{username}' créé avec succès par '{current_user.id}'.")
    return jsonify({
        "message": "Utilisateur créé avec succès.",
        "user": {
            "username": username,
            "full_name": full_name,
            "role": role
            # "permissions": users_data[username].get("permissions", [])
        }
    }), 201

@app.route('/api/users/<string:username_param>', methods=['PUT'])
@login_required
# Base permission, specific field changes will be checked inside
@require_permission("user:edit_details")
def update_user(username_param):
    """Met à jour un utilisateur existant."""
    logger.info(f"User '{current_user.id}' (admin) requête PUT /api/users/{username_param}.")

    if username_param not in users_data:
        return jsonify({"error": f"L'utilisateur '{username_param}' n'existe pas."}), 404

    data = request.get_json()
    if not data:
        return jsonify({"error": "Données JSON requises."}), 400

    user_to_update = users_data[username_param]
    updated_fields = {}

    if 'full_name' in data:
        new_full_name = data['full_name'].strip()
        if not new_full_name:
            return jsonify({"error": "Le nom complet ne peut pas être vide."}), 400
        user_to_update['nom_complet'] = new_full_name
        updated_fields['full_name'] = new_full_name
        logger.debug(f"User '{username_param}': nom_complet mis à jour.")

    if 'role' in data:
        new_role = data['role']
        if new_role not in VALID_ROLES:
            return jsonify({"error": f"Rôle invalide. Doit être l'un de: {', '.join(VALID_ROLES)}"}), 400

        # Logique de protection si l'admin essaie de se dégrader lui-même et qu'il est le seul admin
        if username_param == current_user.id and user_to_update.get('role') == "administrateur" and new_role != "administrateur":
            num_admins = sum(1 for u_data in users_data.values() if u_data.get('role') == "administrateur")
            if num_admins <= 1:
                logger.warning(f"Tentative par l'admin '{current_user.id}' de changer son propre rôle alors qu'il est le seul admin.")
                return jsonify({"error": "Impossible de changer le rôle du seul administrateur restant."}), 403

        user_to_update['role'] = new_role
        updated_fields['role'] = new_role
        logger.debug(f"User '{username_param}': role mis à jour.")
    elif 'role' in data and data['role'] == user_to_update.get('role'): # Role provided but not changed
        pass # No specific permission needed if role isn't actually changing

    if 'password' in data and data['password']: # Si le mot de passe est fourni et non vide
        if not user_has_permission(current_user, "user:edit_password"):
            return permission_access_denied("user:edit_password")
        new_password = data['password']
        if len(new_password) < MIN_PASSWORD_LENGTH:
            return jsonify({"error": f"Le nouveau mot de passe doit contenir au moins {MIN_PASSWORD_LENGTH} caractères."}), 400
        user_to_update['hash'] = generate_password_hash(new_password)
        logger.debug(f"User '{username_param}': mot de passe mis à jour.")
        # On ne met pas 'password' dans updated_fields

    if 'custom_permissions' in data:
        if not user_has_permission(current_user, "user:edit_custom_permissions"):
            logger.warning(f"User '{current_user.id}' lacks 'user:edit_custom_permissions' to modify custom_permissions for '{username_param}'.")
            return jsonify({"error": "Accès refusé: Permission 'user:edit_custom_permissions' requise."}), 403

        new_custom_permissions = data['custom_permissions']
        if new_custom_permissions is not None and not isinstance(new_custom_permissions, dict):
            return jsonify({"error": "Le champ 'custom_permissions' doit être un dictionnaire ou null."}), 400

        if new_custom_permissions is None or (isinstance(new_custom_permissions, dict) and not new_custom_permissions):
            if 'custom_permissions' in user_to_update:
                del user_to_update['custom_permissions']
            logger.info(f"Custom permissions cleared for user '{username_param}' by '{current_user.id}'.")
            updated_fields['custom_permissions_cleared'] = True # Indicate a change was made
        else:
            user_to_update['custom_permissions'] = new_custom_permissions
            logger.info(f"Custom permissions updated for user '{username_param}' by '{current_user.id}'.")
            updated_fields['custom_permissions_updated'] = True # Indicate a change was made

    if not updated_fields and not ('password' in data and data['password']):
         return jsonify({"message": "Aucune donnée à mettre à jour fournie."}), 200


    if not save_users_data():
        # Potentiellement, ici, on pourrait vouloir recharger users_data pour annuler les modifs en mémoire.
        # Pour l'instant, on signale juste l'erreur.
        load_users() # Recharge pour annuler les modifications en mémoire
        return jsonify({"error": "Erreur serveur lors de la sauvegarde des données utilisateur."}), 500

    logger.info(f"Utilisateur '{username_param}' mis à jour avec succès par '{current_user.id}'. Champs modifiés: {list(updated_fields.keys())}")
    return jsonify({
        "message": "Utilisateur mis à jour avec succès.",
        "user": {
            "username": username_param,
            "full_name": user_to_update.get('nom_complet'),
            "role": user_to_update.get('role')
            # "permissions": user_to_update.get('permissions', []) # This line is removed
        }
    }), 200

@app.route('/api/users/<string:username_param>', methods=['DELETE'])
@login_required
@require_permission("user:delete")
def delete_user(username_param):
    """Supprime un utilisateur."""
    logger.info(f"User '{current_user.id}' (admin) requête DELETE /api/users/{username_param}.")

    if username_param not in users_data:
        return jsonify({"error": f"L'utilisateur '{username_param}' n'existe pas."}), 404

    user_to_delete_role = users_data[username_param].get('role')

    # Logique de protection: empêcher la suppression du seul admin, surtout si c'est soi-même
    if user_to_delete_role == "administrateur":
        num_admins = sum(1 for u_data in users_data.values() if u_data.get('role') == "administrateur")
        if num_admins <= 1:
            # Si c'est le seul admin, on ne peut pas le supprimer, qu'il essaie de se supprimer lui-même ou un autre admin (ce qui ne devrait pas arriver s'il est seul).
            logger.warning(f"Tentative de suppression du dernier administrateur '{username_param}' par '{current_user.id}'.")
            return jsonify({"error": "Impossible de supprimer le seul compte administrateur restant."}), 403 # Forbidden

    # Empêcher un utilisateur de se supprimer lui-même (même s'il y a d'autres admins)
    # Cette règle est parfois souhaitée. Si on veut permettre à un admin de se supprimer tant qu'il n'est pas le dernier,
    # il faudrait affiner la condition ci-dessus.
    # Pour l'instant, la logique ci-dessus (dernier admin) couvre le cas où current_user est le dernier admin.
    # Si current_user est admin mais pas le dernier, il peut se supprimer.
    # Si l'on veut interdire TOUTE auto-suppression pour un admin, ajouter:
    # if username_param == current_user.id:
    #    return jsonify({"error": "Les administrateurs ne peuvent pas supprimer leur propre compte directement via cette API."}), 403


    del users_data[username_param]

    if not save_users_data():
        load_users() # Recharge pour annuler la suppression en mémoire
        return jsonify({"error": "Erreur serveur lors de la sauvegarde des données utilisateur."}), 500

    logger.info(f"Utilisateur '{username_param}' supprimé avec succès par '{current_user.id}'.")
    return jsonify({"message": f"Utilisateur '{username_param}' supprimé avec succès."}), 200 # Ou 204 No Content

@app.route('/api/users/<string:username_param>/custom_permissions', methods=['DELETE'])
@login_required
@require_permission("user:edit_custom_permissions") # Use the new permission
def delete_user_custom_permissions(username_param):
    logger.info(f"User '{current_user.id}' attempting to delete custom permissions for user '{username_param}'.")

    if username_param not in users_data:
        return jsonify({"error": f"L'utilisateur '{username_param}' n'existe pas."}), 404

    user_to_update = users_data[username_param]
    if 'custom_permissions' in user_to_update:
        del user_to_update['custom_permissions']
        if save_users_data():
            logger.info(f"Custom permissions for user '{username_param}' deleted successfully by '{current_user.id}'.")
            return jsonify({"message": "Permissions personnalisées supprimées avec succès. L'utilisateur utilisera les permissions de son rôle."}), 200
        else:
            logger.error(f"Failed to save users.json after attempting to delete custom_permissions for {username_param}")
            return jsonify({"error": "Erreur serveur lors de la sauvegarde des données utilisateur."}), 500
    else:
        logger.info(f"No custom permissions to delete for user '{username_param}'.")
        return jsonify({"message": "Aucune permission personnalisée à supprimer pour cet utilisateur."}), 200

# ==============================================================================
# Routes API pour la Gestion des Rôles et Permissions
# ==============================================================================

@app.route('/api/roles_config', methods=['GET'])
@login_required
@require_permission("user_management:edit_role_permissions") # Protégé par la nouvelle permission
def get_roles_config():
    """
    Récupère la configuration actuelle des rôles et le modèle des permissions disponibles.
    """
    user_id = current_user.id
    logger.info(f"User '{user_id}' requête GET /api/roles_config.")

    # roles_config_data est déjà chargé globalement
    # PERMISSIONS_MODEL est importé depuis constants.py

    return jsonify({
        "roles_config": roles_config_data,
        "permissions_model": PERMISSIONS_MODEL
    }), 200

@app.route('/api/roles_config/<string:role_name>', methods=['PUT'])
@login_required
@require_permission("user_management:edit_role_permissions") # Protégé par la nouvelle permission
def update_role_permissions(role_name):
    """
    Met à jour les permissions pour un rôle spécifique.
    """
    user_id = current_user.id
    logger.info(f"User '{user_id}' requête PUT /api/roles_config/{role_name}.")

    # Pour la sécurité, interdire la modification du rôle Administrateur pour le moment
    # et aussi de rôles non prévus (même si la validation ci-dessous le ferait aussi)
    if role_name not in ["Collaborateur", "Lecteur", "collaborateur", "lecteur"]: # Accepter minuscules aussi
        logger.warning(f"User '{user_id}': Tentative de modification du rôle '{role_name}' non autorisée via API.")
        return jsonify({"error": f"La modification du rôle '{role_name}' n'est pas autorisée via cette API."}), 403

    # S'assurer que le rôle existe dans la configuration actuelle
    # Normaliser le nom du rôle (première lettre majuscule) comme dans roles_config.json
    normalized_role_name = role_name.capitalize()
    if normalized_role_name not in roles_config_data.get("roles", {}):
        logger.warning(f"User '{user_id}': Tentative de modification d'un rôle inexistant '{normalized_role_name}'.")
        return jsonify({"error": f"Le rôle '{normalized_role_name}' n'existe pas."}), 404

    new_permissions_for_role = request.get_json()
    if not isinstance(new_permissions_for_role, dict):
        logger.warning(f"User '{user_id}': Données JSON invalides pour la mise à jour du rôle '{normalized_role_name}'. Attendu: dict.")
        return jsonify({"error": "Format de données invalide. Un dictionnaire de permissions est attendu."}), 400

    # Mettre à jour les permissions pour le rôle spécifié
    # Il est important de ne pas écraser d'autres métadonnées du rôle si elles existent (ex: "description")
    if "permissions" in roles_config_data["roles"][normalized_role_name]:
        roles_config_data["roles"][normalized_role_name]["permissions"] = new_permissions_for_role
        logger.info(f"Permissions pour le rôle '{normalized_role_name}' mises à jour par user '{user_id}'.")
    else:
        # Ce cas ne devrait pas arriver si roles_config.json est bien structuré
        logger.error(f"Structure inattendue pour le rôle '{normalized_role_name}' dans roles_config_data: clé 'permissions' manquante.")
        return jsonify({"error": "Erreur de configuration interne du serveur pour ce rôle."}), 500

    # Sauvegarder la configuration des rôles mise à jour
    if not save_roles_config():
        # En cas d'échec de sauvegarde, il serait prudent de recharger l'ancienne config pour éviter une désynchronisation
        load_roles_config() # Recharge l'ancienne version depuis le disque
        logger.error(f"Échec de la sauvegarde de roles_config.json après modification du rôle '{normalized_role_name}'. Les modifications ont été annulées en mémoire.")
        return jsonify({"error": "Erreur serveur lors de la sauvegarde de la configuration des rôles."}), 500

    logger.info(f"Configuration des rôles sauvegardée avec succès après mise à jour du rôle '{normalized_role_name}'.")
    return jsonify({
        "message": f"Permissions pour le rôle '{normalized_role_name}' mises à jour avec succès.",
        "role_name": normalized_role_name,
        "updated_permissions": new_permissions_for_role
    }), 200


        # --- FIN NOUVELLES ROUTES POUR LA CONFIGURATION WEB ---
        # --- FIN ROUTES API POUR LA GESTION DES UTILISATEURS ---
        # --- FIN ROUTES API POUR LA GESTION DES RÔLES ---

# Retirer l'ancienne infrastructure de rôles
# ROLES_HIERARCHIE, role_access_denied, require_role,
# lecteur_required, collaborateur_required, admin_required

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