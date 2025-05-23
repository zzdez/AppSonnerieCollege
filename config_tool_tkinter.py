# config_tool_tkinter.py
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import json
import os
import sys
import requests
from werkzeug.security import generate_password_hash, check_password_hash # check_password_hash n'est pas utilisé ici mais peut l'être pour valider ancien mdp
from datetime import datetime
import logging

# --- Setup Logging (Simple pour l'outil) ---
# Ce logger est spécifique à cet outil et affiche les messages DEBUG dans la console
# pour aider au suivi de l'exécution de l'interface graphique.
tool_logger = logging.getLogger('ConfigTool')
tool_logger.setLevel(logging.DEBUG)
tool_handler = logging.StreamHandler(sys.stdout)
tool_formatter = logging.Formatter('%(asctime)s - DEBUG [%(name)s]: %(message)s')
tool_handler.setFormatter(tool_formatter)
if not tool_logger.handlers:
    tool_logger.addHandler(tool_handler)
tool_logger.propagate = False
# --- Fin Logging ---

# --- Import des constantes et gestion des erreurs ---
# Essentiel pour récupérer les chemins et noms de fichiers définis de manière centralisée.
try:
    import constants
    from constants import (
        CONFIG_PATH, MP3_PATH, DONNEES_SONNERIES_FILE, PARAMS_FILE, USERS_FILE,
        LISTE_DEPARTEMENTS, DEPARTEMENTS_ZONES,
        JOURS_SEMAINE_PLANNING, JOURS_SEMAINE_ASSIGNATION, AUCUNE_SONNERIE
    )
    # Vérification critique que les chemins principaux sont définis
    if not CONFIG_PATH or not MP3_PATH:
        raise ImportError("CONFIG_PATH ou MP3_PATH non définis dans constants.py.")
    CONSTANTS_LOADED = True
except ImportError as e:
    tool_logger.critical(f"ERREUR CRITIQUE import constantes: {e}")
    # Tenter d'afficher une messagebox même si tkinter n'est pas totalement initialisé
    try:
        root_check = tk.Tk(); root_check.withdraw()
        messagebox.showerror("Erreur Critique", f"Impossible charger constantes: {e}\nApplication arrêtée.")
        root_check.destroy()
    except Exception: print(f"ERREUR CRITIQUE: Impossible charger constantes: {e}")
    CONSTANTS_LOADED = False
    sys.exit(1) # Arrêt immédiat si les constantes ne peuvent pas être chargées

# --- Définition des Chemins Absolus vers les fichiers de configuration ---
# Utilise les constantes importées pour construire les chemins complets.
SONNERIES_FILE_PATH = os.path.join(CONFIG_PATH, DONNEES_SONNERIES_FILE)
PARAMS_FILE_PATH = os.path.join(CONFIG_PATH, PARAMS_FILE)
USERS_FILE_PATH = os.path.join(CONFIG_PATH, USERS_FILE)
# Note : Le chemin vers le dossier MP3 est directement constants.MP3_PATH

# --- Configuration pour la Notification du Backend ---
# Adresse et port où le service backend (backend_server.py) écoute. À adapter si besoin.
BACKEND_HOST = "192.168.1.15" # Doit correspondre à l'IP/nom du serveur backend
BACKEND_RELOAD_URL = f"http://{BACKEND_HOST}:5000/api/config/reload"
# Identifiants pour s'authentifier auprès de l'API /api/config/reload si elle est protégée.
# ATTENTION: Stocker le mot de passe en clair ici n'est pas idéal pour la sécurité.
BACKEND_USER = "admin" # Un utilisateur défini dans users.json du backend
BACKEND_PASS = "motdepasse" # Le mot de passe correspondant


# ==============================================================================
# Classe Principale de l'Application Tkinter
# ==============================================================================
class ConfigApp(tk.Tk):
    """
    Application Tkinter pour configurer les paramètres des sonneries.
    Permet de gérer les fichiers MP3, les journées types, le planning hebdomadaire,
    les exceptions, les paramètres généraux et les utilisateurs web.
    Lit et écrit les fichiers de configuration JSON sur un emplacement partagé.
    """
    def __init__(self):
        """Initialise l'application, les variables et crée l'interface."""
        super().__init__()
        tool_logger.debug("Initialisation fenêtre principale...")
        self.title("Outil de Configuration des Sonneries")
        self.geometry("900x700") # Taille initiale fenêtre

        # --- Structures de données internes pour la configuration ---
        # Ces dictionnaires sont peuplés au chargement et mis à jour par l'UI
        self.sonneries_disponibles = {} # Format: { "Nom Affiché Sonnerie": "nom_fichier.mp3" }
        self.journees_types = {}        # Format: { "Nom JT": {"nom": "...", "periodes": [...] } }
        self.planning_hebdomadaire = {} # Format: { "Lundi": "Nom JT", ... }
        self.exceptions_planning = {}   # Format: { "YYYY-MM-DD": {"action": ..., "journee_type": ..., "description": ...} }
        self.parametres_college = {}    # Contenu de parametres_college.json
        self.users = {}                 # Format: { "username": "hash_password" }

        # --- Variables Tkinter pour lier les widgets aux données ---
        self.selected_journee_type = tk.StringVar() # Nom de la JT sélectionnée dans la liste
        self.selected_sonnerie_nom = tk.StringVar() # Nom affiché de la sonnerie sélectionnée
        self.selected_periode_index = tk.IntVar(value=-1) # Index de la période sélectionnée
        self.selected_exception_date = tk.StringVar() # Date de l'exception sélectionnée
        self.selected_user = tk.StringVar() # Nom de l'utilisateur sélectionné

        # --- Style ttk ---
        self.style = ttk.Style(self)
        self.style.theme_use('clam') # Thème visuel

        tool_logger.debug("Variables initialisées.")
        self._create_widgets() # Création de tous les onglets et widgets
        tool_logger.debug("Widgets créés.")

        # Vérifier accès aux dossiers essentiels AVANT de charger/initialiser l'UI
        if self.check_config_dir_access():
            tool_logger.debug("Chargement configuration initiale...")
            self.load_all_config() # Charger les données depuis les fichiers JSON
            tool_logger.debug("Initialisation UI avec données chargées...")
            self.initialize_ui_with_data() # Remplir les widgets avec les données
            self.activate_notify_button() # Activer le bouton Notifier si URL définie
            tool_logger.debug("Initialisation terminée.")
        else:
             # Si accès impossible, afficher erreur et fermer
             messagebox.showerror("Erreur Accès", f"Impossible d'accéder à:\nCONFIG: {CONFIG_PATH}\nMP3: {MP3_PATH}\nVérifiez chemins et permissions.")
             self.destroy()

    def check_config_dir_access(self):
        """Vérifie l'existence et les permissions R/W sur CONFIG_PATH et R sur MP3_PATH."""
        tool_logger.debug(f"Vérif accès CONFIG_PATH: '{CONFIG_PATH}'")
        config_ok = False
        try:
             # Teste si c'est un dossier, si on peut lire et écrire dedans
             config_ok = os.path.isdir(CONFIG_PATH) and os.access(CONFIG_PATH, os.R_OK | os.W_OK)
        except Exception as e: tool_logger.error(f"Erreur accès CONFIG_PATH: {e}")
        if not config_ok: tool_logger.error(f"Accès CONFIG_PATH échoué."); return False

        tool_logger.debug(f"Vérif accès MP3_PATH: '{MP3_PATH}'")
        mp3_ok = False
        try:
             # Teste si c'est un dossier et si on peut le lire
             mp3_ok = os.path.isdir(MP3_PATH) and os.access(MP3_PATH, os.R_OK)
        except Exception as e: tool_logger.error(f"Erreur accès MP3_PATH: {e}")
        if not mp3_ok: tool_logger.warning(f"Accès MP3_PATH échoué."); messagebox.showwarning("Accès MP3", f"Dossier MP3 ('{MP3_PATH}') inaccessible. Gestion sonneries limitée.")

        tool_logger.debug(f"Accès CONFIG OK, Accès MP3 {'OK' if mp3_ok else 'Échoué (Warning)'}.")
        return True # On continue même si MP3 non accessible pour l'instant

    def _create_widgets(self):
        """Crée le Notebook (onglets) et appelle les fonctions de création pour chaque onglet."""
        tool_logger.debug("Création interface (Notebook et onglets)...")
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(pady=10, padx=10, expand=True, fill="both")

        # --- Création des Frames pour chaque onglet ---
        self.tab_horaires = ttk.Frame(self.notebook)
        self.tab_exceptions = ttk.Frame(self.notebook)
        self.tab_sonneries = ttk.Frame(self.notebook)
        self.tab_parametres = ttk.Frame(self.notebook)
        self.tab_users = ttk.Frame(self.notebook)

        # --- Ajout des onglets au Notebook ---
        self.notebook.add(self.tab_horaires, text=" Horaires / Journées Types ") # Espaces pour padding
        self.notebook.add(self.tab_exceptions, text=" Exceptions Planning ")
        self.notebook.add(self.tab_sonneries, text=" Sonneries MP3 ")
        self.notebook.add(self.tab_parametres, text=" Paramètres Collège ")
        self.notebook.add(self.tab_users, text=" Utilisateurs Web ")

        # --- Appel des fonctions pour remplir chaque onglet ---
        self._create_tab_horaires()
        self._create_tab_exceptions()
        self._create_tab_sonneries()
        self._create_tab_parametres()
        self._create_tab_users()

        # --- Création des boutons globaux (Sauvegarder, Notifier) ---
        # Placer dans une frame séparée en bas de la fenêtre principale
        bottom_button_frame = ttk.Frame(self)
        bottom_button_frame.pack(pady=10, fill='x', padx=10)

        # Bouton Sauvegarder toujours visible
        save_button = ttk.Button(bottom_button_frame, text="Sauvegarder Tout", command=self.save_all_config, style='Accent.TButton') # Style optionnel
        save_button.pack(side=tk.LEFT, padx=(0, 10))

        # Bouton Notifier (initialement désactivé, activé dans activate_notify_button)
        self.notify_button = ttk.Button(bottom_button_frame, text="Notifier Backend (Recharger Config)", command=self.notify_backend, state=tk.DISABLED)
        self.notify_button.pack(side=tk.LEFT)

        # Ajouter un style pour le bouton sauvegarder (optionnel)
        self.style.configure('Accent.TButton', font=('TkDefaultFont', 10, 'bold'), background='#28a745', foreground='white')
        # Note: Le style ttk peut dépendre du thème et de l'OS.

    # ==================================================
    # Onglet Horaires / Journées Types : Création UI
    # ==================================================
    def _create_tab_horaires(self):
        """Crée les widgets pour l'onglet de gestion des horaires."""
        tool_logger.debug("Création onglet Horaires...")
        frame = self.tab_horaires
        frame.columnconfigure(1, weight=1) # Colonne droite (périodes) extensible
        frame.rowconfigure(0, weight=1)    # Ligne du haut extensible (pour liste périodes)

        # --- Panneau Gauche ---
        left_panel = ttk.Frame(frame)
        left_panel.grid(row=0, column=0, rowspan=2, padx=10, pady=10, sticky="ns")

        # --- Section Journées Types ---
        jt_frame = ttk.LabelFrame(left_panel, text="Journées Types")
        jt_frame.pack(pady=(0, 10), fill="y", expand=True)
        ttk.Label(jt_frame, text="Liste:").pack(pady=(5,2))
        # Listbox et Scrollbar
        jt_list_frame = ttk.Frame(jt_frame) # Frame pour listbox + scrollbar
        jt_list_frame.pack(fill="both", expand=True, padx=5, pady=(0,5))
        self.jt_listbox = tk.Listbox(jt_list_frame, exportselection=False, width=30) # Hauteur gérée par pack/expand
        sb_jt = ttk.Scrollbar(jt_list_frame, orient=tk.VERTICAL, command=self.jt_listbox.yview)
        self.jt_listbox['yscrollcommand'] = sb_jt.set
        sb_jt.pack(side=tk.RIGHT, fill=tk.Y) # Scrollbar à droite
        self.jt_listbox.pack(side=tk.LEFT, fill="both", expand=True) # Listbox prend l'espace restant
        self.jt_listbox.bind("<<ListboxSelect>>", self.on_jt_select)
        # Boutons de gestion JT
        jt_button_frame = ttk.Frame(jt_frame); jt_button_frame.pack(pady=5)
        ttk.Button(jt_button_frame, text="Ajouter", command=self.ajouter_jt).pack(side=tk.LEFT, padx=5)
        ttk.Button(jt_button_frame, text="Supprimer", command=self.supprimer_jt).pack(side=tk.LEFT, padx=5)
        ttk.Button(jt_button_frame, text="Renommer", command=self.renommer_jt).pack(side=tk.LEFT, padx=5)

        # --- Section Planning Hebdomadaire ---
        hebdo_frame = ttk.LabelFrame(left_panel, text="Planning Hebdomadaire")
        hebdo_frame.pack(pady=10, fill="x") # Prend la largeur, hauteur fixe
        self.hebdo_combos = {}
        for i, jour in enumerate(JOURS_SEMAINE_ASSIGNATION): # Utilise la liste de constants
             ttk.Label(hebdo_frame, text=f"{jour}:").grid(row=i, column=0, padx=5, pady=3, sticky="w")
             combo = ttk.Combobox(hebdo_frame, values=[AUCUNE_SONNERIE], state="readonly", width=25)
             combo.grid(row=i, column=1, padx=5, pady=3, sticky="ew")
             self.hebdo_combos[jour] = combo
        hebdo_frame.columnconfigure(1, weight=1)

        # --- Panneau Droite (Périodes) ---
        periodes_frame = ttk.LabelFrame(frame, text="Périodes de la Journée Type sélectionnée")
        periodes_frame.grid(row=0, column=1, rowspan=2, padx=10, pady=10, sticky="nsew")
        periodes_frame.columnconfigure(0, weight=1); periodes_frame.rowconfigure(0, weight=3); periodes_frame.rowconfigure(1, weight=1) # Configuration Grid

        # Listbox et Scrollbar Périodes
        periode_list_frame = ttk.Frame(periodes_frame)
        periode_list_frame.grid(row=0, column=0, padx=5, pady=5, sticky="nsew")
        periode_list_frame.columnconfigure(0, weight=1); periode_list_frame.rowconfigure(0, weight=1)
        self.periodes_listbox = tk.Listbox(periode_list_frame, exportselection=False)
        sb_periodes = ttk.Scrollbar(periode_list_frame, orient=tk.VERTICAL, command=self.periodes_listbox.yview)
        self.periodes_listbox['yscrollcommand'] = sb_periodes.set
        sb_periodes.pack(side=tk.RIGHT, fill=tk.Y)
        self.periodes_listbox.pack(side=tk.LEFT, fill="both", expand=True)
        self.periodes_listbox.bind("<<ListboxSelect>>", self.on_periode_select)

        # Formulaire Détails Période
        form_periode = ttk.LabelFrame(periodes_frame, text="Détails Période")
        form_periode.grid(row=1, column=0, padx=5, pady=5, sticky="ew")
        form_periode.columnconfigure(1, weight=1); form_periode.columnconfigure(3, weight=1)
        # Labels et Entries/Combos pour nom, début, fin, sonneries
        ttk.Label(form_periode, text="Nom Période:").grid(row=0, column=0, padx=5, pady=3, sticky="w")
        self.periode_nom_entry = ttk.Entry(form_periode); self.periode_nom_entry.grid(row=0, column=1, columnspan=3, padx=5, pady=3, sticky="ew")
        ttk.Label(form_periode, text="Début (HH:MM:SS):").grid(row=1, column=0, padx=5, pady=3, sticky="w")
        self.periode_debut_entry = ttk.Entry(form_periode, width=10); self.periode_debut_entry.grid(row=1, column=1, padx=5, pady=3, sticky="w")
        ttk.Label(form_periode, text="Fin (HH:MM:SS):").grid(row=2, column=0, padx=5, pady=3, sticky="w")
        self.periode_fin_entry = ttk.Entry(form_periode, width=10); self.periode_fin_entry.grid(row=2, column=1, padx=5, pady=3, sticky="w")
        ttk.Label(form_periode, text="Sonnerie Début:").grid(row=1, column=2, padx=(15, 5), pady=3, sticky="w")
        self.periode_sond_combo = ttk.Combobox(form_periode, values=[AUCUNE_SONNERIE], state="readonly"); self.periode_sond_combo.grid(row=1, column=3, padx=5, pady=3, sticky="ew")
        ttk.Label(form_periode, text="Sonnerie Fin:").grid(row=2, column=2, padx=(15, 5), pady=3, sticky="w")
        self.periode_sonf_combo = ttk.Combobox(form_periode, values=[AUCUNE_SONNERIE], state="readonly"); self.periode_sonf_combo.grid(row=2, column=3, padx=5, pady=3, sticky="ew")

        # Boutons Gestion Périodes
        periode_button_frame = ttk.Frame(periodes_frame); periode_button_frame.grid(row=2, column=0, pady=10)
        ttk.Button(periode_button_frame, text="Ajouter Période", command=self.ajouter_periode).pack(side=tk.LEFT, padx=5)
        ttk.Button(periode_button_frame, text="Modifier Période", command=self.modifier_periode).pack(side=tk.LEFT, padx=5)
        ttk.Button(periode_button_frame, text="Supprimer Période", command=self.supprimer_periode).pack(side=tk.LEFT, padx=5)
        ttk.Button(periode_button_frame, text="Effacer Formulaire", command=self.vider_formulaire_periode).pack(side=tk.LEFT, padx=5)

        tool_logger.debug("Onglet Horaires créé.")

    # ==================================================
    # Onglet Exceptions Planning : Création UI
    # ==================================================
    def _create_tab_exceptions(self):
        """Crée les widgets pour l'onglet de gestion des exceptions."""
        tool_logger.debug("Création onglet Exceptions Planning...")
        frame = self.tab_exceptions
        frame.columnconfigure(1, weight=1); frame.rowconfigure(0, weight=1)

        # --- Liste Exceptions (Gauche) ---
        exc_list_frame = ttk.LabelFrame(frame, text="Exceptions Définies")
        exc_list_frame.grid(row=0, column=0, padx=10, pady=10, sticky="ns")
        exc_list_frame.rowconfigure(0, weight=1) # Donne hauteur à la listbox
        # Listbox et Scrollbar
        exc_list_subframe = ttk.Frame(exc_list_frame)
        exc_list_subframe.grid(row=0, column=0, padx=5, pady=5, sticky="ns")
        exc_list_subframe.rowconfigure(0, weight=1); exc_list_subframe.columnconfigure(0, weight=1)
        self.exc_listbox = tk.Listbox(exc_list_subframe, exportselection=False, width=40)
        sb_exc = ttk.Scrollbar(exc_list_subframe, orient=tk.VERTICAL, command=self.exc_listbox.yview)
        self.exc_listbox['yscrollcommand'] = sb_exc.set
        sb_exc.pack(side=tk.RIGHT, fill=tk.Y)
        self.exc_listbox.pack(side=tk.LEFT, fill="both", expand=True)
        self.exc_listbox.bind("<<ListboxSelect>>", self.on_exception_select)
        # Bouton Supprimer
        exc_list_button = ttk.Button(exc_list_frame, text="Supprimer Sélection", command=self.supprimer_exception)
        exc_list_button.grid(row=1, column=0, pady=5)

        # --- Formulaire Ajout/Modif (Droite) ---
        exc_form_frame = ttk.LabelFrame(frame, text="Ajouter / Modifier une Exception")
        exc_form_frame.grid(row=0, column=1, padx=10, pady=10, sticky="nsew")
        exc_form_frame.columnconfigure(1, weight=1)
        # Widgets du formulaire (Date, Action, JT, Description, Bouton)
        ttk.Label(exc_form_frame, text="Date (YYYY-MM-DD):").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.exc_date_entry = ttk.Entry(exc_form_frame, width=15); self.exc_date_entry.grid(row=0, column=1, columnspan=2, padx=5, pady=5, sticky="w")
        ttk.Label(exc_form_frame, text="Action:").grid(row=1, column=0, padx=5, pady=5, sticky="w")
        self.exc_action_var = tk.StringVar(value="silence"); action_radio_frame = ttk.Frame(exc_form_frame); action_radio_frame.grid(row=1, column=1, columnspan=2, padx=5, pady=2, sticky="w")
        action_radio_silence = ttk.Radiobutton(action_radio_frame, text="Silence", variable=self.exc_action_var, value="silence", command=self.on_action_change)
        action_radio_jt = ttk.Radiobutton(action_radio_frame, text="Utiliser Journée Type", variable=self.exc_action_var, value="utiliser_jt", command=self.on_action_change)
        action_radio_silence.pack(side=tk.LEFT, padx=(0, 10)); action_radio_jt.pack(side=tk.LEFT)
        self.exc_jt_label = ttk.Label(exc_form_frame, text="Journée Type:"); self.exc_jt_label.grid(row=2, column=0, padx=5, pady=5, sticky="w")
        self.exc_jt_combo = ttk.Combobox(exc_form_frame, values=[AUCUNE_SONNERIE], state="disabled"); self.exc_jt_combo.grid(row=2, column=1, columnspan=2, padx=5, pady=5, sticky="ew")
        ttk.Label(exc_form_frame, text="Description (Optionnel):").grid(row=3, column=0, padx=5, pady=5, sticky="w")
        self.exc_desc_entry = ttk.Entry(exc_form_frame); self.exc_desc_entry.grid(row=3, column=1, columnspan=2, padx=5, pady=5, sticky="ew")
        exc_form_button = ttk.Button(exc_form_frame, text="Ajouter / Modifier Exception", command=self.ajouter_ou_modifier_exception); exc_form_button.grid(row=4, column=0, columnspan=3, pady=15)

    # ==================================================
    # Onglet Sonneries MP3 : Création UI
    # ==================================================
    def _create_tab_sonneries(self):
        """Crée les widgets pour l'onglet de gestion des fichiers MP3."""
        frame = self.tab_sonneries
        frame.columnconfigure(0, weight=1); frame.rowconfigure(0, weight=1)

        son_frame = ttk.LabelFrame(frame, text="Gestion des Fichiers Sonneries MP3")
        son_frame.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        son_frame.columnconfigure(0, weight=1); son_frame.rowconfigure(1, weight=1)

        # Afficher le chemin MP3 attendu
        ttk.Label(son_frame, text=f"Fichiers MP3 attendus dans :\n{MP3_PATH}", justify=tk.LEFT).grid(row=0, column=0, columnspan=2, padx=5, pady=(5,10), sticky="w")

        # Listbox et Scrollbar
        son_list_frame = ttk.Frame(son_frame)
        son_list_frame.grid(row=1, column=0, padx=5, pady=5, sticky="nsew")
        son_list_frame.columnconfigure(0, weight=1); son_list_frame.rowconfigure(0, weight=1)
        self.son_listbox = tk.Listbox(son_list_frame, width=60)
        sb_son = ttk.Scrollbar(son_list_frame, orient=tk.VERTICAL, command=self.son_listbox.yview)
        self.son_listbox['yscrollcommand'] = sb_son.set
        sb_son.pack(side=tk.RIGHT, fill=tk.Y)
        self.son_listbox.pack(side=tk.LEFT, fill="both", expand=True)
        # Ajouter fonctionnalité "Parcourir" pour ajouter serait bien, mais complexe (copie fichier etc.)

        # Boutons
        button_frame = ttk.Frame(son_frame); button_frame.grid(row=2, column=0, pady=10)
        ttk.Button(button_frame, text="Rafraîchir Liste Fichiers", command=self.rafraichir_liste_sonneries).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Supprimer de la Config", command=self.supprimer_sonnerie_config).pack(side=tk.LEFT, padx=5)
        # ttk.Button(button_frame, text="Pré-écouter", state=tk.DISABLED).pack(side=tk.LEFT, padx=5) # Fonctionnalité non implémentée

    # ==================================================
    # Onglet Paramètres Collège : Création UI
    # ==================================================
    def _create_tab_parametres(self):
        """Crée les widgets pour l'onglet des paramètres généraux."""
        frame = self.tab_parametres
        frame.columnconfigure(0, weight=1) # Permet aux frames de s'étendre

        # Section Paramètres Généraux
        param_frame = ttk.LabelFrame(frame, text="Paramètres Généraux"); param_frame.grid(row=0, column=0, padx=10, pady=10, sticky="ew"); param_frame.columnconfigure(1, weight=1)
        ttk.Label(param_frame, text="Département:").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.dept_combo = ttk.Combobox(param_frame, values=LISTE_DEPARTEMENTS, state="readonly"); self.dept_combo.grid(row=0, column=1, padx=5, pady=5, sticky="ew"); self.dept_combo.bind("<<ComboboxSelected>>", self.on_dept_select)
        ttk.Label(param_frame, text="Zone Vacances:").grid(row=1, column=0, padx=5, pady=5, sticky="w")
        self.zone_label = ttk.Label(param_frame, text="N/A", width=5, anchor="w"); self.zone_label.grid(row=1, column=1, padx=5, pady=5, sticky="w")
        ttk.Label(param_frame, text="URL API Jours Fériés:").grid(row=2, column=0, padx=5, pady=5, sticky="w")
        self.api_url_entry = ttk.Entry(param_frame); self.api_url_entry.grid(row=2, column=1, padx=5, pady=5, sticky="ew"); self.api_url_entry.insert(0, "https://date.nager.at/api/v3/PublicHolidays")
        ttk.Label(param_frame, text="Code Pays (API):").grid(row=3, column=0, padx=5, pady=5, sticky="w")
        self.country_code_entry = ttk.Entry(param_frame, width=5); self.country_code_entry.grid(row=3, column=1, padx=5, pady=5, sticky="w"); self.country_code_entry.insert(0, "FR")
        ttk.Label(param_frame, text="URL Base ICS Vacances (Manuel):").grid(row=4, column=0, padx=5, pady=5, sticky="w")
        self.manual_ics_url_entry = ttk.Entry(param_frame); self.manual_ics_url_entry.grid(row=4, column=1, padx=5, pady=5, sticky="ew")
        ttk.Label(param_frame, text="(Optionnel, surcharge URL par défaut)", foreground="grey", font=('TkDefaultFont', 8)).grid(row=5, column=1, padx=5, sticky="w")

        # Fichier ICS Local (Fallback)
        ics_frame = ttk.LabelFrame(frame, text="Fichier ICS Local (Utilisé si téléchargement échoue)"); ics_frame.grid(row=1, column=0, padx=10, pady=10, sticky="ew"); ics_frame.columnconfigure(1, weight=1)
        ttk.Label(ics_frame, text="Chemin Fichier ICS:").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.ics_path_entry = ttk.Entry(ics_frame); self.ics_path_entry.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        ttk.Button(ics_frame, text="Parcourir...", command=self.parcourir_ics).grid(row=0, column=2, padx=5, pady=5)
        self.ics_info_label = ttk.Label(ics_frame, text="Info chargement vacances...", justify=tk.LEFT, wraplength=500); self.ics_info_label.grid(row=1, column=0, columnspan=3, padx=5, pady=5, sticky="w")

        # Sonneries d'Alerte
        alert_frame = ttk.LabelFrame(frame, text="Sonneries d'Alerte Spéciales"); alert_frame.grid(row=2, column=0, padx=10, pady=10, sticky="ew"); alert_frame.columnconfigure(1, weight=1)
        ttk.Label(alert_frame, text="Sonnerie PPMS:").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.ppms_combo = ttk.Combobox(alert_frame, values=[AUCUNE_SONNERIE], state="readonly"); self.ppms_combo.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        ttk.Label(alert_frame, text="Sonnerie Alerte Attentat:").grid(row=1, column=0, padx=5, pady=5, sticky="w")
        self.attentat_combo = ttk.Combobox(alert_frame, values=[AUCUNE_SONNERIE], state="readonly"); self.attentat_combo.grid(row=1, column=1, padx=5, pady=5, sticky="ew")
        ttk.Label(alert_frame, text="Sonnerie Fin d'Alerte:").grid(row=2, column=0, padx=5, pady=5, sticky="w")
        self.fin_alerte_combo = ttk.Combobox(alert_frame, values=[AUCUNE_SONNERIE], state="readonly")
        self.fin_alerte_combo.grid(row=2, column=1, padx=5, pady=5, sticky="ew")

    # ==================================================
    # Onglet Utilisateurs Web : Création UI
    # ==================================================
    def _create_tab_users(self):
        """Crée les widgets pour l'onglet de gestion des utilisateurs."""
        tool_logger.debug("Création onglet Utilisateurs...")
        frame = self.tab_users
        frame.columnconfigure(1, weight=1); frame.rowconfigure(0, weight=1)

        # --- Liste Utilisateurs (Gauche) ---
        user_list_frame = ttk.LabelFrame(frame, text="Utilisateurs Autorisés (Web)")
        user_list_frame.grid(row=0, column=0, padx=10, pady=10, sticky="ns"); user_list_frame.rowconfigure(0, weight=1)
        # Listbox et Scrollbar
        user_list_subframe = ttk.Frame(user_list_frame)
        user_list_subframe.grid(row=0, column=0, sticky="ns"); user_list_subframe.rowconfigure(0, weight=1); user_list_subframe.columnconfigure(0, weight=1)
        self.user_listbox = tk.Listbox(user_list_subframe, exportselection=False, width=30)
        sb_users = ttk.Scrollbar(user_list_subframe, orient=tk.VERTICAL, command=self.user_listbox.yview); self.user_listbox['yscrollcommand'] = sb_users.set
        sb_users.pack(side=tk.RIGHT, fill=tk.Y); self.user_listbox.pack(side=tk.LEFT, fill="both", expand=True)
        self.user_listbox.bind("<<ListboxSelect>>", self.on_user_select); self.user_listbox.bind("<Button-1>", lambda e: self.check_listbox_empty_click(e, self.user_listbox, self.clear_user_form))
        # Bouton Supprimer
        user_list_button = ttk.Button(user_list_frame, text="Supprimer Utilisateur", command=self.supprimer_user); user_list_button.grid(row=1, column=0, pady=5)

        # --- Formulaire Ajout/Modif (Droite) ---
        user_form_frame = ttk.LabelFrame(frame, text="Ajouter / Modifier Utilisateur")
        user_form_frame.grid(row=0, column=1, padx=10, pady=10, sticky="nsew"); user_form_frame.columnconfigure(1, weight=1)
        # Widgets formulaire
        ttk.Label(user_form_frame, text="Nom d'utilisateur:").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.user_entry = ttk.Entry(user_form_frame); self.user_entry.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        ttk.Label(user_form_frame, text="Mot de passe:").grid(row=1, column=0, padx=5, pady=5, sticky="w")
        self.pass_entry = ttk.Entry(user_form_frame, show="*"); self.pass_entry.grid(row=1, column=1, padx=5, pady=5, sticky="ew")
        ttk.Label(user_form_frame, text="Confirmer Mot de passe:").grid(row=2, column=0, padx=5, pady=5, sticky="w")
        self.pass_confirm_entry = ttk.Entry(user_form_frame, show="*"); self.pass_confirm_entry.grid(row=2, column=1, padx=5, pady=5, sticky="ew")
        ttk.Label(user_form_frame, text="(Laissez vide pour ne pas changer)").grid(row=3, column=1, padx=5, pady=0, sticky="w", columnspan=1)
        user_form_button = ttk.Button(user_form_frame, text="Ajouter / Modifier Mot de passe", command=self.ajouter_ou_modifier_user); user_form_button.grid(row=4, column=0, columnspan=2, pady=15)


    def activate_notify_button(self):
        """Active le bouton Notifier si l'URL est définie."""
        if BACKEND_RELOAD_URL: tool_logger.debug("Activation bouton Notifier."); self.notify_button.config(state=tk.NORMAL)
        else: tool_logger.debug("Désactivation bouton Notifier (URL non définie)."); self.notify_button.config(state=tk.DISABLED)


    # ==================================================
    # Fonctions Logiques (Chargement, Sauvegarde, Callbacks UI)
    # ==================================================

    # --- Chargement ---
    def load_all_config(self):
        """Charge toutes les configurations depuis les fichiers JSON."""
        self.load_sonneries_config()
        self.load_parametres()
        self.load_users()

    def load_sonneries_config(self):
        """Charge donnees_sonneries.json."""
        tool_logger.debug(f"Chargement sonneries depuis {SONNERIES_FILE_PATH}")
        try:
            with open(SONNERIES_FILE_PATH, 'r', encoding='utf-8') as f: data = json.load(f)
            self.sonneries_disponibles = data.get("sonneries", {}); self.journees_types = data.get("journees_types", {})
            self.planning_hebdomadaire = data.get("planning_hebdomadaire", {}); self.exceptions_planning = data.get("exceptions_planning", {})
            tool_logger.debug(f"{len(self.sonneries_disponibles)} sonneries, {len(self.journees_types)} JT, {len(self.exceptions_planning)} exceptions chargées.")
        except FileNotFoundError:
            tool_logger.warning(f"{SONNERIES_FILE_PATH} non trouvé. Initialisation config vide.")
            self.sonneries_disponibles = {}; self.journees_types = {}; self.exceptions_planning = {}
            self.planning_hebdomadaire = {j: AUCUNE_SONNERIE for j in JOURS_SEMAINE_ASSIGNATION}
        except json.JSONDecodeError: tool_logger.error(f"Erreur JSON dans {SONNERIES_FILE_PATH}."); messagebox.showerror("Erreur Fichier", f"{DONNEES_SONNERIES_FILE} corrompu.")
        except Exception as e: tool_logger.error(f"Erreur chargement {SONNERIES_FILE_PATH}: {e}", exc_info=True); messagebox.showerror("Erreur", f"Erreur chargement {DONNEES_SONNERIES_FILE}:\n{e}")

    def load_parametres(self):
        """Charge parametres_college.json."""
        tool_logger.debug(f"Chargement paramètres depuis {PARAMS_FILE_PATH}")
        try:
            with open(PARAMS_FILE_PATH, 'r', encoding='utf-8') as f: self.parametres_college = json.load(f)
            tool_logger.debug(f"Paramètres chargés: {self.parametres_college}")
        except FileNotFoundError: tool_logger.warning(f"{PARAMS_FILE_PATH} non trouvé. Défauts appliqués."); self.parametres_college = {}
        except json.JSONDecodeError: tool_logger.error(f"Erreur JSON dans {PARAMS_FILE_PATH}."); messagebox.showerror("Erreur Fichier", f"{PARAMS_FILE} corrompu."); self.parametres_college = {}
        except Exception as e: tool_logger.error(f"Erreur chargement {PARAMS_FILE_PATH}: {e}", exc_info=True); messagebox.showerror("Erreur", f"Erreur chargement {PARAMS_FILE}:\n{e}")

    def load_users(self):
        """Charge users.json."""
        tool_logger.debug(f"Chargement users depuis {USERS_FILE_PATH}")
        try:
            with open(USERS_FILE_PATH, 'r', encoding='utf-8') as f: self.users = json.load(f)
            tool_logger.debug(f"{len(self.users)} users chargés.")
        except FileNotFoundError: tool_logger.warning(f"{USERS_FILE_PATH} non trouvé."); self.users = {}
        except json.JSONDecodeError: tool_logger.error(f"Erreur JSON dans {USERS_FILE_PATH}."); messagebox.showerror("Erreur Fichier", f"{USERS_FILE} corrompu."); self.users = {}
        except Exception as e: tool_logger.error(f"Erreur chargement {USERS_FILE_PATH}: {e}", exc_info=True); messagebox.showerror("Erreur", f"Erreur chargement {USERS_FILE}:\n{e}")

    # --- Initialisation UI ---
    def initialize_ui_with_data(self):
        """Met à jour tous les widgets de l'interface avec les données chargées depuis les fichiers JSON."""
        tool_logger.debug("Initialisation de l'UI avec les données chargées...")

        # --- Onglet Paramètres ---
        tool_logger.debug("...Init Paramètres...")
        # Département et Zone (lecture depuis self.parametres_college)
        default_dept = LISTE_DEPARTEMENTS[0] if LISTE_DEPARTEMENTS else "" # Valeur par défaut si liste vide
        dept = self.parametres_college.get("departement", default_dept)
        # S'assurer que la valeur chargée est bien dans les options du combo
        if dept not in LISTE_DEPARTEMENTS and LISTE_DEPARTEMENTS:
            tool_logger.warning(f"Département '{dept}' chargé invalide, utilisation du premier de la liste.")
            dept = LISTE_DEPARTEMENTS[0]
        elif not LISTE_DEPARTEMENTS:
             tool_logger.warning("Aucun département disponible.")
             dept = "" # Mettre à vide si pas d'options
        self.dept_combo.set(dept)
        self.on_dept_select() # Mettre à jour le label Zone

        # API Jours Fériés
        self.api_url_entry.delete(0, tk.END)
        api_url = self.parametres_college.get("api_holidays_url", "https://date.nager.at/api/v3/PublicHolidays")
        self.api_url_entry.insert(0, api_url)

        self.country_code_entry.delete(0, tk.END)
        country_code = self.parametres_college.get("country_code_holidays", "FR")
        self.country_code_entry.insert(0, country_code)

        # URL ICS Manuelle (avec correction pour None)
        self.manual_ics_url_entry.delete(0, tk.END)
        manual_url = self.parametres_college.get("vacances_ics_base_url_manuel") # Peut être None
        self.manual_ics_url_entry.insert(0, str(manual_url) if manual_url is not None else "") # Assurer chaîne

        # Chemin ICS Local
        self.ics_path_entry.delete(0, tk.END)
        # Note: get_ics_path_from_config() lit le fichier donnees_sonneries.json
        # Il serait plus cohérent de lire self.sonneries_data si on le stockait globalement,
        # mais la relecture simple du fichier est OK pour l'instant.
        ics_path = self.get_ics_path_from_config() # Fonction helper qui lit le fichier
        self.ics_path_entry.insert(0, ics_path or "") # or "" gère None et chaîne vide

        # Mettre à jour le label d'information ICS
        self.afficher_dates_info()

        # --- Onglet Horaires / JT ---
        tool_logger.debug("...Init Horaires...")
        # 1. Mettre à jour les options des combos de sonneries PARTOUT
        self.update_sonneries_combos() # Important de le faire avant de setter les valeurs
        # 2. Peupler la liste des Journées Types
        self.update_jt_listbox()
        # 3. Mettre à jour les combos du Planning Hebdomadaire
        self.update_assignation_hebdo_combos()
        # 4. Vider le formulaire d'édition de période
        self.vider_formulaire_periode()

        # --- Onglet Utilisateurs ---
        tool_logger.debug("...Init Utilisateurs...")
        self.update_user_listbox()
        self.clear_user_form() # Assurer que le formulaire est vide au début

        # --- Onglet Exceptions ---
        tool_logger.debug("...Init Exceptions...")
        # Le combo JT dans exceptions est mis à jour par update_jt_listbox via update_exception_jt_combo
        self.update_exception_listbox() # Afficher les exceptions chargées
        self.clear_exception_form() # Assurer formulaire vide

        # --- Onglet Sonneries ---
        tool_logger.debug("...Init Sonneries...")
        # Rafraîchir la liste depuis le dossier MP3
        # (Cela met aussi à jour self.sonneries_disponibles qui est utilisé par update_sonneries_combos)
        # Il faut donc appeler update_sonneries_combos APRES rafraichir_liste_sonneries
        self.rafraichir_liste_sonneries()
        # Rappeler update_sonneries_combos pour être sûr que les combos ont la liste à jour
        self.update_sonneries_combos()

        # Charger spécifiquement la valeur pour fin_alerte_combo (les autres sont faits dans update_sonneries_combos)
        fin_alerte_current_file = self.parametres_college.get("sonnerie_fin_alerte", "")
        fin_alerte_display = self.find_sonnerie_display_name(fin_alerte_current_file)
        self.fin_alerte_combo.set(fin_alerte_display if fin_alerte_display else AUCUNE_SONNERIE)

        tool_logger.debug("Initialisation UI vraiment terminée.") # Nouveau log final  
        

    def get_ics_path_from_config(self):
        """Lit le chemin ICS depuis la config sonneries (fichier JSON)."""
        try:
            with open(SONNERIES_FILE_PATH, 'r', encoding='utf-8') as f: data = json.load(f)
            return data.get("vacances", {}).get("ics_file_path", "") # Retourne "" si non trouvé
        except (FileNotFoundError, json.JSONDecodeError): return "" # Cas où le fichier n'existe pas ou est corrompu
        except Exception as e: tool_logger.error(f"Erreur lecture chemin ICS: {e}"); return ""

    # --- Sauvegarde ---
    def save_all_config(self):
        """Prépare les données et les sauvegarde dans les fichiers JSON."""
        tool_logger.info("Sauvegarde configuration...");
        if not self.check_config_dir_access(): messagebox.showerror("Erreur Sauvegarde", f"Accès {CONFIG_PATH} impossible."); return

        # Préparer data pour donnees_sonneries.json
        donnees_sonneries_data = {
            "sonneries": self.sonneries_disponibles, "journees_types": self.journees_types,
            "planning_hebdomadaire": self.get_current_hebdo_config(), "exceptions_planning": self.exceptions_planning,
            "vacances": { "ics_file_path": self.ics_path_entry.get().strip() or None }
        }
        # Préparer data pour parametres_college.json
        ppms_name = self.ppms_combo.get(); ppms_file = self.sonneries_disponibles.get(ppms_name) if ppms_name != AUCUNE_SONNERIE else ""
        attentat_name = self.attentat_combo.get(); attentat_file = self.sonneries_disponibles.get(attentat_name) if attentat_name != AUCUNE_SONNERIE else ""
        parametres_data = {
            "departement": self.dept_combo.get(), "zone": DEPARTEMENTS_ZONES.get(self.dept_combo.get(), ""),
            "api_holidays_url": self.api_url_entry.get().strip(), "country_code_holidays": self.country_code_entry.get().strip().upper(),
            "vacances_ics_base_url_manuel": self.manual_ics_url_entry.get().strip() or None,
            "sonnerie_ppms": ppms_file or "", "sonnerie_attentat": attentat_file or ""
        }
        # Préparer data pour users.json (c'est juste self.users)
        users_data = self.users

        # Écriture fichiers
        try:
            tool_logger.debug(f"Write {SONNERIES_FILE_PATH}");
            with open(SONNERIES_FILE_PATH, 'w', encoding='utf-8') as f: json.dump(donnees_sonneries_data, f, ensure_ascii=False, indent=2)
            tool_logger.debug(f"Write {PARAMS_FILE_PATH}");
            with open(PARAMS_FILE_PATH, 'w', encoding='utf-8') as f: json.dump(parametres_data, f, ensure_ascii=False, indent=2)
            tool_logger.debug(f"Write {USERS_FILE_PATH}");
            with open(USERS_FILE_PATH, 'w', encoding='utf-8') as f: json.dump(users_data, f, ensure_ascii=False, indent=2)
            messagebox.showinfo("Sauvegarde", "Configuration sauvegardée avec succès."); tool_logger.info("Configuration sauvegardée.")
            self.notify_backend() # Notifier après sauvegarde réussie
        except Exception as e:
            tool_logger.error(f"Erreur écriture sauvegarde: {e}", exc_info=True); messagebox.showerror("Erreur Sauvegarde", f"Impossible d'écrire config.\nVérifiez permissions sur {CONFIG_PATH}\nErreur: {e}")

    def notify_backend(self):
        """Tente d'envoyer une requête POST au backend pour recharger la config."""
        tool_logger.info(f"Tentative de notification du backend à {BACKEND_RELOAD_URL}")
        if not BACKEND_RELOAD_URL:
            tool_logger.warning("URL de notification backend non configurée.")
            # Pas besoin d'afficher de message à l'utilisateur ici
            return

        try:
            auth = None
            if BACKEND_USER and BACKEND_PASS:
                 auth = (BACKEND_USER, BACKEND_PASS)
            response = requests.post(BACKEND_RELOAD_URL, timeout=5, auth=auth)
            response.raise_for_status()
            if response.status_code == 200:
                 messagebox.showinfo("Notification Backend", "Le backend a été notifié avec succès pour recharger la configuration.")
                 tool_logger.info("Notification backend réussie.")
            else:
                 messagebox.showwarning("Notification Backend", f"Le backend a répondu avec un statut inattendu: {response.status_code}")
                 tool_logger.warning(f"Réponse inattendue du backend: {response.status_code} - {response.text}")
        except requests.exceptions.ConnectionError:
            tool_logger.error(f"Impossible de se connecter au backend à {BACKEND_RELOAD_URL}. Est-il démarré?")
            messagebox.showwarning("Notification Backend", "Impossible de joindre le serveur backend.\nAssurez-vous qu'il est démarré et que l'adresse est correcte.")
        except requests.exceptions.Timeout:
             tool_logger.error(f"Timeout lors de la connexion au backend.")
             messagebox.showwarning("Notification Backend", "Le serveur backend n'a pas répondu à temps (timeout).")
        except requests.exceptions.HTTPError as e:
             tool_logger.error(f"Erreur HTTP du backend: {e.response.status_code} - {e.response.text}")
             msg = f"Erreur du backend: {e.response.status_code}"
             if e.response.status_code == 401: msg += "\n(Erreur d'authentification)"
             else:
                 try: msg += f"\nDétail: {e.response.json().get('error', e.response.text)}"
                 except: msg += f"\nDétail: {e.response.text}"
             messagebox.showerror("Notification Backend", msg)
        except Exception as e:
            tool_logger.error(f"Erreur inattendue lors de la notification backend: {e}", exc_info=True)
            messagebox.showerror("Notification Backend", f"Erreur inattendue lors de la notification:\n{e}")

    # --- Fonctions spécifiques aux onglets (Indentations vérifiées) ---

    def update_jt_listbox(self):
        tool_logger.debug("màj listbox journées...")
        self.jt_listbox.delete(0, tk.END)
        sorted_jts = sorted(self.journees_types.keys())
        for jt_name in sorted_jts: self.jt_listbox.insert(tk.END, jt_name)
        tool_logger.debug(f"Listbox màj: {sorted_jts}")
        self.update_exception_jt_combo()

    def on_jt_select(self, event=None):
        selection = self.jt_listbox.curselection()
        if not selection:
            self.selected_journee_type.set("")
            self.periodes_listbox.delete(0, tk.END)
            self.vider_formulaire_periode(); return
        selected_name = self.jt_listbox.get(selection[0])
        self.selected_journee_type.set(selected_name)
        tool_logger.debug(f"JT sélectionnée: {selected_name}")
        self.afficher_periodes_jt(selected_name)
        self.vider_formulaire_periode()

    def afficher_periodes_jt(self, jt_name):
        self.periodes_listbox.delete(0, tk.END)
        jt_data = self.journees_types.get(jt_name)
        if not jt_data or "periodes" not in jt_data: return
        try:
             periods_sorted = sorted(jt_data["periodes"], key=lambda p: datetime.strptime(p.get("heure_debut", "99:99:99"), "%H:%M:%S").time())
        except ValueError:
             tool_logger.warning(f"Tri périodes échoué pour {jt_name}.")
             periods_sorted = jt_data["periodes"]
        for i, periode in enumerate(periods_sorted):
            debut = periode.get("heure_debut", "??:??:??"); fin = periode.get("heure_fin", "??:??:??")
            nom = periode.get("nom", "Période"); sd = periode.get("sonnerie_debut"); sf = periode.get("sonnerie_fin")
            sd_display = self.find_sonnerie_display_name(sd) or AUCUNE_SONNERIE
            sf_display = self.find_sonnerie_display_name(sf) or AUCUNE_SONNERIE
            self.periodes_listbox.insert(tk.END, f"{debut} - {fin} : {nom} (Début: {sd_display}, Fin: {sf_display})")

    def on_periode_select(self, event=None):
        selection = self.periodes_listbox.curselection()
        if not selection: self.selected_periode_index.set(-1); return
        index = selection[0]; self.selected_periode_index.set(index)
        jt_name = self.selected_journee_type.get()
        if not jt_name or jt_name not in self.journees_types: return
        try:
            periods_sorted = sorted(self.journees_types[jt_name]["periodes"], key=lambda p: datetime.strptime(p.get("heure_debut", "99:99:99"), "%H:%M:%S").time())
            periode_data = periods_sorted[index]
            tool_logger.debug(f"Période sélectionnée (index {index}): {periode_data}")
            self.periode_nom_entry.delete(0, tk.END); self.periode_nom_entry.insert(0, periode_data.get("nom", ""))
            self.periode_debut_entry.delete(0, tk.END); self.periode_debut_entry.insert(0, periode_data.get("heure_debut", ""))
            self.periode_fin_entry.delete(0, tk.END); self.periode_fin_entry.insert(0, periode_data.get("heure_fin", ""))
            sond_value = periode_data.get("sonnerie_debut"); sonf_value = periode_data.get("sonnerie_fin")
            sond_display = self.find_sonnerie_display_name(sond_value); sonf_display = self.find_sonnerie_display_name(sonf_value)
            self.periode_sond_combo.set(sond_display if sond_display else AUCUNE_SONNERIE)
            self.periode_sonf_combo.set(sonf_display if sonf_display else AUCUNE_SONNERIE)
        except (IndexError, ValueError, KeyError) as e: # Indentation correcte ici
             tool_logger.error(f"Erreur lors de la sélection de période: {e}")
             self.vider_formulaire_periode()

    def ajouter_jt(self):
        nom_jt = simpledialog.askstring("Nouvelle Journée Type", "Entrez le nom de la nouvelle journée type:")
        if nom_jt:
            nom_jt = nom_jt.strip()
            if not nom_jt: messagebox.showerror("Erreur", "Le nom ne peut pas être vide."); return
            if nom_jt in self.journees_types: messagebox.showerror("Erreur", f"La journée type '{nom_jt}' existe déjà."); return
            self.journees_types[nom_jt] = {"nom": nom_jt, "periodes": []}
            self.update_jt_listbox(); self.update_assignation_hebdo_combos()
            tool_logger.info(f"Journée type '{nom_jt}' ajoutée.")

    def supprimer_jt(self):
        jt_name = self.selected_journee_type.get()
        if not jt_name: messagebox.showerror("Erreur", "Aucune journée type sélectionnée."); return
        if messagebox.askyesno("Confirmation", f"Supprimer la journée type '{jt_name}' ?\n(Références mises à jour dans planning hebdo et exceptions)"):
            if jt_name in self.journees_types:
                del self.journees_types[jt_name]
                for jour, jt in list(self.planning_hebdomadaire.items()):
                     if jt == jt_name: self.planning_hebdomadaire[jour] = AUCUNE_SONNERIE
                for date, details in list(self.exceptions_planning.items()):
                     if details.get("journee_type") == jt_name: del self.exceptions_planning[date]
                self.update_jt_listbox(); self.update_assignation_hebdo_combos(); self.update_exception_listbox()
                self.periodes_listbox.delete(0, tk.END); self.vider_formulaire_periode()
                tool_logger.info(f"Journée type '{jt_name}' supprimée.")

    def renommer_jt(self):
        old_name = self.selected_journee_type.get()
        if not old_name: messagebox.showerror("Erreur", "Aucune journée type sélectionnée."); return
        new_name = simpledialog.askstring("Renommer Journée Type", f"Nouveau nom pour '{old_name}':", initialvalue=old_name)
        if new_name:
            new_name = new_name.strip()
            if not new_name: messagebox.showerror("Erreur", "Le nouveau nom ne peut pas être vide."); return
            if new_name == old_name: return # Pas de changement
            if new_name in self.journees_types: messagebox.showerror("Erreur", f"Le nom '{new_name}' existe déjà."); return
            self.journees_types[new_name] = self.journees_types.pop(old_name)
            if "nom" in self.journees_types[new_name]: self.journees_types[new_name]["nom"] = new_name
            for jour, jt in self.planning_hebdomadaire.items():
                 if jt == old_name: self.planning_hebdomadaire[jour] = new_name
            for date, details in self.exceptions_planning.items():
                 if details.get("journee_type") == old_name: self.exceptions_planning[date]["journee_type"] = new_name
            self.update_jt_listbox(); self.update_assignation_hebdo_combos(); self.update_exception_listbox()
            try:
                idx = list(self.jt_listbox.get(0, tk.END)).index(new_name)
                self.jt_listbox.selection_set(idx); self.on_jt_select()
            except ValueError: pass
            tool_logger.info(f"Journée type '{old_name}' renommée en '{new_name}'.")

    def ajouter_periode(self):
        jt_name = self.selected_journee_type.get()
        if not jt_name:
            messagebox.showerror("Erreur", "Sélectionnez d'abord une journée type.")
            return

        # Récupérer les valeurs du formulaire
        nom = self.periode_nom_entry.get().strip()
        debut = self.periode_debut_entry.get().strip()
        fin = self.periode_fin_entry.get().strip()
        sond_display_name = self.periode_sond_combo.get() # Nom affiché (ex: "Sonnerie Cool")
        sonf_display_name = self.periode_sonf_combo.get() # Nom affiché

        # Valider les champs obligatoires
        if not nom or not debut or not fin:
            messagebox.showerror("Erreur", "Le nom, l'heure de début et l'heure de fin sont requis.")
            return

        # Valider le format des heures
        try:
            datetime.strptime(debut, "%H:%M:%S")
            datetime.strptime(fin, "%H:%M:%S")
        except ValueError:
            messagebox.showerror("Erreur Format Heure", "Le format d'heure doit être HH:MM:SS (ex: 08:00:00).")
            return

        # --- Retrouver le nom de fichier (.mp3) correspondant au nom affiché ---
        sond_filename = None # Initialiser à None
        if sond_display_name != AUCUNE_SONNERIE:
            # Chercher le nom de fichier dans le dictionnaire {nom_affiché: nom_fichier.mp3}
            sond_filename = self.sonneries_disponibles.get(sond_display_name)
            if not sond_filename:
                 # Si non trouvé (devrait pas arriver si combo à jour), log et message
                 tool_logger.error(f"Fichier MP3 non trouvé pour le nom affiché: '{sond_display_name}'")
                 messagebox.showwarning("Sonnerie Introuvable", f"Le fichier pour la sonnerie de début '{sond_display_name}' n'a pas été trouvé. Elle sera définie sur 'Aucune'.")
                 # sond_filename reste None

        sonf_filename = None # Initialiser à None
        if sonf_display_name != AUCUNE_SONNERIE:
            sonf_filename = self.sonneries_disponibles.get(sonf_display_name)
            if not sonf_filename:
                 tool_logger.error(f"Fichier MP3 non trouvé pour le nom affiché: '{sonf_display_name}'")
                 messagebox.showwarning("Sonnerie Introuvable", f"Le fichier pour la sonnerie de fin '{sonf_display_name}' n'a pas été trouvé. Elle sera définie sur 'Aucune'.")
                 # sonf_filename reste None
        # -------------------------------------------------------------------

        # Créer le dictionnaire de la nouvelle période avec les noms de fichiers (ou None)
        nouvelle_periode = {
            "nom": nom,
            "heure_debut": debut,
            "heure_fin": fin,
            "sonnerie_debut": sond_filename, # Nom de fichier avec .mp3 ou None
            "sonnerie_fin": sonf_filename   # Nom de fichier avec .mp3 ou None
        }

        # Ajouter la période à la journée type et mettre à jour l'UI
        self.journees_types[jt_name]["periodes"].append(nouvelle_periode)
        self.afficher_periodes_jt(jt_name) # Rafraîchir l'affichage de la liste des périodes
        self.vider_formulaire_periode()   # Vider les champs de saisie
        tool_logger.info(f"Période '{nom}' ajoutée à la JT '{jt_name}'.")

    def modifier_periode(self):
        jt_name = self.selected_journee_type.get()
        index = self.selected_periode_index.get() # Index dans la liste triée affichée
        if not jt_name or index < 0:
            messagebox.showerror("Erreur", "Sélectionnez une journée type ET une période à modifier.")
            return

        # Récupérer les nouvelles valeurs depuis le formulaire
        nom = self.periode_nom_entry.get().strip()
        debut = self.periode_debut_entry.get().strip()
        fin = self.periode_fin_entry.get().strip()
        sond_display_name = self.periode_sond_combo.get() # Nom affiché (ex: "Sonnerie Cool")
        sonf_display_name = self.periode_sonf_combo.get() # Nom affiché

        # Valider les champs
        if not nom or not debut or not fin:
            messagebox.showerror("Erreur", "Nom, Heure Début et Heure Fin sont requis.")
            return
        try:
            # Valider format heure
            datetime.strptime(debut, "%H:%M:%S")
            datetime.strptime(fin, "%H:%M:%S")
            # Optionnel: Vérifier que fin > debut ?
        except ValueError:
             messagebox.showerror("Erreur Format Heure", "Le format d'heure doit être HH:MM:SS.")
             return

        # --- Retrouver le nom de fichier (.mp3) correspondant au nom affiché ---
        sond_filename = None
        if sond_display_name != AUCUNE_SONNERIE:
            sond_filename = self.sonneries_disponibles.get(sond_display_name)
            if not sond_filename:
                 tool_logger.error(f"Fichier non trouvé pour sonnerie début modifiée: '{sond_display_name}'")
                 messagebox.showwarning("Sonnerie Invalide", f"La sonnerie de début '{sond_display_name}' n'a pas de fichier MP3 associé. Elle sera définie sur 'Aucune'.")
                 # sond_filename reste None

        sonf_filename = None
        if sonf_display_name != AUCUNE_SONNERIE:
            sonf_filename = self.sonneries_disponibles.get(sonf_display_name)
            if not sonf_filename:
                 tool_logger.error(f"Fichier non trouvé pour sonnerie fin modifiée: '{sonf_display_name}'")
                 messagebox.showwarning("Sonnerie Invalide", f"La sonnerie de fin '{sonf_display_name}' n'a pas de fichier MP3 associé. Elle sera définie sur 'Aucune'.")
                 # sonf_filename reste None
        # -------------------------------------------------------------------

        # Créer le dictionnaire de la période modifiée
        periode_modifiee = {
            "nom": nom,
            "heure_debut": debut,
            "heure_fin": fin,
            "sonnerie_debut": sond_filename, # Utiliser nom de fichier ou None
            "sonnerie_fin": sonf_filename   # Utiliser nom de fichier ou None
        }

        # Logique pour remplacer l'ancienne période par la nouvelle
        try:
            # 1. Obtenir la liste triée qui était affichée
            periods_sorted = sorted(self.journees_types[jt_name]["periodes"], key=lambda p: datetime.strptime(p.get("heure_debut", "99:99:99"), "%H:%M:%S").time())
            # 2. Identifier la période originale dans la liste triée grâce à l'index
            periode_originale_affichee = periods_sorted[index]

            # 3. Trouver et supprimer cette période originale dans la liste NON TRIÉE (self.journees_types...)
            #    C'est la partie délicate si des périodes sont identiques (même nom, heures, sonneries)
            #    On suppose ici qu'elles sont suffisamment uniques ou que supprimer la première trouvée est acceptable.
            original_list = self.journees_types[jt_name]["periodes"]
            found_and_removed = False
            for i in range(len(original_list)):
                 # Comparer les dictionnaires (peut être fragile si l'ordre des clés change, mais souvent ok pour JSON simple)
                 if original_list[i] == periode_originale_affichee:
                      del original_list[i]
                      found_and_removed = True
                      break # Arrêter dès qu'on l'a trouvée et supprimée

            if found_and_removed:
                # 4. Ajouter la période modifiée à la liste NON TRIÉE
                original_list.append(periode_modifiee)
                # 5. Mettre à jour l'affichage (qui re-trie) et vider le formulaire
                self.afficher_periodes_jt(jt_name)
                self.vider_formulaire_periode()
                tool_logger.info(f"Période (idx affiché {index}) modifiée dans JT '{jt_name}'. Nouvelle valeur: {periode_modifiee}")
            else:
                 # Si on n'a pas trouvé l'originale (ne devrait pas arriver si la sélection vient de la liste)
                 messagebox.showerror("Erreur Interne", "Impossible de retrouver la période originale à modifier.")
                 tool_logger.error(f"Période originale non trouvée pour modification. Donnée cherchée: {periode_originale_affichee}")

        except (IndexError, ValueError, KeyError) as e:
             messagebox.showerror("Erreur", f"Erreur lors de la modification de la période : {e}")
             tool_logger.error(f"Erreur modification période: {e}", exc_info=True)

    def supprimer_periode(self):
        jt_name = self.selected_journee_type.get(); index = self.selected_periode_index.get()
        if not jt_name or index < 0: messagebox.showerror("Erreur", "Sélectionnez JT ET période."); return
        if messagebox.askyesno("Confirmation", "Supprimer cette période ?"):
            try:
                periods_sorted = sorted(self.journees_types[jt_name]["periodes"], key=lambda p: datetime.strptime(p.get("heure_debut", "99:99:99"), "%H:%M:%S").time())
                periode_originale = periods_sorted[index]
                original_list = self.journees_types[jt_name]["periodes"]
                if periode_originale in original_list:
                     original_list.remove(periode_originale)
                     self.afficher_periodes_jt(jt_name); self.vider_formulaire_periode()
                     tool_logger.info(f"Période (idx {index}) supprimée de JT '{jt_name}'.")
                else: messagebox.showerror("Erreur", "Erreur interne: Période originale non trouvée."); tool_logger.error("Période originale non trouvée pour suppr.")
            except (IndexError, ValueError, KeyError) as e: messagebox.showerror("Erreur", "Sélection invalide."); tool_logger.error(f"Erreur suppr période: {e}")

    def vider_formulaire_periode(self):
        tool_logger.debug("Vidage formulaire période.")
        self.selected_periode_index.set(-1); self.periodes_listbox.selection_clear(0, tk.END)
        self.periode_nom_entry.delete(0, tk.END); self.periode_debut_entry.delete(0, tk.END)
        self.periode_fin_entry.delete(0, tk.END); self.periode_sond_combo.set(AUCUNE_SONNERIE); self.periode_sonf_combo.set(AUCUNE_SONNERIE)

    def update_sonneries_combos(self):
        """Met à jour toutes les listes déroulantes de sélection de sonnerie."""
        tool_logger.debug("màj combos sonneries...")
        sonneries_options = [AUCUNE_SONNERIE] + sorted(self.sonneries_disponibles.keys())
        # Périodes
        self.periode_sond_combo['values'] = sonneries_options
        self.periode_sonf_combo['values'] = sonneries_options
        # Alertes
        self.ppms_combo['values'] = sonneries_options
        self.attentat_combo['values'] = sonneries_options
        self.fin_alerte_combo['values'] = sonneries_options
        # Récupérer et setter les valeurs actuelles pour PPMS/Attentat/Fin
        ppms_current = self.parametres_college.get("sonnerie_ppms", "")
        attentat_current = self.parametres_college.get("sonnerie_attentat", "")
        fin_alerte_current = self.parametres_college.get("sonnerie_fin_alerte", "") # Lire la valeur

        ppms_display = self.find_sonnerie_display_name(ppms_current)
        attentat_display = self.find_sonnerie_display_name(attentat_current)
        fin_alerte_display = self.find_sonnerie_display_name(fin_alerte_current) # Trouver nom affiché

        self.ppms_combo.set(ppms_display if ppms_display else AUCUNE_SONNERIE)
        self.attentat_combo.set(attentat_display if attentat_display else AUCUNE_SONNERIE)
        # --- AJOUTER CETTE LIGNE ---
        self.fin_alerte_combo.set(fin_alerte_display if fin_alerte_display else AUCUNE_SONNERIE) # Setter la valeur
        # --- FIN AJOUT ---

        tool_logger.debug("Listes sonneries màj.")

    def find_sonnerie_display_name(self, filename):
        if not filename: return None
        for display_name, fname in self.sonneries_disponibles.items():
            if fname == filename: return display_name
        return None

    def update_assignation_hebdo_combos(self):
        tool_logger.debug("MàJ Combos Assignation Hebdo...")
        options = [AUCUNE_SONNERIE] + sorted(self.journees_types.keys())
        current_planning = self.planning_hebdomadaire
        tool_logger.debug(f"Options: {options}, Actuel: {current_planning}")
        for jour, combo in self.hebdo_combos.items():
            combo['values'] = options
            current_jt = current_planning.get(jour, AUCUNE_SONNERIE)
            if current_jt != AUCUNE_SONNERIE and current_jt not in self.journees_types:
                 tool_logger.warning(f"JT '{current_jt}' pour {jour} invalide. Reset.")
                 current_jt = AUCUNE_SONNERIE; self.planning_hebdomadaire[jour] = AUCUNE_SONNERIE
            tool_logger.debug(f"{jour}: set -> '{current_jt}'")
            combo.set(current_jt)
        tool_logger.debug("Combos Assignation màj.")

    def get_current_hebdo_config(self):
        return {jour: combo.get() for jour, combo in self.hebdo_combos.items()}

    def update_exception_listbox(self):
        tool_logger.debug("Chargement/Affichage exceptions")
        self.exc_listbox.delete(0, tk.END)
        sorted_dates = sorted(self.exceptions_planning.keys())
        for date_str in sorted_dates:
            details = self.exceptions_planning[date_str]; action = details.get("action", "?"); jt = details.get("journee_type", "")
            desc = details.get("description", ""); display_text = f"{date_str}: {action.upper()}"
            if action == "utiliser_jt": display_text += f" ({jt})"
            if desc: display_text += f" - {desc}"
            self.exc_listbox.insert(tk.END, display_text)
        tool_logger.debug(f"{len(sorted_dates)} exceptions affichées.")

    # Dans config_tool_tkinter.py -> class ConfigApp

    def on_exception_select(self, event=None):
        """Appelé lorsqu'une exception est sélectionnée dans la liste."""
        selection = self.exc_listbox.curselection()
        if not selection:
            # Si la sélection est vide (ex: clic ailleurs), on vide le formulaire
            self.selected_exception_date.set("")
            self.clear_exception_form()
            return

        # Extraire la date du texte affiché (ex: "2025-05-30: SILENCE - ...")
        selected_text = self.exc_listbox.get(selection[0])
        try:
            # Isoler et valider la partie date
            date_str = selected_text.split(":")[0].strip()
            datetime.strptime(date_str, "%Y-%m-%d") # Vérifie le format
        except (IndexError, ValueError):
             tool_logger.error(f"Impossible d'extraire une date valide de: '{selected_text}'")
             self.clear_exception_form()
             messagebox.showerror("Erreur Interne", "Impossible de lire la date de l'exception sélectionnée.")
             return

        # Mettre à jour la date sélectionnée (utile pour suppression/modification)
        self.selected_exception_date.set(date_str)
        tool_logger.debug(f"Exception sélectionnée: {date_str}")

        # Récupérer les détails de l'exception depuis les données chargées
        details = self.exceptions_planning.get(date_str)

        if details:
            # Remplir le formulaire avec les détails trouvés
            self.exc_date_entry.delete(0, tk.END)
            self.exc_date_entry.insert(0, date_str)

            action = details.get("action", "silence") # Action par défaut = silence
            self.exc_action_var.set(action)
            # Mettre à jour l'état du ComboBox JT en fonction de l'action
            self.on_action_change()

            # --- Logique Corrigée pour le ComboBox Journée Type ---
            jt_name = details.get("journee_type") # Récupère le nom de JT (peut être None)
            jt_to_set = AUCUNE_SONNERIE # Ce qu'on va mettre dans le combo par défaut

            if action == "utiliser_jt":
                 # Si l'action est d'utiliser une JT, on essaie de la sélectionner
                 combo_options = list(self.exc_jt_combo['values']) # Options actuelles du combo
                 if jt_name and jt_name in combo_options:
                      # La JT stockée existe dans les options, on la sélectionne
                      jt_to_set = jt_name
                 elif jt_name:
                      # La JT stockée existe mais n'est PLUS une option valide
                      messagebox.showwarning("Journée Type Invalide",
                                             f"La journée type '{jt_name}' associée à cette exception ({date_str}) n'existe plus ou a été renommée.\nElle sera réinitialisée sur '{AUCUNE_SONNERIE}'.")
                      tool_logger.warning(f"JT '{jt_name}' pour exception {date_str} non trouvée dans options: {combo_options}")
                      # Laisser jt_to_set à AUCUNE_SONNERIE
                 else:
                      # Action est 'utiliser_jt' mais aucun nom n'est fourni (donnée invalide)
                      messagebox.showwarning("Donnée Invalide",
                                              f"L'exception pour le {date_str} est configurée pour utiliser une journée type, mais aucune n'est spécifiée.\nElle sera réinitialisée sur '{AUCUNE_SONNERIE}'.")
                      tool_logger.warning(f"Action 'utiliser_jt' pour {date_str} mais jt_name est vide ou None.")
                      # Laisser jt_to_set à AUCUNE_SONNERIE

            # Définir la valeur du combo.
            # Si action='silence', jt_to_set est resté AUCUNE_SONNERIE.
            # Si action='utiliser_jt', jt_to_set est soit la JT valide, soit AUCUNE_SONNERIE si invalide.
            self.exc_jt_combo.set(jt_to_set)
            # --- Fin Logique Corrigée ---

            # Remplir la description
            self.exc_desc_entry.delete(0, tk.END)
            self.exc_desc_entry.insert(0, details.get("description", ""))
        else:
             # Ce cas indique une incohérence entre la ListBox et les données internes
             tool_logger.error(f"Incohérence: Exception pour date '{date_str}' sélectionnée mais non trouvée dans self.exceptions_planning.")
             messagebox.showerror("Erreur Interne", f"Les détails pour l'exception du {date_str} sont introuvables.")
             self.clear_exception_form()

    def clear_exception_form(self):
         self.exc_date_entry.delete(0, tk.END); self.exc_action_var.set("silence"); self.on_action_change()
         self.exc_jt_combo.set(AUCUNE_SONNERIE); self.exc_desc_entry.delete(0, tk.END)
         self.selected_exception_date.set(""); self.exc_listbox.selection_clear(0, tk.END)

    def ajouter_ou_modifier_exception(self):
        date_str = self.exc_date_entry.get().strip(); action = self.exc_action_var.get()
        jt_name = self.exc_jt_combo.get(); description = self.exc_desc_entry.get().strip()
        try: date_obj = datetime.strptime(date_str, "%Y-%m-%d").date() # Valider format
        except ValueError: messagebox.showerror("Erreur Format Date", "Format date: YYYY-MM-DD."); return
        if action == "utiliser_jt" and (not jt_name or jt_name == AUCUNE_SONNERIE):
            messagebox.showerror("Erreur", "Sélectionnez une JT pour action 'Utiliser JT'."); return
        details = {"action": action, "journee_type": jt_name if action == "utiliser_jt" else None, "description": description}
        self.exceptions_planning[date_str] = details
        self.update_exception_listbox(); self.clear_exception_form()
        tool_logger.info(f"Exception ajoutée/modifiée pour {date_str}.")

    def supprimer_exception(self):
        date_str = self.selected_exception_date.get()
        if not date_str: messagebox.showerror("Erreur", "Aucune exception sélectionnée."); return
        if messagebox.askyesno("Confirmation", f"Supprimer l'exception pour {date_str} ?"):
             if date_str in self.exceptions_planning:
                  del self.exceptions_planning[date_str]; self.update_exception_listbox()
                  self.clear_exception_form(); tool_logger.info(f"Exception pour {date_str} supprimée.")

    def on_action_change(self):
        tool_logger.debug(f"Action = {self.exc_action_var.get()}")
        is_jt_action = self.exc_action_var.get() == "utiliser_jt"
        new_state = tk.NORMAL if is_jt_action else tk.DISABLED
        combo_state = "readonly" if is_jt_action else tk.DISABLED
        self.exc_jt_combo.config(state=combo_state); self.exc_jt_label.config(state=new_state)
        if not is_jt_action: self.exc_jt_combo.set(AUCUNE_SONNERIE)
        tool_logger.debug(f"Action = {self.exc_action_var.get()} -> Combo JT state: {combo_state}")

    def update_exception_jt_combo(self):
        tool_logger.debug(f"Combo exception JT màj: {list(self.journees_types.keys())}")
        options = [AUCUNE_SONNERIE] + sorted(self.journees_types.keys())
        self.exc_jt_combo['values'] = options
        current_val = self.exc_jt_combo.get()
        if current_val not in options: self.exc_jt_combo.set(AUCUNE_SONNERIE)

    def rafraichir_liste_sonneries(self):
        tool_logger.info(f"Rafraîchissement sonneries depuis {MP3_PATH}")
        self.son_listbox.delete(0, tk.END); nouvelles_sonneries = {}
        try:
            if not os.path.isdir(MP3_PATH): messagebox.showerror("Erreur", f"Dossier MP3 introuvable:\n{MP3_PATH}"); return
            fichiers_trouves = []; display_names_used = set()
            for f in os.listdir(MP3_PATH):
                 if f.lower().endswith('.mp3'):
                      fichiers_trouves.append(f); display_name = os.path.splitext(f)[0]
                      original_display_name = display_name; count = 1
                      while display_name in display_names_used: display_name = f"{original_display_name}_{count}"; count += 1
                      nouvelles_sonneries[display_name] = f; display_names_used.add(display_name)
                      self.son_listbox.insert(tk.END, f"{display_name} ({f})")
            # Remplacer l'ancienne liste par la nouvelle pour nettoyer les sonneries supprimées du dossier
            self.sonneries_disponibles = nouvelles_sonneries
            tool_logger.info(f"{len(self.sonneries_disponibles)} sonneries trouvées/mises à jour.")
            self.update_sonneries_combos() # Mettre à jour tous les combos
        except Exception as e: messagebox.showerror("Erreur Liste Sonneries", f"Erreur lecture dossier MP3:\n{e}"); tool_logger.error(f"Erreur lecture dossier MP3: {e}", exc_info=True)

    def supprimer_sonnerie_config(self):
        selection = self.son_listbox.curselection()
        if not selection: messagebox.showerror("Erreur", "Aucune sonnerie sélectionnée."); return
        selected_text = self.son_listbox.get(selection[0]); display_name = selected_text.split(" (")[0]
        if messagebox.askyesno("Confirmation", f"Retirer '{display_name}' de la config?\n(Fichier non supprimé, peut affecter JT)"):
            if display_name in self.sonneries_disponibles:
                del self.sonneries_disponibles[display_name]; self.rafraichir_liste_sonneries()
                tool_logger.info(f"Sonnerie '{display_name}' retirée de la config.")
            else: tool_logger.warning(f"Tentative suppression '{display_name}' non trouvée.")

    def on_dept_select(self, event=None):
        dept = self.dept_combo.get(); zone = DEPARTEMENTS_ZONES.get(dept, "N/A")
        self.zone_label.config(text=zone); tool_logger.debug(f"Département: '{dept}', Zone: '{zone}'")

    def parcourir_ics(self):
        filepath = filedialog.askopenfilename(initialdir=CONFIG_PATH, title="Sélectionner fichier ICS", filetypes=(("iCalendar", "*.ics"),("Tous", "*.*")))
        if filepath:
            self.ics_path_entry.delete(0, tk.END)
            try:
                rel_path = os.path.relpath(filepath, CONFIG_PATH)
                if not rel_path.startswith('..'): self.ics_path_entry.insert(0, rel_path); tool_logger.info(f"Chemin ICS relatif: {rel_path}")
                else: self.ics_path_entry.insert(0, filepath); tool_logger.info(f"Chemin ICS absolu: {filepath}")
            except ValueError: self.ics_path_entry.insert(0, filepath); tool_logger.info(f"Chemin ICS absolu (lecteurs diff): {filepath}")
            self.afficher_dates_info()

    def afficher_dates_info(self):
        tool_logger.debug("afficher_dates_info.")
        ics_path = self.ics_path_entry.get()
        if ics_path: self.ics_info_label.config(text=f"Fichier ICS: {ics_path}")
        else: self.ics_info_label.config(text="Aucun fichier ICS configuré.")

    def update_user_listbox(self):
        self.user_listbox.delete(0, tk.END)
        for username in sorted(self.users.keys()): self.user_listbox.insert(tk.END, username)

    def on_user_select(self, event=None):
        selection = self.user_listbox.curselection()
        if not selection: return
        username = self.user_listbox.get(selection[0])
        self.selected_user.set(username); self.user_entry.delete(0, tk.END); self.user_entry.insert(0, username)
        self.pass_entry.delete(0, tk.END); self.pass_confirm_entry.delete(0, tk.END)
        tool_logger.debug(f"User sélectionné: {username}")

    def check_listbox_empty_click(self, event, listbox, callback):
         if listbox.identify(event.x, event.y) == "": callback()

    def clear_user_form(self, event=None):
         tool_logger.debug("Clic vide users -> Clear form.")
         self.selected_user.set(""); self.user_entry.delete(0, tk.END)
         self.pass_entry.delete(0, tk.END); self.pass_confirm_entry.delete(0, tk.END)
         self.user_listbox.selection_clear(0, tk.END)

    def ajouter_ou_modifier_user(self):
        username = self.user_entry.get().strip(); password = self.pass_entry.get(); password_confirm = self.pass_confirm_entry.get()
        if not username: messagebox.showerror("Erreur", "Nom d'utilisateur requis."); return
        is_new_user = username not in self.users
        if not password and is_new_user: messagebox.showerror("Erreur", "Mot de passe requis pour nouvel utilisateur."); return
        if password: # Si un mdp est saisi (modif ou ajout)
             if password != password_confirm: messagebox.showerror("Erreur", "Mots de passe non identiques."); return
             hashed_password = generate_password_hash(password, method='pbkdf2:sha256:1000000')
             self.users[username] = hashed_password
             tool_logger.info(f"Utilisateur '{username}' {'ajouté' if is_new_user else 'mis à jour'}.")
        elif not is_new_user and not password:
             # Cas où on sélectionne un user existant mais on ne met pas de mdp (on ne change pas le mdp)
             tool_logger.info(f"Utilisateur '{username}' sélectionné, mot de passe non modifié.")
             # Il suffit de vider le formulaire, pas besoin de réassigner self.users[username]
        self.update_user_listbox(); self.clear_user_form()

    def supprimer_user(self):
        username = self.selected_user.get()
        if not username: messagebox.showerror("Erreur", "Aucun utilisateur sélectionné."); return
        if username == "admin" and len(self.users) == 1: # Sécurité pour ne pas supprimer le dernier admin ? À adapter.
            messagebox.showerror("Erreur", "Impossible de supprimer le dernier utilisateur.")
            return
        if messagebox.askyesno("Confirmation", f"Supprimer l'utilisateur '{username}' ?"):
             if username in self.users:
                  del self.users[username]; self.update_user_listbox(); self.clear_user_form()
                  tool_logger.info(f"Utilisateur '{username}' supprimé.")


# --- Point d'entrée ---
if __name__ == "__main__":
    # Assurer que l'import initial a fonctionné
    if CONSTANTS_LOADED:
        print("--- Démarrage Outil Configuration Sonneries ---")
        app = ConfigApp()
        if app.winfo_exists():
             tool_logger.debug("Démarrage mainloop (outil config)...")
             app.mainloop()
             tool_logger.debug("Mainloop finie (outil config).")
        else:
             tool_logger.critical("Échec création fenêtre principale.")
        print("--- Fin script outil config ---")
    else:
        # Message d'erreur déjà affiché via messagebox si possible
        print("Échec chargement constantes initiales. Arrêt.")
        # Pas besoin de sys.exit(1) ici car déjà fait plus haut si erreur critique