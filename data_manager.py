# data_manager.py
"""
Gère le chargement et la sauvegarde des données de l'application
(sonneries et paramètres) depuis/vers des fichiers JSON.
"""
import json
import os
from tkinter import messagebox
# Importer les noms de fichiers depuis les constantes
from constants import DONNEES_SONNERIES_FILE, PARAMS_FILE

def sauvegarder_donnees_sonneries(app):
    """Sauvegarde uniquement la liste des sonneries."""
    print(f"DEBUG [DataMgr]: Sauvegarde sonneries dans {SONNERIES_DATA_FILE}...")
    donnees = {"sonneries": app.sonneries}
    try:
        with open(SONNERIES_DATA_FILE, "w", encoding='utf-8') as f:
            json.dump(donnees, f, indent=4, ensure_ascii=False)
        print("DEBUG [DataMgr]: Sauvegarde sonneries réussie.")
    except Exception as e:
        print(f"ERREUR [DataMgr]: Impossible de sauvegarder {SONNERIES_DATA_FILE}: {e}")
        messagebox.showerror("Erreur Sauvegarde", f"Impossible sauvegarder sonneries:\n{str(e)}")

def charger_donnees_sonneries(app):
    """Charge uniquement la liste des sonneries et met à jour l'attribut app.sonneries."""
    print(f"DEBUG [DataMgr]: Chargement sonneries depuis {SONNERIES_DATA_FILE}...")
    app.sonneries = {} # Réinitialiser avant chargement
    try:
        if os.path.exists(SONNERIES_DATA_FILE):
            with open(SONNERIES_DATA_FILE, "r", encoding='utf-8') as f:
                donnees = json.load(f)
            # Utiliser .get avec un dict vide par défaut
            loaded_sonneries = donnees.get("sonneries", {})
            if isinstance(loaded_sonneries, dict):
                 app.sonneries = loaded_sonneries
                 print(f"DEBUG [DataMgr]: Sonneries chargées: {len(app.sonneries)}")
            else:
                 print(f"ATTENTION [DataMgr]: Format invalide pour 'sonneries' dans {SONNERIES_DATA_FILE}. Ignoré.")
                 messagebox.showwarning("Données Invalides", f"Le format des sonneries dans {SONNERIES_DATA_FILE} est invalide.")
        else:
            print(f"DEBUG [DataMgr]: {SONNERIES_DATA_FILE} non trouvé. Liste sonneries vide.")
    except json.JSONDecodeError as json_err:
        print(f"ERREUR [DataMgr]: {SONNERIES_DATA_FILE} corrompu: {json_err}")
        messagebox.showerror("Erreur Chargement", f"{SONNERIES_DATA_FILE} corrompu.\nListe sonneries réinitialisée.")
    except Exception as e:
        print(f"ERREUR [DataMgr]: Impossible charger {SONNERIES_DATA_FILE}: {e}")
        messagebox.showerror("Erreur Chargement", f"Err charg. sonneries:\n{str(e)}")
    # La mise à jour de l'UI (Treeview, Combos) doit être faite séparément après l'appel

def sauvegarder_parametres(app):
    """Sauvegarde tous les paramètres de configuration."""
    print(f"DEBUG [DataMgr]: Sauvegarde paramètres dans {PARAMETERS_FILE}...")
    parametres = {
        "departement": app.departement_selectionne.get(),
        "zone": app.zone_academique.get(),
        "dates_vacances": app.dates_vacances,
        "jours_feries": app.jours_feries,
        "sonnerie_ppms": app.sonnerie_ppms.get(),
        "sonnerie_attentat": app.sonnerie_attentat.get(),
        "journees_types": app.journees_types,
        "assignation_hebdo": app.assignation_hebdo
    }
    try:
        with open(PARAMETERS_FILE, "w", encoding='utf-8') as f:
            json.dump(parametres, f, indent=4, ensure_ascii=False)
        print("DEBUG [DataMgr]: Sauvegarde paramètres réussie.")
    except Exception as e:
        print(f"ERREUR [DataMgr]: Impossible de sauvegarder {PARAMETERS_FILE}: {e}")
        messagebox.showerror("Erreur Sauvegarde", f"Impossible sauvegarder paramètres:\n{str(e)}")

def charger_parametres(app):
    """Charge tous les paramètres et met à jour les attributs de l'instance app."""
    print(f"DEBUG [DataMgr]: Chargement paramètres depuis {PARAMETERS_FILE}...")
    # Réinitialiser les attributs avant chargement pour éviter de garder d'anciennes données
    app.departement_selectionne.set("")
    app.zone_academique.set("Inconnue")
    app.dates_vacances = []
    app.jours_feries = []
    app.sonnerie_ppms.set("")
    app.sonnerie_attentat.set("")
    app.journees_types = {}
    app.assignation_hebdo = {}

    try:
        if os.path.exists(PARAMETERS_FILE):
            with open(PARAMETERS_FILE, "r", encoding='utf-8') as f:
                parametres = json.load(f)
            print(f"DEBUG [DataMgr]: Paramètres chargés: {list(parametres.keys())}")

            # Appliquer les valeurs chargées
            app.departement_selectionne.set(parametres.get("departement", ""))
            # Note: La zone sera mise à jour via update_zone_display appelé après chargement

            temp_vac = parametres.get("dates_vacances", [])
            app.dates_vacances = temp_vac if isinstance(temp_vac, list) else []
            if not isinstance(temp_vac, list): print("WARN [DataMgr]: dates_vacances reset (pas une liste)")

            temp_fer = parametres.get("jours_feries", [])
            app.jours_feries = temp_fer if isinstance(temp_fer, list) else []
            if not isinstance(temp_fer, list): print("WARN [DataMgr]: jours_feries reset (pas une liste)")

            app.sonnerie_ppms.set(parametres.get("sonnerie_ppms", ""))
            app.sonnerie_attentat.set(parametres.get("sonnerie_attentat", ""))

            temp_jt = parametres.get("journees_types", {})
            app.journees_types = temp_jt if isinstance(temp_jt, dict) else {}
            if not isinstance(temp_jt, dict): print("WARN [DataMgr]: journees_types reset (pas un dict)")

            temp_ah = parametres.get("assignation_hebdo", {})
            app.assignation_hebdo = temp_ah if isinstance(temp_ah, dict) else {}
            if not isinstance(temp_ah, dict): print("WARN [DataMgr]: assignation_hebdo reset (pas un dict)")

            print(f"DEBUG [DataMgr]: Paramètres appliqués.")
            # L'appel à app.update_zone_display() doit être fait DANS la classe App après cet appel.
            # Les vérifications de sonneries alertes doivent être faites DANS la classe App après cet appel.
            # L'affichage des dates / statut UI doit être fait DANS la classe App après cet appel.

        else:
            print(f"DEBUG [DataMgr]: {PARAMETERS_FILE} non trouvé. Paramètres par défaut (vides).")

    except json.JSONDecodeError as json_err:
        print(f"ERREUR [DataMgr]: {PARAMETERS_FILE} corrompu: {json_err}")
        messagebox.showerror("Erreur Chargement", f"{PARAMETERS_FILE} corrompu.\nParamètres réinitialisés.")
        # Les valeurs par défaut (vides) définies au début de la fonction restent.
    except Exception as e:
        print(f"ERREUR [DataMgr]: Impossible charger {PARAMETERS_FILE}: {e}")
        messagebox.showerror("Erreur Chargement", f"Err charg. params:\n{str(e)}")
        # Les valeurs par défaut (vides) définies au début de la fonction restent.