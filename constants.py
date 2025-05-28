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
ROLES_CONFIG_FILE = "roles_config.json"

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

# --- Permissions Granulaires ---
AVAILABLE_PERMISSIONS = [
    # Permissions Générales / Accès aux Pages Principales
    "page:view_control",
    "page:view_config_general",
    "page:view_config_weekly",
    "page:view_config_day_types",
    "page:view_config_exceptions",
    "page:view_config_sounds",
    "page:view_config_users",

    # Permissions pour la Page de Contrôle (control.html)
    "control:scheduler_activate",
    "control:scheduler_deactivate",
    "control:config_reload",
    "control:alert_trigger_ppms",
    "control:alert_trigger_attentat",
    "control:alert_trigger_any", # Permission générale pour déclencher une alerte
    "control:alert_stop",
    "control:alert_end",

    # Permissions pour la Configuration Générale (config_general.html)
    "config_general:edit_settings",
    "config_general:edit_alert_sounds",

    # Permissions pour la Configuration Hebdomadaire (config_weekly.html)
    "config_weekly:edit_planning",

    # Permissions pour les Journées Types (config_day_types.html)
    "day_type:create",
    "day_type:rename",
    "day_type:delete",
    "day_type:edit_periods", # Gère l'ajout, modification, suppression de périodes

    # Permissions pour les Exceptions (config_exceptions.html)
    "exception:create",
    "exception:edit",
    "exception:delete",

    # Permissions pour la Gestion des Sonneries (config_sounds.html)
    "sound:upload",
    "sound:scan_folder",
    "sound:edit_display_name",
    "sound:disassociate",
    "sound:delete_file",
    "sound:preview",

    # Permissions pour la Gestion des Utilisateurs (config_users.html)
    # Note: page:view_config_users contrôle l'accès à la page elle-même.
    "user:view_list", # Pour voir la liste des utilisateurs (API GET /api/users)
    "user:create",
    "user:edit_details", # nom_complet, etc. (hors rôle et permissions)
    "user:edit_password",
    "user:edit_role", # Changer le rôle de base d'un utilisateur
    "user:edit_permissions", # Modifier la liste fine des permissions d'un utilisateur
    "user:delete",

    # Permission spéciale pour les administrateurs
    "admin:has_all_permissions",

    # Nouvelle permission pour la gestion des permissions des rôles
    "user_management:edit_role_permissions"
]
# --- FIN Permissions Granulaires ---

# --- Noms Conviviaux pour les Permissions (pour affichage UI) ---
FRIENDLY_PERMISSION_NAMES = {
    "page:view_control": "Voir la page de Contrôle Principal",
    "page:view_config_general": "Voir la page de configuration Générale et Alertes",
    "page:view_config_weekly": "Voir la page de configuration du Planning Hebdomadaire",
    "page:view_config_day_types": "Voir la page de configuration des Journées Types",
    "page:view_config_exceptions": "Voir la page de configuration des Exceptions de planning",
    "page:view_config_sounds": "Voir la page de configuration des Sonneries",
    "page:view_config_users": "Voir la page de Gestion des Utilisateurs",

    "control:view_status": "Voir le statut détaillé du système",
    "control:view_calendar": "Voir le calendrier scolaire et les types de jours",
    "control:view_day_details": "Voir les détails d'une journée spécifique du calendrier",

    "control:scheduler_activate": "Activer le planning des sonneries",
    "control:scheduler_deactivate": "Désactiver le planning des sonneries",
    "control:config_reload": "Recharger la configuration du serveur",
    "control:alert_trigger_ppms": "Déclencher l'alerte PPMS",
    "control:alert_trigger_attentat": "Déclencher l'alerte Attentat",
    "control:alert_trigger_any": "Déclencher une alerte sonore (générique)",
    "control:alert_stop": "Arrêter l'alerte sonore en cours",
    "control:alert_end": "Déclencher le son de fin d'alerte",

    "config_general:edit_settings": "Modifier les paramètres généraux (département, API vacances, etc.)",
    "config_general:edit_alert_sounds": "Modifier les sonneries d'alerte (PPMS, attentat, fin d'alerte)",

    "config_weekly:edit_planning": "Modifier le planning hebdomadaire (assigner journées types)",

    "day_type:create": "Créer de nouvelles journées types",
    "day_type:rename": "Renommer des journées types existantes",
    "day_type:delete": "Supprimer des journées types",
    "day_type:edit_periods": "Modifier les périodes (horaires, sonneries) d'une journée type",

    "exception:create": "Créer des exceptions au planning (jours fériés, événements)",
    "exception:edit": "Modifier des exceptions de planning existantes",
    "exception:delete": "Supprimer des exceptions de planning",

    "sound:upload": "Uploader de nouveaux fichiers son",
    "sound:scan_folder": "Scanner le dossier MP3 pour de nouveaux sons",
    "sound:edit_display_name": "Modifier le nom d'affichage des sonneries",
    "sound:disassociate": "Désassocier une sonnerie (garder le fichier)",
    "sound:delete_file": "Supprimer les fichiers son du disque et leur association",
    "sound:preview": "Pré-écouter les sonneries",

    "user:view_list": "Voir la liste des utilisateurs",
    "user:create": "Créer de nouveaux utilisateurs",
    "user:edit_details": "Modifier les détails d'un utilisateur (nom complet)",
    "user:edit_password": "Modifier le mot de passe d'un utilisateur",
    "user:edit_role": "Modifier le rôle d'un utilisateur",
    "user:edit_permissions": "Modifier les permissions spécifiques d'un utilisateur", # Cette permission sera probablement retirée ou renommée
    "user:delete": "Supprimer des utilisateurs",

    "user_management:edit_role_permissions": "Modifier les permissions associées à chaque rôle"
}
# --- FIN Noms Conviviaux ---

# --- Modèle de Permissions pour l'UI de Configuration des Rôles ---
# Ce modèle aide à structurer l'affichage des permissions dans l'interface utilisateur.
PERMISSIONS_MODEL = {
    "control_panel": {
        "label": "Panneau de Contrôle",
        "page_view_meta": {
            "key": "page:view_control",
            "label": FRIENDLY_PERMISSION_NAMES.get("page:view_control")
        },
        "functional_permissions": {
            "control:view_status": FRIENDLY_PERMISSION_NAMES.get("control:view_status"),
            "control:view_calendar": FRIENDLY_PERMISSION_NAMES.get("control:view_calendar"),
            "control:view_day_details": FRIENDLY_PERMISSION_NAMES.get("control:view_day_details"),
            "control:scheduler_activate": FRIENDLY_PERMISSION_NAMES.get("control:scheduler_activate"),
            "control:scheduler_deactivate": FRIENDLY_PERMISSION_NAMES.get("control:scheduler_deactivate"),
            "control:config_reload": FRIENDLY_PERMISSION_NAMES.get("control:config_reload"),
            "control:alert_trigger_ppms": FRIENDLY_PERMISSION_NAMES.get("control:alert_trigger_ppms"),
            "control:alert_trigger_attentat": FRIENDLY_PERMISSION_NAMES.get("control:alert_trigger_attentat"),
            "control:alert_trigger_any": FRIENDLY_PERMISSION_NAMES.get("control:alert_trigger_any"),
            "control:alert_stop": FRIENDLY_PERMISSION_NAMES.get("control:alert_stop"),
            "control:alert_end": FRIENDLY_PERMISSION_NAMES.get("control:alert_end"),
        }
    },
    "general_config": {
        "label": "Configuration Générale et Alertes",
        "page_view_meta": {
            "key": "page:view_config_general",
            "label": FRIENDLY_PERMISSION_NAMES.get("page:view_config_general")
        },
        "functional_permissions": {
            "config_general:edit_settings": FRIENDLY_PERMISSION_NAMES.get("config_general:edit_settings"),
            "config_general:edit_alert_sounds": FRIENDLY_PERMISSION_NAMES.get("config_general:edit_alert_sounds"),
        }
    },
    "weekly_planning_config": {
        "label": "Configuration Planning Hebdomadaire",
        "page_view_meta": {
            "key": "page:view_config_weekly",
            "label": FRIENDLY_PERMISSION_NAMES.get("page:view_config_weekly")
        },
        "functional_permissions": {
            "config_weekly:edit_planning": FRIENDLY_PERMISSION_NAMES.get("config_weekly:edit_planning"),
        }
    },
    "day_types_config": {
        "label": "Configuration Journées Types",
        "page_view_meta": {
            "key": "page:view_config_day_types",
            "label": FRIENDLY_PERMISSION_NAMES.get("page:view_config_day_types")
        },
        "functional_permissions": {
            "day_type:create": FRIENDLY_PERMISSION_NAMES.get("day_type:create"),
            "day_type:rename": FRIENDLY_PERMISSION_NAMES.get("day_type:rename"),
            "day_type:delete": FRIENDLY_PERMISSION_NAMES.get("day_type:delete"),
            "day_type:edit_periods": FRIENDLY_PERMISSION_NAMES.get("day_type:edit_periods"),
        }
    },
    "exceptions_config": {
        "label": "Configuration Exceptions de Planning",
        "page_view_meta": {
            "key": "page:view_config_exceptions",
            "label": FRIENDLY_PERMISSION_NAMES.get("page:view_config_exceptions")
        },
        "functional_permissions": {
            "exception:create": FRIENDLY_PERMISSION_NAMES.get("exception:create"),
            "exception:edit": FRIENDLY_PERMISSION_NAMES.get("exception:edit"),
            "exception:delete": FRIENDLY_PERMISSION_NAMES.get("exception:delete"),
        }
    },
    "sounds_config": {
        "label": "Gestion des Sonneries",
        "page_view_meta": {
            "key": "page:view_config_sounds",
            "label": FRIENDLY_PERMISSION_NAMES.get("page:view_config_sounds")
        },
        "functional_permissions": {
            "sound:upload": FRIENDLY_PERMISSION_NAMES.get("sound:upload"),
            "sound:scan_folder": FRIENDLY_PERMISSION_NAMES.get("sound:scan_folder"),
            "sound:edit_display_name": FRIENDLY_PERMISSION_NAMES.get("sound:edit_display_name"),
            "sound:disassociate": FRIENDLY_PERMISSION_NAMES.get("sound:disassociate"),
            "sound:delete_file": FRIENDLY_PERMISSION_NAMES.get("sound:delete_file"),
            "sound:preview": FRIENDLY_PERMISSION_NAMES.get("sound:preview")
        }
    },
    "user_management": {
        "label": "Gestion Utilisateurs et Rôles",
        "page_view_meta": {
            "key": "page:view_config_users",
            "label": FRIENDLY_PERMISSION_NAMES.get("page:view_config_users")
        },
        "functional_permissions": {
            "user:view_list": FRIENDLY_PERMISSION_NAMES.get("user:view_list"),
            "user:create": FRIENDLY_PERMISSION_NAMES.get("user:create"),
            "user:edit_details": FRIENDLY_PERMISSION_NAMES.get("user:edit_details"),
            "user:edit_password": FRIENDLY_PERMISSION_NAMES.get("user:edit_password"),
            "user:edit_role": FRIENDLY_PERMISSION_NAMES.get("user:edit_role"),
            "user:delete": FRIENDLY_PERMISSION_NAMES.get("user:delete"),
            "user_management:edit_role_permissions": FRIENDLY_PERMISSION_NAMES.get("user_management:edit_role_permissions"),
        }
    },
    "special_permissions": { # Permissions spéciales (onglet "Avancé" ou "Spécial")
        "label": "Permissions Spéciales",
        "permissions": { # This section remains unchanged
            "admin:has_all_permissions": FRIENDLY_PERMISSION_NAMES.get("admin:has_all_permissions", "Accès total administrateur (non modifiable)"),
        }
    }
}
# --- FIN Modèle de Permissions ---


# --- Permissions par Défaut pour chaque Rôle ---
# Ces listes sont maintenant obsolètes car les permissions sont définies dans roles_config.json
# Elles sont conservées ici temporairement pour référence ou si une logique de fallback devait être réintroduite.
# Il est recommandé de supprimer cette constante une fois le nouveau système de rôles pleinement validé.
DEFAULT_ROLE_PERMISSIONS = {
    "lecteur": [
        "page:view_control",
        "page:view_config_general", # Souvent utile de voir la config audio/alertes
        # "control:alert_trigger_ppms", # Commenté suite à demande utilisateur
        # "control:alert_trigger_attentat",
        # "control:alert_stop",
        # "control:alert_end",
    ],
    "collaborateur": [
        "page:view_control",
        "page:view_config_general",
        "page:view_config_weekly",
        "page:view_config_day_types",
        "page:view_config_exceptions",
        "page:view_config_sounds",
        # Accès aux actions de la page de contrôle (sauf reload)
        "control:scheduler_activate",
        "control:scheduler_deactivate",
        "control:alert_trigger_ppms",
        "control:alert_trigger_attentat",
        "control:alert_stop",
        "control:alert_end",
        # Accès à la modification des configurations
        "config_general:edit_settings",
        "config_general:edit_alert_sounds",
        "config_weekly:edit_planning",
        "day_type:create",
        "day_type:rename",
        "day_type:delete",
        "day_type:edit_periods",
        "exception:create",
        "exception:edit",
        "exception:delete",
        "sound:upload",
        "sound:scan_folder",
        "sound:edit_display_name",
        "sound:disassociate",
        "sound:delete_file",
    ],
    "administrateur": [
        "admin:has_all_permissions" # L'admin a toutes les permissions via cette permission spéciale
    ]
}
# --- FIN Permissions par Défaut ---

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