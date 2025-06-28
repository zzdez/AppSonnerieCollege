# holiday_manager.py

import logging
from datetime import date, datetime, timedelta
import requests
import json
import os
import sys # Importer sys pour stderr dans le logger fallback

# Essayer d'importer icalendar et logguer si absent
try:
    from icalendar import Calendar
    ICALENDAR_AVAILABLE = True
except ImportError:
    Calendar = None
    ICALENDAR_AVAILABLE = False
    logging.getLogger(__name__).warning("Librairie 'icalendar' non installée. Import/parsing ICS échouera.")

# Importer les constantes nécessaires
try:
    from constants import VACANCES_ICS_BASE_URL, CONFIG_PATH
except ImportError:
    # Fallbacks si constants.py est incomplet ou non trouvé
    VACANCES_ICS_BASE_URL = "https://www.service-public.fr/simulateur/calcul/assets/dsfr-particuliers/fichiers_ics/"
    # Tenter de construire un chemin de config même si l'import échoue
    try:
        # Chemin à côté de ce script, dans un sous-dossier 'config'
        default_config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'config') # Remonter d'un niveau si holiday_manager est dans un sous-dossier
        # Ou si holiday_manager est à la racine :
        # default_config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config')
        if 'CONFIG_PATH' not in locals(): CONFIG_PATH = default_config_path
        logging.getLogger(__name__).warning(f"CONFIG_PATH non importé, utilisation défaut: {CONFIG_PATH}")
    except NameError: # Au cas où __file__ ne serait pas défini
         if 'CONFIG_PATH' not in locals(): CONFIG_PATH = "./config" # Chemin relatif très basique
         logging.getLogger(__name__).warning(f"CONFIG_PATH non importé et __file__ non défini, utilisation chemin relatif: {CONFIG_PATH}")

    logging.getLogger(__name__).warning("VACANCES_ICS_BASE_URL non importé depuis constants, utilisation valeur par défaut.")


# --- Classe HolidayManager ---
class HolidayManager:
    """ Gère fériés et vacances (auto-download ICS). """
    HOLIDAY_CACHE_FILE = "holiday_cache.json"
    TEMP_ICS_FILENAME = "temp_vacances_downloaded.ics"
    CACHE_EXPIRY_DAYS = 7

    Calendar = Calendar

    def __init__(self, logger_instance: logging.Logger, cache_dir=None):
        """ Initialise le gestionnaire. """
        self.logger = logger_instance
        self._holidays = {} # {date: "Description"}
        self._vacations = [] # [{"debut": date, "fin": date, "description": str}]

        # Définir le répertoire de cache
        determined_cache_dir = None
        if cache_dir and os.path.isdir(cache_dir):
            determined_cache_dir = cache_dir
            self.logger.info(f"Utilisation répertoire cache fourni: {determined_cache_dir}")
        elif CONFIG_PATH and os.path.isdir(CONFIG_PATH):
             determined_cache_dir = CONFIG_PATH
             self.logger.info(f"Utilisation CONFIG_PATH comme répertoire cache: {determined_cache_dir}")
        else:
            # Fallback ultime
            script_dir = os.path.dirname(os.path.abspath(__file__))
            determined_cache_dir = script_dir
            self.logger.warning(f"Cache_dir/CONFIG_PATH invalide/inaccessible, utilisation répertoire local: {determined_cache_dir}")

        # S'assurer que le dossier existe avant de joindre les noms de fichiers
        self.cache_directory = determined_cache_dir
        try:
            if not os.path.exists(self.cache_directory):
                 self.logger.warning(f"Répertoire de cache n'existe pas, tentative de création: {self.cache_directory}")
                 os.makedirs(self.cache_directory, exist_ok=True)
        except OSError as e:
             self.logger.error(f"Impossible de créer le répertoire de cache {self.cache_directory}: {e}. Cache désactivé.")
             self.cache_directory = None # Désactiver le cache si on ne peut pas créer le dossier

        if self.cache_directory:
            self.holiday_cache_path = os.path.join(self.cache_directory, self.HOLIDAY_CACHE_FILE)
            self.temp_ics_path = os.path.join(self.cache_directory, self.TEMP_ICS_FILENAME)
            self.logger.info(f"Chemin cache fériés: {self.holiday_cache_path}")
            self.logger.info(f"Chemin ICS temp: {self.temp_ics_path}")
            self._load_holidays_from_cache() # Charger fériés existants
        else:
             self.holiday_cache_path = None
             self.temp_ics_path = None
             self.logger.error("Cache désactivé car répertoire inaccessible.")


    # --- Méthodes Jours Fériés ---
    def load_holidays_from_api(self, api_url, country_code="FR", region_code=None, force_refresh=False):
        # ... (Code existant - potentiellement ajouter vérif self.holiday_cache_path is not None avant d'essayer de lire/écrire) ...
        self.logger.info(f"Demande chargement/màj jours fériés API: {api_url} (Pays={country_code}, Force={force_refresh})")
        if not api_url: self.logger.warning("URL API fériés non fournie."); return bool(self._holidays)

        cache_expired = True
        # Vérifier le cache seulement si le chemin est valide
        if self.holiday_cache_path and os.path.exists(self.holiday_cache_path):
            try:
                cache_mod_time = os.path.getmtime(self.holiday_cache_path)
                expiry_limit = datetime.now() - timedelta(days=self.CACHE_EXPIRY_DAYS)
                if datetime.fromtimestamp(cache_mod_time) > expiry_limit: cache_expired = False
                self.logger.debug(f"Cache fériés trouvé. Modifié: {datetime.fromtimestamp(cache_mod_time)}. Expiré: {cache_expired}")
            except Exception as e: self.logger.warning(f"Vérif âge cache fériés échouée ({self.holiday_cache_path}): {e}. Considéré expiré.")

        if not force_refresh and not cache_expired and self._holidays:
             self.logger.info("Utilisation jours fériés depuis cache (non expiré/forcé)."); return True

        # --- Reste de la logique API ---
        self.logger.info("Récupération jours fériés depuis API..."); current_year = datetime.now().year; years_to_load = [current_year - 1, current_year, current_year + 1, current_year + 2]
        self.logger.info(f"Années à charger/màj depuis API: {years_to_load}") # Log ajouté pour confirmer
        new_holidays = {}; api_ok = True
        for year in years_to_load:
            full_url = f"{api_url.rstrip('/')}/{year}/{country_code}"; self.logger.debug(f"Appel API {year}: {full_url}")
            try:
                response = requests.get(full_url, timeout=15); self.logger.debug(f"API {year} status: {response.status_code}"); response.raise_for_status(); api_data = response.json()
                self.logger.info(f"API {year} data OK ({len(api_data)} jours).")
                for item in api_data:
                    try:
                        hdate_str = item.get("date"); desc = item.get("localName") or item.get("name")
                        if hdate_str and desc: new_holidays[date.fromisoformat(hdate_str)] = desc
                        else: self.logger.warning(f"API férié {year} item incomplet: {item}")
                    except (ValueError, TypeError) as e_parse: self.logger.warning(f"API férié {year} parse échoué: {item}. Err: {e_parse}")
            except requests.exceptions.RequestException as e_req: self.logger.error(f"API fériés {year} erreur req: {e_req}"); api_ok = False
            except json.JSONDecodeError as e_json: self.logger.error(f"API fériés {year} erreur JSON: {e_json}"); api_ok = False
            except Exception as e_glob: self.logger.error(f"API fériés {year} erreur glob: {e_glob}", exc_info=True); api_ok = False
        if new_holidays and api_ok:
             self.logger.info(f"Total {len(new_holidays)} fériés chargés API {years_to_load}."); self._holidays = new_holidays; self._save_holidays_to_cache(); return True
        elif not new_holidays and api_ok: self.logger.warning("Aucun férié valide retourné par API."); return bool(self._holidays)
        else: self.logger.error("Échec récupération API fériés."); return bool(self._holidays) # Retourne True si on a un cache

    def _load_holidays_from_cache(self):
        if not self.holiday_cache_path: return # Ne rien faire si pas de chemin de cache
        if os.path.exists(self.holiday_cache_path):
            self.logger.info(f"Load cache fériés: {self.holiday_cache_path}")
            try:
                with open(self.holiday_cache_path, 'r', encoding='utf-8') as f: cached = json.load(f)
                loaded = {date.fromisoformat(dt): desc for dt, desc in cached.items()}
                self._holidays = loaded; self.logger.info(f"{len(self._holidays)} fériés chargés cache.")
            except Exception as e: self.logger.error(f"Erreur load cache fériés ({self.holiday_cache_path}): {e}. Ignoré.", exc_info=True); self._holidays = {}
        else: self.logger.info(f"Aucun cache fériés trouvé ({self.holiday_cache_path})."); self._holidays = {}

    def _save_holidays_to_cache(self):
        if not self.holiday_cache_path: return # Ne rien faire si pas de chemin de cache
        if not self._holidays: self.logger.info("Aucun férié à sauver cache."); return
        self.logger.info(f"Save {len(self._holidays)} fériés cache: {self.holiday_cache_path}")
        try:
            data = {dt.isoformat(): desc for dt, desc in self._holidays.items()}
            os.makedirs(os.path.dirname(self.holiday_cache_path), exist_ok=True)
            with open(self.holiday_cache_path, 'w', encoding='utf-8') as f: json.dump(data, f, ensure_ascii=False, indent=2)
            self.logger.info("Save cache fériés OK.")
        except Exception as e: self.logger.error(f"Erreur save cache fériés ({self.holiday_cache_path}): {e}", exc_info=True)

    # --- Méthodes Vacances ---
    def _download_ics(self, url, save_path):
        """Télécharge un fichier ICS depuis une URL et le sauvegarde."""
        if not save_path: self.logger.error("Chemin de sauvegarde ICS temporaire non défini."); return False
        self.logger.info(f"Tentative téléchargement ICS: {url}")
        try:
            response = requests.get(url, timeout=20, allow_redirects=True, headers={'User-Agent': 'CollegeSonnerieApp/1.0'})
            self.logger.debug(f"DL ICS status: {response.status_code}")
            response.raise_for_status()
            content_type = response.headers.get('content-type', '').lower()
            if 'text/calendar' not in content_type and 'application/octet-stream' not in content_type: self.logger.warning(f"DL ICS type contenu inattendu: '{content_type}' pour {url}")
            os.makedirs(os.path.dirname(save_path), exist_ok=True) # Assurer existence dossier cache
            with open(save_path, 'wb') as f: f.write(response.content)
            self.logger.info(f"ICS téléchargé et sauvé dans: {save_path}")
            return True
        except requests.exceptions.Timeout: self.logger.error(f"Timeout DL ICS: {url}"); return False
        except requests.exceptions.RequestException as e: self.logger.error(f"Erreur DL ICS {url}: {e}"); return False
        except Exception as e: self.logger.error(f"Erreur inattendue DL/Save ICS: {e}", exc_info=True); return False

    def load_vacations(self, zone, local_ics_path=None, manual_ics_base_url=None):
        self.logger.info(f"Load vacances: Zone={zone}, PathLocal={local_ics_path}, URLManuelle={manual_ics_base_url}")
        self._vacations = [] # Reset
        ics_to_parse_current = None # Chemin pour l'année scolaire actuelle
        ics_to_parse_next = None    # Chemin pour l'année scolaire suivante
        download_urls_tried = []

        # --- Logique pour déterminer les années scolaires à charger ---
        now = datetime.now(); current_civil_year = now.year
        # Année scolaire en cours (ex: 2024-2025 si on est après août 2024)
        current_academic_start = current_civil_year - 1 if now.month < 8 else current_civil_year
        current_academic_end = current_academic_start + 1
        # Année scolaire suivante (ex: 2025-2026)
        next_academic_start = current_academic_start + 1
        next_academic_end = next_academic_start + 1
        # -------------------------------------------------------------

        # --- Tentative pour l'année scolaire EN COURS ---
        self.logger.info(f"--- Tentative chargement vacances pour {current_academic_start}-{current_academic_end} ---")
        ics_to_parse_current = self._try_load_specific_academic_year(
            zone, local_ics_path, manual_ics_base_url,
            current_academic_start, current_academic_end,
            "temp_vacances_current.ics" # Nom de fichier temporaire spécifique
        )

        # --- Tentative pour l'année scolaire SUIVANTE ---
        self.logger.info(f"--- Tentative chargement vacances pour {next_academic_start}-{next_academic_end} ---")
        ics_to_parse_next = self._try_load_specific_academic_year(
            zone, None, manual_ics_base_url, # Ne pas utiliser le local_ics_path pour la suivante
            next_academic_start, next_academic_end,
            "temp_vacances_next.ics" # Nom de fichier temporaire différent
        )

        # --- Parser les fichiers trouvés ---
        all_parsed_vacations = []
        if ics_to_parse_current:
            self.logger.info(f"Analyse ICS année courante: {ics_to_parse_current}")
            parsed_current = self._parse_ics_file(ics_to_parse_current)
            if parsed_current: all_parsed_vacations.extend(parsed_current) # Ajouter à la liste globale
            else: self.logger.error("Échec analyse ICS année courante.")
        if ics_to_parse_next:
            self.logger.info(f"Analyse ICS année suivante: {ics_to_parse_next}")
            parsed_next = self._parse_ics_file(ics_to_parse_next)
            if parsed_next: all_parsed_vacations.extend(parsed_next)
            else: self.logger.error("Échec analyse ICS année suivante.")

        # Trier et stocker le résultat combiné
        all_parsed_vacations.sort(key=lambda x: x["debut"])
        self._vacations = all_parsed_vacations

        if not self._vacations: self.logger.warning("Aucune donnée de vacances chargée.")
        else: self.logger.info(f"Total de {len(self._vacations)} périodes de vacances chargées pour les années scolaires.")


    # --- NOUVELLE MÉTHODE HELPER pour charger une année scolaire spécifique ---
    def _try_load_specific_academic_year(self, zone, local_path, manual_url, start_year, end_year, temp_filename):
        """Tente de trouver/télécharger l'ICS pour une année scolaire et retourne le chemin si trouvé."""
        ics_to_use = None
        download_attempted = False
        download_success = False
        temp_save_path = os.path.join(self.cache_directory, temp_filename) if self.cache_directory else None

        # 1. Utiliser le chemin local s'il correspond (on ne le fait que pour l'année courante via l'appel externe)
        if local_path and os.path.isfile(local_path):
             self.logger.info(f"Utilisation fichier local fourni pour {start_year}-{end_year}: {local_path}")
             ics_to_use = local_path

        # 2. Si pas de local, tenter téléchargement
        if not ics_to_use:
            if not temp_save_path: self.logger.error("Chemin temp invalide, téléchargement impossible."); return None
            url_base = manual_url if manual_url else VACANCES_ICS_BASE_URL
            self.logger.info(f"Tentative DL auto ({start_year}-{end_year}) depuis: {url_base}")
            if not zone or zone not in ["A", "B", "C", "Corse"]: self.logger.warning(f"Zone invalide ('{zone}'), DL impossible."); return None # Retourner None si zone invalide

            download_attempted = True
            ics_filename = f"Zone{zone}-{start_year}-{end_year}.ics"
            download_url = url_base.strip('/') + '/' + ics_filename
            if self._download_ics(download_url, temp_save_path): ics_to_use = temp_save_path; download_success = True
            else: self.logger.warning(f"Échec DL {download_url}.")

        # 3. Si DL échoué, tenter ancien fichier temp
        if not ics_to_use and download_attempted and not download_success:
             if temp_save_path and os.path.exists(temp_save_path):
                  self.logger.warning(f"Tentative utilisation ancien fichier temp: {temp_save_path}"); ics_to_use = temp_save_path
             else: self.logger.error(f"Ni DL ni fichier temp dispo pour {start_year}-{end_year}.")

        return ics_to_use # Retourne le chemin du fichier à parser, ou None


    def _parse_ics_file(self, ics_file_path):
        """ Analyse fichier ICS. Retourne la LISTE des vacances trouvées ou None si erreur. """
        if not ICALENDAR_AVAILABLE or self.Calendar is None: self.logger.error("icalendar non dispo."); return None
        if not os.path.isfile(ics_file_path): self.logger.error(f"Fichier ICS à parser introuvable: {ics_file_path}"); return None
        
        parsed_vacations = []; count=0; ignored=0
        try:
            with open(ics_file_path, 'rb') as f: cal = self.Calendar.from_ical(f.read())
            for comp in cal.walk('VEVENT'):
                # ... (logique de parsing existante) ...
                count += 1; summary = comp.get('summary'); dtstart = comp.get('dtstart'); dtend = comp.get('dtend')
                if summary and dtstart and dtend:
                    start_dt = dtstart.dt; end_dt = dtend.dt
                    if isinstance(start_dt, datetime): start_dt = start_dt.date()
                    if isinstance(end_dt, datetime): end_dt = end_dt.date() - timedelta(days=1) if end_dt.time() == datetime.min.time() else end_dt.date()
                    elif isinstance(end_dt, date): end_dt = end_dt - timedelta(days=1)
                    if isinstance(start_dt, date) and isinstance(end_dt, date) and end_dt >= start_dt:
                        parsed_vacations.append({"debut": start_dt, "fin": end_dt, "description": str(summary)}) # Ajouter à la liste locale
                    else: ignored += 1; self.logger.warning(f"ICS VEVENT invalide ignoré: {summary} / {start_dt} / {end_dt}")
                else: ignored += 1; self.logger.warning(f"ICS VEVENT incomplet ignoré: {comp}")
            
            self.logger.info(f"{len(parsed_vacations)} périodes vacances parsées depuis {os.path.basename(ics_file_path)} ({count} VEVENTs, {ignored} ignorés).")
            return parsed_vacations # Retourner la liste parsée
        except Exception as e:
            self.logger.error(f"Erreur analyse ICS {os.path.basename(ics_file_path)}: {e}", exc_info=True)
            return None # Retourner None en cas d'erreur

    # --- Méthodes de consultation ---
    def is_holiday(self, target_date):
        if isinstance(target_date, datetime): target_date = target_date.date()
        elif not isinstance(target_date, date): return False
        return target_date in self._holidays

    def get_holiday_description(self, target_date):
        if isinstance(target_date, datetime): target_date = target_date.date()
        return self._holidays.get(target_date)

    def is_vacation(self, target_date):
        if isinstance(target_date, datetime): target_date = target_date.date()
        elif not isinstance(target_date, date): return False
        for vac in self._vacations:
            if vac["debut"] <= target_date <= vac["fin"]: return True
        return False

    def get_vacation_info(self, target_date):
        if isinstance(target_date, datetime): target_date = target_date.date()
        elif not isinstance(target_date, date): return None
        for vac in self._vacations:
            if vac["debut"] <= target_date <= vac["fin"]: return vac.copy()
        return None

    # --- get_day_type_and_desc (CORRIGÉ - Indentation et Priorité) ---
    def get_day_type_and_desc(self, target_date, weekly_planning, planning_exceptions):
        """ Détermine type/description (Priorité: Excep > Férié > Vacances > Hebdo). """
        if isinstance(target_date, datetime): target_date = target_date.date()
        elif not isinstance(target_date, date): return {"type": "Erreur", "description": "Date invalide", "schedule_name": None}
        self.logger.debug(f"Détermination type/desc pour: {target_date}"); date_str = target_date.strftime('%Y-%m-%d')

        # 1. Exceptions
        if planning_exceptions and date_str in planning_exceptions:
            details = planning_exceptions[date_str]; action = details.get("action"); jt_name = details.get("journee_type")
            desc = details.get("description", "") or (f"Exception: {action.upper()}" + (f" ({jt_name})" if jt_name else ""))
            self.logger.debug(f"-> Trouvé: Exception {action}")
            if action == "silence": return {"type": "Exception (Silence)", "description": desc, "schedule_name": None}
            elif action == "utiliser_jt": return {"type": "Exception (utiliser_jt)", "description": desc, "schedule_name": jt_name}
            else: self.logger.warning(f"Action exception inconnue {date_str}: {details}. -> Silence"); return {"type": "Exception (Silence)", "description": "Silence (Exception Inconnue)", "schedule_name": None}

        # 2. Férié (AVANT Vacances)
        holiday_desc = self.get_holiday_description(target_date)
        self.logger.debug(f"-> Check Férié: Résultat = {holiday_desc}")
        if holiday_desc:
            self.logger.debug(f"-> Trouvé: Férié ({holiday_desc}).")
            return {"type": "Férié", "description": holiday_desc, "schedule_name": None}

        # 3. Vacances (APRÈS Férié)
        vac_info = self.get_vacation_info(target_date)
        self.logger.debug(f"-> Check Vacances: Résultat = {vac_info}")
        if vac_info:
            desc = vac_info['description']; self.logger.debug(f"-> Trouvé: Vacances ({desc}).")
            return {"type": "Vacances", "description": desc, "schedule_name": None}

        # 4. Planning Hebdo / Weekend
        day_index = target_date.weekday(); day_keys = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]; current_day_key = day_keys[day_index] if 0 <= day_index < len(day_keys) else None
        self.logger.debug(f"-> Check Hebdo/WE: Jour={current_day_key} (Index={day_index})")

        # Valeurs par défaut si pas de planning spécifique ou jour non trouvé
        default_type = "Weekend"
        default_description = "Weekend (Par défaut)"
        default_schedule_name = None
        if day_index < 5: # Pour les jours de semaine par défaut sans config spécifique
            default_description = "Aucun planning (Défaut Semaine)"


        if weekly_planning and current_day_key in weekly_planning:
            schedule_name_from_config = weekly_planning[current_day_key]
            self.logger.debug(f"   -> Hebdo défini pour {current_day_key}: '{schedule_name_from_config}'")

            # Vérifier si le nom est None, vide, ou "Aucune" (insensible à la casse)
            if schedule_name_from_config is None or \
               schedule_name_from_config.strip() == "" or \
               schedule_name_from_config.strip().lower() == "aucune":
                self.logger.debug(f"   -> Trouvé: '{schedule_name_from_config}' interprété comme Weekend/Aucun planning.")
                return {"type": "Weekend", "description": "Weekend", "schedule_name": None}
            else:
                # C'est un nom de journée type valide
                self.logger.debug(f"   -> Trouvé: Classe via Hebdo (JT: {schedule_name_from_config}).")
                return {"type": f"Classe ({schedule_name_from_config})", "description": f"Planning: {schedule_name_from_config}", "schedule_name": schedule_name_from_config}
        else:
            # Cas où le jour n'est pas du tout dans weekly_planning (ex: Samedi/Dimanche non explicitement définis)
            # ou si weekly_planning est vide/None.
            self.logger.info(f"-> Jour '{current_day_key}' non trouvé dans weekly_planning ou planning vide. Utilisation type/description par défaut pour ce jour.")
            # Le comportement par défaut pour les jours de semaine sans config est "Aucun Planning (Défaut Semaine)" -> type "Weekend"
            # Le comportement par défaut pour Samedi/Dimanche est "Weekend (Par défaut)" -> type "Weekend"
            return {"type": default_type, "description": default_description, "schedule_name": default_schedule_name}

    # --- Méthodes pour API ---
    def get_holidays(self): return sorted(self._holidays.items())
    def get_vacation_periods(self):
        return [{"debut": p["debut"].isoformat(), "fin": p["fin"].isoformat(), "description": p["description"]}
                for p in sorted(self._vacations, key=lambda x: x['debut'])] # Trié pour l'API