/**
 * pwa.js — Cross-platform PWA management for Neurosync AI (Android & iOS)
 */

(function () {
  'use strict';

  // ─── Service Worker Registration ──────────────────────────────────────────
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker
        .register('/sw.js', { scope: '/' })
        .then((reg) => {
          console.log('[PWA] ServiceWorker registered with scope:', reg.scope);

          // Handle updates
          reg.addEventListener('updatefound', () => {
            const newWorker = reg.installing;
            if (newWorker) {
              newWorker.addEventListener('statechange', () => {
                if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
                  showUpdateToast(newWorker);
                }
              });
            }
          });
        })
        .catch((err) => {
          console.error('[PWA] ServiceWorker registration failed:', err);
        });

      // Reload once new service worker activates
      let refreshing = false;
      navigator.serviceWorker.addEventListener('controllerchange', () => {
        if (!refreshing) {
          refreshing = true;
          window.location.reload();
        }
      });
    });
  }

  // ─── Platform & Standalone Detection ──────────────────────────────────────
  const isIOS = /iphone|ipad|ipod/i.test(window.navigator.userAgent.toLowerCase());
  const isStandalone =
    window.matchMedia('(display-mode: standalone)').matches ||
    window.navigator.standalone === true;

  let deferredInstallPrompt = null;

  // ─── DOM Ready Setup ──────────────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', () => {
    initInstallUI();
    initOfflineBanner();
  });

  // ─── Install Prompt Handling (Android / Desktop) ──────────────────────────
  window.addEventListener('beforeinstallprompt', (e) => {
    // Prevent default browser banner
    e.preventDefault();
    deferredInstallPrompt = e;

    // Show custom install button if not already standalone
    if (!isStandalone) {
      const installBtn = document.getElementById('pwaInstallBtn');
      if (installBtn) {
        installBtn.style.display = 'flex';
      }
    }
  });

  window.addEventListener('appinstalled', () => {
    deferredInstallPrompt = null;
    const installBtn = document.getElementById('pwaInstallBtn');
    if (installBtn) {
      installBtn.style.display = 'none';
    }
    showToast('Neurosync AI was installed successfully!', 'success');
  });

  function initInstallUI() {
    const installBtn = document.getElementById('pwaInstallBtn');
    const iosModal = document.getElementById('iosInstallModal');
    const iosModalClose = document.getElementById('iosModalClose');

    if (!installBtn) return;

    // If already in standalone mode, hide install trigger
    if (isStandalone) {
      installBtn.style.display = 'none';
      return;
    }

    // For iOS Safari (which doesn't have beforeinstallprompt)
    if (isIOS) {
      installBtn.style.display = 'flex';
      installBtn.innerHTML = `
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
          <polyline points="7 10 12 15 17 10"></polyline>
          <line x1="12" y1="15" x2="12" y2="3"></line>
        </svg>
        <span>Install on iPhone</span>
      `;

      installBtn.addEventListener('click', (e) => {
        e.preventDefault();
        if (iosModal) {
          iosModal.classList.add('visible');
        }
      });
    } else {
      // Android / Chrome / Edge
      installBtn.addEventListener('click', async (e) => {
        e.preventDefault();
        if (!deferredInstallPrompt) {
          // If browser hasn't fired beforeinstallprompt yet
          showToast('To install, tap your browser menu (⋮) and select "Install app" or "Add to Home screen".', 'info');
          return;
        }
        deferredInstallPrompt.prompt();
        const choice = await deferredInstallPrompt.userChoice;
        if (choice.outcome === 'accepted') {
          installBtn.style.display = 'none';
        }
        deferredInstallPrompt = null;
      });
    }

    if (iosModalClose && iosModal) {
      iosModalClose.addEventListener('click', () => {
        iosModal.classList.remove('visible');
      });
      iosModal.addEventListener('click', (e) => {
        if (e.target === iosModal) {
          iosModal.classList.remove('visible');
        }
      });
    }
  }

  // ─── Network Status Monitoring (Online / Offline) ─────────────────────────
  function initOfflineBanner() {
    const banner = document.getElementById('pwaNetworkBanner');
    if (!banner) return;

    function updateStatus() {
      if (!navigator.onLine) {
        banner.classList.add('visible', 'offline');
        banner.classList.remove('online');
        banner.innerHTML = `
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="1" y1="1" x2="23" y2="23"></line>
            <path d="M16.72 11.06A10.94 10.94 0 0 1 19 12.55"></path>
            <path d="M5 12.55a10.94 10.94 0 0 1 5.17-2.39"></path>
            <line x1="12" y1="20" x2="12.01" y2="20"></line>
          </svg>
          <span>Offline mode — Showing cached data</span>
        `;
      } else {
        // If it was previously offline, show a brief "Back online" toast
        if (banner.classList.contains('offline')) {
          banner.classList.remove('offline');
          banner.classList.add('online');
          banner.innerHTML = `
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M20 6L9 17l-5-5"></path>
            </svg>
            <span>Back online — Live telemetry resumed</span>
          `;
          setTimeout(() => {
            banner.classList.remove('visible', 'online');
          }, 4000);
        } else {
          banner.classList.remove('visible');
        }
      }
    }

    window.addEventListener('online', updateStatus);
    window.addEventListener('offline', updateStatus);

    if (!navigator.onLine) {
      updateStatus();
    }
  }

  // ─── Update Prompt ────────────────────────────────────────────────────────
  function showUpdateToast(worker) {
    let updateToast = document.getElementById('pwaUpdateToast');
    if (!updateToast) {
      updateToast = document.createElement('div');
      updateToast.id = 'pwaUpdateToast';
      updateToast.className = 'pwa-update-toast';
      document.body.appendChild(updateToast);
    }
    updateToast.innerHTML = `
      <span>An update is available for Neurosync AI</span>
      <button id="pwaReloadBtn" class="pwa-update-btn">Update Now</button>
    `;
    updateToast.classList.add('visible');

    document.getElementById('pwaReloadBtn').addEventListener('click', () => {
      worker.postMessage({ type: 'SKIP_WAITING' });
    });
  }

  // ─── Helper Toast ─────────────────────────────────────────────────────────
  function showToast(msg, type = 'info') {
    const flashContainer = document.getElementById('flashContainer') || createFlashContainer();
    const flash = document.createElement('div');
    flash.className = `flash flash-${type}`;
    flash.innerHTML = `
      <span>${msg}</span>
      <button class="flash-close" onclick="this.parentElement.remove()">×</button>
    `;
    flashContainer.appendChild(flash);
    setTimeout(() => {
      flash.style.animation = 'slideOut 0.4s ease forwards';
      setTimeout(() => flash.remove(), 400);
    }, 5000);
  }

  function createFlashContainer() {
    const fc = document.createElement('div');
    fc.className = 'flash-container';
    fc.id = 'flashContainer';
    document.body.appendChild(fc);
    return fc;
  }
})();
