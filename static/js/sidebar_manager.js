document.addEventListener("DOMContentLoaded", function() {
    try {
        // Obtenir le chemin de la page actuelle
        const currentPath = window.location.pathname;
        console.log("Sidebar Manager: Page actuelle ->", currentPath);

        // Obtenir tous les liens dans le sidebar
        const sidebarLinks = document.querySelectorAll('#sidebar-wrapper .list-group-item');

        if (sidebarLinks.length === 0) {
            console.warn("Sidebar Manager: Aucun lien de sidebar trouvé.");
            return;
        }

        let foundActive = false;

        sidebarLinks.forEach(link => {
            // S'assurer que le lien a une URL valide
            if (!link.href) return;

            const linkPath = new URL(link.href).pathname;

            // Retirer la classe 'active' de tous les liens pour commencer
            link.classList.remove('active');

            // Gérer le cas de la page d'accueil ('/' qui peut aussi être '/control')
            if ((currentPath === '/' || currentPath === '/control') && (linkPath === '/' || linkPath === '/control')) {
                link.classList.add('active');
                foundActive = true;
            }
            // Gérer les autres pages
            else if (linkPath !== '/' && linkPath !== '/control' && currentPath.startsWith(linkPath)) {
                link.classList.add('active');
                foundActive = true;
            }
        });

        if (foundActive) {
            console.log("Sidebar Manager: Lien actif trouvé et défini.");
        } else {
            console.warn("Sidebar Manager: Aucun lien correspondant à la page actuelle n'a été trouvé.");
        }

    } catch (error) {
        console.error("Erreur dans Sidebar Manager:", error);
    }
});