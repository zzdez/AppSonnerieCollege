// static/js/global_status.js

// Fonction pour mettre à jour l'UI du statut global
function updateGlobalStatusUI(data) {
    console.log("Mise à jour UI statut GLOBAL:", data);

    // Logique pour le switch scheduler-toggle-switch et son texte
    const schedulerToggle = document.getElementById('scheduler-toggle-switch');
    const schedulerStatusText = document.getElementById('scheduler-status-text'); // Récupérer le nouveau span

    if (schedulerToggle && schedulerStatusText) { // S'assurer que les deux éléments existent
        schedulerStatusText.textContent = ''; // Vider le texte précédent
        schedulerStatusText.classList.remove('bg-success', 'bg-warning', 'bg-secondary', 'bg-danger'); // Nettoyer les anciennes classes de couleur

        if (typeof data.scheduler_running === 'boolean') {
            schedulerToggle.checked = data.scheduler_running;
            schedulerToggle.disabled = false;

            if (data.scheduler_running) {
                schedulerStatusText.textContent = 'Actif';
                schedulerStatusText.classList.add('bg-success');
            } else {
                schedulerStatusText.textContent = 'Inactif';
                schedulerStatusText.classList.add('bg-secondary'); // Ou 'bg-warning'
            }
        } else {
            // En cas d'erreur ou de statut non booléen, on le décoche et le désactive
            schedulerToggle.checked = false;
            schedulerToggle.disabled = true;
            schedulerStatusText.textContent = 'Erreur';
            schedulerStatusText.classList.add('bg-danger');
            console.warn("Statut du scheduler non booléen, switch désactivé:", data.scheduler_running);
        }
    }

    // const nextRingSpan = document.getElementById('global-next-ring'); // ANCIENNE LIGNE
    const nextRingTimeSpan = document.getElementById('global-next-ring-time');
    const nextRingLabelSpan = document.getElementById('global-next-ring-label');

    if (nextRingTimeSpan && nextRingLabelSpan) {
        if (data.next_ring_time && data.scheduler_running === true) {
            try {
                const d = new Date(data.next_ring_time);
                if (!isNaN(d.getTime())) {
                    nextRingTimeSpan.textContent = d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
                    nextRingTimeSpan.className = '';
                    nextRingLabelSpan.textContent = `(${data.next_ring_label || '?'})`;
                    nextRingLabelSpan.className = ''; // ou une classe spécifique si besoin de style
                } else { throw new Error('Invalid date'); }
            } catch (e) {
                nextRingTimeSpan.textContent = 'Erreur date';
                nextRingTimeSpan.className = 'status-error';
                nextRingLabelSpan.textContent = '';
                console.error("Err date next_ring (global):", data.next_ring_time);
            }
        } else if (data.scheduler_running === false) {
            nextRingTimeSpan.textContent = 'N/A';
            nextRingTimeSpan.className = 'status-inactive';
            nextRingLabelSpan.textContent = '(Scheduler Inactif)';
            nextRingLabelSpan.className = 'status-inactive';
        } else if (typeof data.scheduler_running !== 'boolean') { // Erreur API
            nextRingTimeSpan.textContent = 'N/A';
            nextRingTimeSpan.className = 'status-error';
            nextRingLabelSpan.textContent = '(Erreur)';
            nextRingLabelSpan.className = 'status-error';
        } else { // Cas scheduler_running est true mais pas de next_ring_time
            nextRingTimeSpan.textContent = 'Aucune';
            nextRingTimeSpan.className = '';
            nextRingLabelSpan.textContent = '';
        }
    }

    const alertActiveSpan = document.getElementById('global-alert-active');
    if (alertActiveSpan) {
        if (typeof data.alert_active === 'boolean') {
            alertActiveSpan.textContent = data.alert_active ? 'Oui' : 'Non';
            alertActiveSpan.className = data.alert_active ? 'status-error' : 'status-ok'; // Erreur si active, ok si non
        } else {
            alertActiveSpan.textContent = String(data.alert_active);
            alertActiveSpan.className = 'status-error';
        }
    }

    const lastErrorSpan = document.getElementById('global-last-error');
    if (lastErrorSpan) {
        const errorText = data.last_error || 'Aucune';
        lastErrorSpan.textContent = errorText;
        lastErrorSpan.className = (!errorText || errorText.toLowerCase() === 'aucune' || errorText.toLowerCase() === 'n/a') ? 'status-ok' : 'status-error';
    }
}

// Fonction pour récupérer le statut depuis l'API
function fetchGlobalStatus() {
    console.log("Appel fetchGlobalStatus (API /api/status)...");
    fetch('/api/status')
        .then(response => {
            console.log("Réponse /api/status (global), statut:", response.status);
            if (!response.ok) {
                if (response.status === 401) { console.warn("Statut non récupéré (401), utilisateur probablement non connecté."); return null; }
                throw new Error(`Erreur HTTP status: ${response.status}`);
            }
            return response.json();
        })
        .then(data => {
            if (data) {
                updateGlobalStatusUI(data);
            } else if (data === null) { // Cas du 401
                 // Ne rien faire de spécial, la redirection login devrait gérer.
                 // On pourrait mettre un statut "Non connecté" si souhaité, mais attention aux boucles.
            }
        })
        .catch(error => {
            console.error("Erreur lors de la récupération/maj du statut GLOBAL:", error);
            updateGlobalStatusUI({
                scheduler_running: 'Erreur', next_ring_time: null, next_ring_label: 'Erreur',
                last_error: error.message || 'Erreur API', alert_active: 'Erreur'
            });
        });
}

// Initialisation au chargement du DOM
document.addEventListener('DOMContentLoaded', function() {
    const refreshButton = document.getElementById('refresh-status-btn');
    if (refreshButton) {
        refreshButton.addEventListener('click', function() {
            console.log("Bouton Rafraîchir Statut cliqué.");
            fetchGlobalStatus();
        });
    }

    const schedulerToggle = document.getElementById('scheduler-toggle-switch');
    if (schedulerToggle) {
        schedulerToggle.addEventListener('change', handleSchedulerToggleChange);
    }

    // Vérifier si l'utilisateur est sur la page de login pour ne pas lancer les appels fetch
    // Cela suppose que la page de login a un body avec une classe 'login-page' ou un ID spécifique.
    // Pour l'instant, on lance toujours, mais c'est une piste d'amélioration si des erreurs console apparaissent sur /login
    if (!document.body.classList.contains('login-page')) { // Adaptez 'login-page' si besoin
        fetchGlobalStatus(); // Charger le statut une première fois
        setInterval(fetchGlobalStatus, 15000); // Mettre à jour toutes les 15 secondes
    } else {
        console.log("Sur la page de login, initialisation du statut global différée.");
    }

    const reloadConfigSidebarBtn = document.getElementById('reload-config-sidebar-btn');
    if (reloadConfigSidebarBtn) {
        reloadConfigSidebarBtn.addEventListener('click', callReloadConfigAPI);
    }
});

// --- Fonctions de contrôle spécifiques au statut global ---

// Fonction pour gérer le changement d'état du switch du scheduler
function handleSchedulerToggleChange() {
    const schedulerToggle = document.getElementById('scheduler-toggle-switch');
    if (!schedulerToggle) return;

    const activate = schedulerToggle.checked; // Vrai si on veut activer, faux si désactiver
    const apiUrl = activate ? '/api/planning/activate' : '/api/planning/deactivate';
    const actionText = activate ? 'Activation' : 'Désactivation';

    console.log(`${actionText} du planning demandée...`);
    // Optionnel: Désactiver le switch pendant l'appel pour éviter les clics multiples
    // schedulerToggle.disabled = true; // Note: fetchGlobalStatus le réactivera via updateGlobalStatusUI

    fetch(apiUrl, { method: 'POST' })
        .then(response => response.json().then(data => ({ ok: response.ok, status: response.status, data })))
        .then(({ ok, status, data }) => {
            const message = data.message || (ok ? `${actionText} réussie.` : `Erreur ${status}`);
            if (ok) {
                console.log(message);
                // On pourrait avoir une fonction de feedback globale simple ici (ex: showGlobalFeedback('success', message))
            } else {
                console.error(`Erreur ${actionText}: ${data.error || data.message || message}`);
                // En cas d'erreur, l'état du switch pourrait ne pas correspondre au backend.
                // fetchGlobalStatus() ci-dessous resynchronisera pour corriger l'affichage du switch.
            }
        })
        .catch(error => {
            console.error(`Erreur réseau/serveur (${actionText}): ${error.message}`);
            // Afficher une erreur à l'utilisateur si possible
        })
        .finally(() => {
            // Toujours rafraîchir le statut pour refléter l'état réel du backend
            fetchGlobalStatus();
            // Le switch sera réactivé (ou désactivé correctement en cas d'erreur persistante) par updateGlobalStatusUI appelé par fetchGlobalStatus
        });
}

function callReloadConfigAPI() {
    console.log("Rechargement de la configuration demandé depuis le sidebar...");
    const btn = document.getElementById('reload-config-sidebar-btn');
    if(btn) btn.disabled = true;

    fetch('/api/config/reload', { method: 'POST' })
        .then(response => response.json().then(data => ({ ok: response.ok, data })))
        .then(({ ok, data }) => {
            const message = data.message || (ok ? `Configuration rechargée avec succès.` : `Erreur lors du rechargement de la configuration.`);
            if (ok) {
                console.info(message);
                // Ici, on pourrait appeler une fonction de feedback globale si elle existait
                // exemple: showGlobalNotification(message, 'success');
            } else {
                console.error(message);
                // exemple: showGlobalNotification(message, 'error');
            }
        })
        .catch(error => {
            console.error(`Erreur réseau lors du rechargement de la configuration: ${error.message}`);
            // exemple: showGlobalNotification(`Erreur réseau: ${error.message}`, 'error');
        })
        .finally(() => {
            // Toujours rafraîchir le statut global pour refléter tout changement potentiel (même si config reload ne change pas directement le statut)
            // et pour s'assurer que localUpdateStatusUI est appelé sur control.html si les settings ont changé.
            if (typeof fetchGlobalStatus === 'function') fetchGlobalStatus();

            // Important: Si la page control.html est ouverte, elle doit aussi recharger ses propres settings et son calendrier.
            // Cela pourrait être fait en émettant un événement personnalisé ici, que control.html écouterait.
            // Ou, si `fetchGlobalStatus` provoque l'appel de `localUpdateStatusUI` sur `control.html`,
            // `control.html` pourrait vérifier si `configSettings` a besoin d'être rechargé.
            // Pour l'instant, on se contente de rafraîchir le statut global.
            // Un rechargement complet de la page control.html pourrait être nécessaire pour voir les effets de la config.
            // ou un appel explicite à des fonctions de rechargement sur control.html.
            // Par exemple, si `control.html` a une fonction `refreshPageSpecificConfigsAndUI()`:
            // if (typeof refreshPageSpecificConfigsAndUI === 'function') { refreshPageSpecificConfigsAndUI(); }


            if(btn) btn.disabled = false; // Réactiver le bouton
        });
}
