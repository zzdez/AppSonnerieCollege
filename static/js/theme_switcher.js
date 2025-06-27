/*!
  * Color mode toggler for Bootstrap's docs (https://getbootstrap.com/)
  * Copyright 2011-2023 The Bootstrap Authors
  * Licensed under the Creative Commons Attribution 3.0 Unported License.
  *
  * Ce script est adapté de la documentation officielle de Bootstrap.
  */

 (() => {
     'use strict'

     const getStoredTheme = () => localStorage.getItem('theme')
     const setStoredTheme = theme => localStorage.setItem('theme', theme)

     const getPreferredTheme = () => {
         const storedTheme = getStoredTheme()
         if (storedTheme) {
             return storedTheme
         }
         // Si pas de thème stocké, on utilise les préférences du système (mode 'auto')
         return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
     }

     const setTheme = theme => {
         if (theme === 'auto' && window.matchMedia('(prefers-color-scheme: dark)').matches) {
             document.documentElement.setAttribute('data-bs-theme', 'dark')
         } else {
             document.documentElement.setAttribute('data-bs-theme', theme)
         }
     }

     // Appliquer le thème immédiatement au chargement du script pour éviter le "flash"
     setTheme(getPreferredTheme())

     const showActiveTheme = (theme, focus = false) => {
         const themeSwitcher = document.querySelector('#themeDropdown')
         if (!themeSwitcher) {
             return
         }

         // Mettre à jour le texte du bouton principal (optionnel)
         // const themeSwitcherText = document.querySelector('#theme-text')
         // if (themeSwitcherText) { ... }

         // Retirer l'état actif de tous les boutons
         document.querySelectorAll('[data-bs-theme-value]').forEach(element => {
             element.classList.remove('active')
             element.setAttribute('aria-pressed', 'false')
         })

         // Ajouter l'état actif au bouton correspondant au thème choisi
         const btnToActivate = document.querySelector(`[data-bs-theme-value="${theme}"]`)
         if (btnToActivate) {
             btnToActivate.classList.add('active')
             btnToActivate.setAttribute('aria-pressed', 'true')
         }
     }

     // Mettre à jour l'icône et l'état actif quand le thème du système d'exploitation change
     window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
         const storedTheme = getStoredTheme()
         if (storedTheme !== 'light' && storedTheme !== 'dark') {
             setTheme(getPreferredTheme())
         }
     })

     // Mettre en place les listeners après le chargement complet du DOM
     window.addEventListener('DOMContentLoaded', () => {
         showActiveTheme(getPreferredTheme())

         document.querySelectorAll('[data-bs-theme-value]')
             .forEach(toggle => {
                 toggle.addEventListener('click', () => {
                     const theme = toggle.getAttribute('data-bs-theme-value')
                     setStoredTheme(theme)
                     setTheme(theme)
                     showActiveTheme(theme, true)
                 })
             })
     })
 })()
