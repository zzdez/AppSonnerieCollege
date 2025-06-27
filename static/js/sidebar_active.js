document.addEventListener("DOMContentLoaded", function() {
    // Obtenir le chemin de la page actuelle
    const currentPath = window.location.pathname;

    // Obtenir tous les liens dans le sidebar
    const sidebarLinks = document.querySelectorAll('#sidebar-wrapper .list-group-item');

    sidebarLinks.forEach(link => {
        const linkPath = new URL(link.href).pathname;

        // Retirer la classe 'active' de tous les liens pour commencer
        link.classList.remove('active');

        // Gérer le cas de la page d'accueil ('/' et '/control')
        if ((currentPath === '/' || currentPath === '/control') && (linkPath === '/' || linkPath === '/control')) {
            link.classList.add('active');
        } 
        // Gérer les autres pages
        else if (linkPath !== '/' && linkPath !== '/control' && currentPath.startsWith(linkPath)) {
            link.classList.add('active');
        }
    });
});