# scheduler.py

import threading
import time
from datetime import datetime, time as dt_time, date, timedelta
import os
import sys        # Pour sys.executable
import subprocess # Pour lancer les sons
import logging    # Importer pour type hint

# Import nécessaire pour la classe HolidayManager (pour type hinting si besoin)
try:
    from holiday_manager import HolidayManager as HolidayManagerType
except ImportError:
    HolidayManagerType = None # Type générique si import échoue

class SchedulerManager:
    """
    Gère la planification et le déclenchement des sonneries dans un thread séparé.
    Déclenche les sonneries via des sous-processus pour isoler pygame.
    Recherche la prochaine sonnerie au-delà du jour courant si nécessaire.
    """
    def __init__(self, day_types_config: dict, weekly_planning_config: dict, exceptions_config: dict,
                 holiday_manager: HolidayManagerType, mp3_path: str, logger: logging.Logger, audio_device_name: str = None):
        """
        Initialise le SchedulerManager.
        Args:
            day_types_config: Configuration des journées types.
            weekly_planning_config: Planning hebdo (jour -> nom journée type).
            exceptions_config: Exceptions de planning par date.
            holiday_manager: Instance pour vérifier vacances et fériés.
            mp3_path: Chemin vers le dossier des fichiers MP3.
            logger: Instance du logger.
            audio_device_name: Nom du périphérique audio à utiliser pour les sonneries.
        """
        self.logger = logger
        self.logger.info("Initialisation de SchedulerManager...")

        self.config_lock = threading.Lock()
        self.day_types = day_types_config
        self.weekly_planning = weekly_planning_config
        self.planning_exceptions = exceptions_config
        self.holiday_manager = holiday_manager
        self.mp3_path = mp3_path
        self.audio_device_name = audio_device_name
        self.logger.info(f"Audio device name configured: {self.audio_device_name}")

        self._running = False
        self._stop_event = threading.Event()
        self._force_recheck = threading.Event()
        self._last_checked_date = None
        self._current_schedule_for_today = []
        self._current_day_type_info = {}
        self._next_ring_info = {"time": None, "label": None, "event_type": None, "sonnerie": None}
        self._last_error = None
        self._lookahead_limit_days = 60

        if not isinstance(holiday_manager, HolidayManagerType if HolidayManagerType else object):
             self.logger.error("HolidayManager invalide passé à SchedulerManager ! Les types de jours seront incorrects.")
        self.logger.info("SchedulerManager initialisé.")

    def start(self):
        self.logger.info("Activation du scheduler demandée.")
        if not self._running:
            self._running = True
            self._last_error = None
            self._force_recheck.set()
            self.logger.info("Scheduler activé. Vérification planning en cours...")
        else:
            self.logger.warning("Scheduler déjà actif.")

    def stop(self):
        self.logger.info("Désactivation du scheduler demandée.")
        if self._running:
            self._running = False
            self._next_ring_info = {"time": None, "label": None, "event_type": None, "sonnerie": None}
            self.logger.info("Scheduler désactivé (flag _running=False).")
        else:
            self.logger.warning("Scheduler déjà inactif.")

    def shutdown(self):
        self.logger.info("Arrêt complet scheduler demandé.")
        self.stop()
        self._stop_event.set()
        self._force_recheck.set()

    def reload_schedule(self, day_types_config, weekly_planning_config, exceptions_config, holiday_manager_instance, audio_device_name: str = None):
        self.logger.info("Rechargement configuration scheduler demandé.")
        with self.config_lock:
            self.day_types = day_types_config
            self.weekly_planning = weekly_planning_config
            self.planning_exceptions = exceptions_config
            self.holiday_manager = holiday_manager_instance
            self.audio_device_name = audio_device_name
            self.logger.info(f"Scheduler reloaded audio device name: {self.audio_device_name}")
            self._last_checked_date = None
            self._next_ring_info = {"time": None, "label": None, "event_type": None, "sonnerie": None}
            self._force_recheck.set()
            self.logger.info("Config scheduler rechargée. Recalcul forcé.")

    def is_running(self):
        return self._running

    def get_next_ring_time_iso(self):
        next_time = self._next_ring_info.get("time")
        return next_time.isoformat() if next_time and isinstance(next_time, datetime) else None

    def get_next_ring_label(self):
        return self._next_ring_info.get("label")

    def get_last_error(self):
        return self._last_error

    def run(self):
        self.logger.info("Thread Scheduler démarré. En attente d'activation...")
        while not self._stop_event.is_set():
            try:
                if self._running:
                    now = datetime.now()
                    today = now.date()

                    if today != self._last_checked_date or self._force_recheck.is_set():
                        self.logger.info(f"Recalcul planning pour {today} (Change={today != self._last_checked_date}, Force={self._force_recheck.is_set()})...")
                        with self.config_lock:
                            current_day_types = self.day_types
                            current_weekly_planning = self.weekly_planning
                            current_exceptions = self.planning_exceptions

                        self._current_day_type_info = self.holiday_manager.get_day_type_and_desc(today, current_weekly_planning, current_exceptions)
                        self._current_schedule_for_today = self._generate_daily_events(self._current_day_type_info, current_day_types, today)
                        self._last_checked_date = today
                        self._force_recheck.clear()

                        next_today = self._find_next_upcoming_event(now)
                        if next_today.get("time"):
                            self._next_ring_info = next_today
                            self.logger.info(f"Planning OK. Prochaine AUJOURD'HUI: {self.get_next_ring_label()} à {self.get_next_ring_time_iso()}")
                        else:
                            self.logger.info("Aucune sonnerie restante aujourd'hui. Recherche jours suivants...")
                            # Note: _find_absolute_next_event est déjà loggué en interne
                            next_absolute = self._find_absolute_next_event(today + timedelta(days=1))
                            # Log après l'appel pour confirmer ce qui a été affecté
                            self.logger.info(f"Recherche absolue (appelée depuis run) terminée. Prochain assigné: {next_absolute.get('time')}")
                            self._next_ring_info = next_absolute # Affectation de la prochaine sonnerie

                            if self._next_ring_info.get("time"): # Vérifier après affectation
                                self.logger.info(f"Planning OK. Prochaine ABSOLUE: {self.get_next_ring_label()} à {self.get_next_ring_time_iso()}")
                            else:
                                self.logger.info(f"Planning OK. Aucune sonnerie future trouvée (limite: {self._lookahead_limit_days}j).")

                    next_event_time = self._next_ring_info.get("time")
                    if next_event_time and isinstance(next_event_time, datetime) and now >= next_event_time:
                        event_to_ring = None
                        with self.config_lock:
                            if self._next_ring_info and self._next_ring_info.get("time") == next_event_time:
                                event_to_ring = self._next_ring_info.copy()
                                next_today = self._find_next_upcoming_event(now + timedelta(seconds=1))
                                if not next_today.get("time"):
                                    self.logger.info("Dernière sonnerie d'aujourd'hui terminée. Recherche jours suivants...")
                                    next_absolute = self._find_absolute_next_event(today + timedelta(days=1))
                                    self.logger.info(f"Recherche absolue (pendant sonnerie) terminée. Prochain assigné: {next_absolute.get('time')}")
                                    self._next_ring_info = next_absolute
                                else:
                                    self._next_ring_info = next_today

                        if event_to_ring:
                            self.logger.debug(f"-> Cycle {now.strftime('%H:%M:%S.%f')}: Prêt à déclencher event_to_ring = {event_to_ring.get('label')}")
                            self.logger.info(f"HEURE DE SONNER ! Événement: {event_to_ring.get('label', '?')} prévu à {event_to_ring.get('time').strftime('%Y-%m-%d %H:%M:%S') if event_to_ring.get('time') else 'N/A'}")
                            self._play_ring(event_to_ring)
                            self.logger.debug(f"-> Cycle {now.strftime('%H:%M:%S.%f')}: Appel à _play_ring terminé pour -> {event_to_ring.get('label', '?')}")

                            next_ring_time_iso_after_play = self.get_next_ring_time_iso()
                            next_ring_label_after_play = self.get_next_ring_label()
                            if next_ring_time_iso_after_play:
                                self.logger.info(f"Prochaine sonnerie màj (après sonnerie): {next_ring_label_after_play or '?'} à {next_ring_time_iso_after_play}")
                            else:
                                self.logger.info(f"Aucune autre sonnerie future trouvée (après sonnerie).")
                        else:
                            self.logger.debug(f"-> Cycle {now.strftime('%H:%M:%S.%f')}: Pas d'événement à sonner pour cette itération (event_to_ring est None).")

                    # else: # Optionnel: log si pas l'heure
                    #     if next_event_time:
                    #         self.logger.debug(f"-> Cycle {now.strftime('%H:%M:%S.%f')}: Pas encore l'heure pour '{self._next_ring_info.get('label', 'N/A')}' (prévu {next_event_time.strftime('%H:%M:%S')})")
                    #     else:
                    #         self.logger.debug(f"-> Cycle {now.strftime('%H:%M:%S.%f')}: Aucune prochaine sonnerie définie.")

                else: # Pas _running
                    self.logger.debug(f"Scheduler inactif. Attente _force_recheck (timeout 5s)...")
                    self._force_recheck.wait(timeout=5)

            except Exception as e:
                self.logger.error(f"Erreur boucle scheduler: {e}", exc_info=True)
                self._last_error = f"{datetime.now():%H:%M:%S}: {e}"
                self._stop_event.wait(15)

            if self._running:
                sleep_duration = 1.0
                next_ring_dt = self._next_ring_info.get("time")
                if next_ring_dt and isinstance(next_ring_dt, datetime):
                    now_sleep = datetime.now()
                    if next_ring_dt > now_sleep:
                        time_to = (next_ring_dt - now_sleep).total_seconds()
                        sleep_duration = max(0.05, min(1.0, time_to - 0.05))
                    else:
                        sleep_duration = 0.05
                        self._force_recheck.set()

                woken = self._stop_event.wait(timeout=sleep_duration)
                if woken:
                    self.logger.debug("Scheduler réveillé par événement (stop_event ou force_recheck).") # Modifié
            # Si pas _running, la boucle attendra au début du prochain tour via _force_recheck.wait()

        self.logger.info("Thread Scheduler run() terminé.")

    def _find_absolute_next_event(self, start_search_date: date):
        self.logger.info(f"----> Entrée _find_absolute_next_event: start_search_date={start_search_date}, limite={self._lookahead_limit_days}j")
        next_event = {"time": None, "label": None, "event_type": None, "sonnerie": None}
        current_check = start_search_date # Utilisons current_check, c'est bien

        try:
            current_weekly_planning = self.weekly_planning.copy()
            current_exceptions = self.planning_exceptions.copy()
            current_day_types = self.day_types.copy()
            self.logger.debug(f"----> _find_absolute: Copies config OK. (day_types contient {len(current_day_types)} journees)")

            for i in range(self._lookahead_limit_days):
                self.logger.debug(f"----> _find_absolute: Boucle jour {i+1}/{self._lookahead_limit_days}, date={current_check}")
                day_info = self.holiday_manager.get_day_type_and_desc(current_check, current_weekly_planning, current_exceptions)
                self.logger.info(f"----> _find_absolute: Pour date {current_check}, holiday_manager dit: {day_info}")

                schedule_name_for_day = day_info.get("schedule_name")
                # Commentaire log optionnel
                # if schedule_name_for_day:
                #    self.logger.debug(f"----> _find_absolute: Appel _generate_daily_events pour {current_check} avec schedule_name='{schedule_name_for_day}' et day_types = {list(current_day_types.keys())}")

                daily_schedule = self._generate_daily_events(day_info, current_day_types, current_check)
                self.logger.info(f"----> _find_absolute: Pour date {current_check} (schedule_name='{schedule_name_for_day}'), _generate_daily_events a retourné {len(daily_schedule)} événements.")

                if daily_schedule: # Si des événements sont trouvés pour ce jour
                    first_event = daily_schedule[0]
                    next_event = first_event.copy()
                    self.logger.info(f"----> _find_absolute: Prochaine absolue TROUVÉE: {next_event.get('label')} le {current_check} à {next_event.get('time').strftime('%H:%M:%S') if next_event.get('time') else 'Heure inconnue'}")
                    break # Sortir de la boucle for, nous avons trouvé

                # SI ON ARRIVE ICI, c'est que daily_schedule était vide pour current_check.
                # Il faut donc passer au jour suivant POUR LA PROCHAINE ITÉRATION de la boucle for.
                current_check += timedelta(days=1) # <--- CETTE LIGNE EST CRUCIALE ET DOIT ÊTRE ICI

            # Ce 'else' est attaché au 'for'. Il s'exécute si la boucle 'for' s'est terminée
            # naturellement (c'est-à-dire sans rencontrer de 'break').
            else:
                self.logger.warning(f"----> _find_absolute: Aucune sonnerie trouvée dans les {self._lookahead_limit_days} prochains jours (boucle for terminée sans break).")

        except Exception as e:
             self.logger.error(f"!!!! ERREUR dans _find_absolute_next_event pour start={start_search_date}: {e}", exc_info=True)
             next_event = {"time": None, "label": None, "event_type": None, "sonnerie": None} # Assurer un retour par défaut

        self.logger.info(f"----> Sortie _find_absolute_next_event: Retourne: {next_event.get('time')}")
        return next_event

    def _generate_daily_events(self, day_type_info: dict, current_day_types: dict, date_for_events: date):
        schedule_name = day_type_info.get("schedule_name")
        if not schedule_name: return []
        day_schedule_config = current_day_types.get(schedule_name)
        if not day_schedule_config:
            self.logger.error(f"JT '{schedule_name}' ({date_for_events}) non trouvée."); return []

        periods = day_schedule_config.get("periodes", []); daily_events = []
        # self.logger.debug(f"Génération événements pour JT '{schedule_name}' ({date_for_events})") # Un peu verbeux
        for p in periods:
            nom = p.get("nom", "?"); h_deb_str = p.get("heure_debut"); h_fin_str = p.get("heure_fin")
            s_deb = p.get("sonnerie_debut"); s_fin = p.get("sonnerie_fin")
            try:
                 if h_deb_str: daily_events.append({"time": datetime.combine(date_for_events, dt_time.fromisoformat(h_deb_str)), "label": f"Début {nom}", "event_type": "debut", "sonnerie": s_deb})
                 if h_fin_str: daily_events.append({"time": datetime.combine(date_for_events, dt_time.fromisoformat(h_fin_str)), "label": f"Fin {nom}", "event_type": "fin", "sonnerie": s_fin})
            except ValueError as e_time: self.logger.warning(f"Format heure invalide '{h_deb_str or h_fin_str}' JT '{schedule_name}': {e_time}")
        daily_events.sort(key=lambda x: x["time"])
        # self.logger.debug(f"{len(daily_events)} événements générés triés pour {date_for_events}.") # Un peu verbeux
        return daily_events

    def _find_next_upcoming_event(self, from_time: datetime):
        next_event = {"time": None, "label": None, "event_type": None, "sonnerie": None}
        for event in self._current_schedule_for_today:
            if event["time"] >= from_time:
                next_event = event.copy()
                # self.logger.debug(f"Prochain event (today >= {from_time}): {next_event['label']} à {next_event['time']}") # Un peu verbeux
                break
        # else: self.logger.debug(f"Aucun événement trouvé aujourd'hui après {from_time}.") # Un peu verbeux
        return next_event

    def _play_ring(self, event_details: dict):
        filename = event_details.get("sonnerie")
        label = event_details.get("label", "?")
        event_time_str = event_details.get("time").strftime('%Y-%m-%d %H:%M:%S') if event_details.get("time") else "Heure Inconnue"

        self.logger.debug(f"---> Entrée _play_ring pour event '{label}' ({event_time_str}), son: '{filename}'")

        if not filename:
            self.logger.info(f"---> Event '{label}' ({event_time_str}): SILENCE (pas de fichier son configuré).")
            return

        try:
            if not self.mp3_path or not os.path.isdir(self.mp3_path):
                 self.logger.error(f"---> _play_ring ERREUR: MP3_PATH invalide ou non défini: '{self.mp3_path}'")
                 self._last_error = f"{datetime.now():%H:%M:%S}: MP3_PATH invalide"
                 return
            sound_path = os.path.join(self.mp3_path, filename)
        except Exception as e_path:
            self.logger.error(f"---> _play_ring ERREUR construction chemin son pour '{filename}': {e_path}", exc_info=True)
            self._last_error = f"{datetime.now():%H:%M:%S}: Erreur chemin {filename}"
            return

        self.logger.info(f"---> Play '{label}' ({event_time_str}) via subprocess: '{sound_path}'")

        if not os.path.isfile(sound_path):
            self.logger.error(f"---> SONNERIE INTROUVABLE (fichier physique): '{sound_path}' pour event '{label}'")
            self._last_error = f"{datetime.now():%H:%M:%S}: Fichier {filename} introuvable"
            return

        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            backend_script = os.path.join(script_dir, 'backend_server.py')

            if not os.path.exists(backend_script):
                self.logger.error(f"---> _play_ring ERREUR: Script backend_server.py non trouvé à '{backend_script}'")
                self._last_error = f"{datetime.now():%H:%M:%S}: Erreur interne: backend_server.py absent"
                return

            cmd = [sys.executable, backend_script, '--play-sound', sound_path]
            if self.audio_device_name:
                cmd.extend(['--device', self.audio_device_name])
                self.logger.info(f"---> Using audio device: {self.audio_device_name}")
            else:
                self.logger.info("---> No specific audio device configured for scheduler, using system default.")
            self.logger.debug(f"---> Commande son: {' '.join(cmd)}")
            flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0

            # Correction: Ajout de errors='replace' pour gérer les erreurs de décodage
            process = subprocess.Popen(cmd, creationflags=flags, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
            self.logger.info(f"---> Subprocess lancé pour jouer '{filename}'. PID: {process.pid}")
            try:
                stdout, stderr = process.communicate(timeout=15) # Read output, timeout 15s
                if stdout:
                    self.logger.info(f"---> Output from sound process (PID {process.pid}):\n{stdout.strip()}")
                if stderr:
                    self.logger.error(f"---> Errors from sound process (PID {process.pid}):\n{stderr.strip()}")
            except subprocess.TimeoutExpired:
                self.logger.error(f"---> Timeout waiting for sound process (PID {process.pid}) to complete. Killing.")
                process.kill()
                stdout, stderr = process.communicate() # Try to get any remaining output
                if stdout:
                    self.logger.info(f"---> Output (post-kill) from sound process (PID {process.pid}):\n{stdout.strip()}")
                if stderr:
                    self.logger.error(f"---> Errors (post-kill) from sound process (PID {process.pid}):\n{stderr.strip()}")
            self.logger.info(f"---> Sound process (PID {process.pid}) finished with code: {process.returncode}")

        except Exception as e_sub:
            self.logger.error(f"---> _play_ring ERREUR lancement subprocess son '{filename}': {e_sub}", exc_info=True)
            self._last_error = f"{datetime.now():%H:%M:%S}: Erreur lecture {filename}: {e_sub}"

        self.logger.debug(f"---> Sortie _play_ring pour event '{label}'")

    def get_schedule_for_date(self, target_date: date):
        self.logger.debug(f"API request get_schedule_for_date: {target_date}")
        if isinstance(target_date, datetime): target_date = target_date.date()
        elif not isinstance(target_date, date): return {"error": "Type date invalide."}
        try:
            day_info = self.holiday_manager.get_day_type_and_desc(target_date, self.weekly_planning, self.planning_exceptions)
            schedule_name = day_info.get("schedule_name")
            # Utiliser directement le "type" de day_info pour le "day_type" de la réponse API.
            # day_desc est conservé pour le message s'il n'y a pas de planning détaillé.
            actual_day_type = day_info.get("type")
            day_desc_for_message = day_info.get("description", actual_day_type) # Message utilise la description ou le type

            day_schedule_config = self.day_types.get(schedule_name) if schedule_name else None

            if not schedule_name or not day_schedule_config:
                 self.logger.debug(f"API daily: Pas de planning détaillé pour {target_date} (type: {actual_day_type})")
                 # Le message peut rester day_desc_for_message, mais le day_type doit être actual_day_type
                 return {"message": day_desc_for_message, "schedule": [], "day_type": actual_day_type}
            else:
                 periods = day_schedule_config.get("periodes", []); api_schedule = []
                 for p in periods:
                     nom = p.get("nom", "?"); sd = p.get("sonnerie_debut"); sf = p.get("sonnerie_fin")
                     sd_disp = sd or "Silence"; sf_disp = sf or "Silence"
                     if p.get("heure_debut"): api_schedule.append({"time": p["heure_debut"], "event": f"Début {nom}", "sonnerie": sd_disp})
                     if p.get("heure_fin"): api_schedule.append({"time": p["heure_fin"], "event": f"Fin {nom}", "sonnerie": sf_disp})
                 try:
                     api_schedule.sort(key=lambda x: dt_time.fromisoformat(x["time"]))
                 except ValueError:
                     self.logger.warning(f"API daily: tri impossible pour {target_date}.")
                 self.logger.debug(f"API daily: Planning pour {target_date} généré ({len(api_schedule)} events). Type: {actual_day_type}")
                 # Utiliser actual_day_type pour le champ "day_type"
                 return {"schedule": api_schedule, "day_type": actual_day_type, "message": None}
        except Exception as e:
            self.logger.error(f"Erreur get_schedule_for_date API({target_date}): {e}", exc_info=True)
            return {"error": f"Erreur serveur: {e}"}