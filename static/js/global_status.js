// static/js/global_status.js

// Fonction pour mettre à jour l'UI du statut global
function updateGlobalStatusUI(data) {
    console.log("Mise à jour UI statut GLOBAL:", data);

    const schedulerStatusSpan = document.getElementById('global-scheduler-status');
    if (schedulerStatusSpan) {
        if (typeof data.scheduler_running === 'boolean') {
            schedulerStatusSpan.textContent = data.scheduler_running ? 'Actif' : 'Inactif';
            schedulerStatusSpan.className = data.scheduler_running ? 'status-ok' : 'status-inactive';
        } else { // Gérer le cas où scheduler_running n'est pas un booléen (par ex. 'Erreur API')
            schedulerStatusSpan.textContent = String(data.scheduler_running); // Afficher la valeur telle quelle
            schedulerStatusSpan.className = 'status-error';
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

    // Vérifier si l'utilisateur est sur la page de login pour ne pas lancer les appels fetch
    // Cela suppose que la page de login a un body avec une classe 'login-page' ou un ID spécifique.
    // Pour l'instant, on lance toujours, mais c'est une piste d'amélioration si des erreurs console apparaissent sur /login
    if (!document.body.classList.contains('login-page')) { // Adaptez 'login-page' si besoin
        fetchGlobalStatus(); // Charger le statut une première fois
        setInterval(fetchGlobalStatus, 15000); // Mettre à jour toutes les 15 secondes
    } else {
        console.log("Sur la page de login, initialisation du statut global différée.");
    }
});
