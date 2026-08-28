import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import { VitePWA } from 'vite-plugin-pwa'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      manifest: {
        name: 'NER-SHIELD',
        short_name: 'NER-SHIELD',
        description: 'NER landslide decision-support dashboard',
        theme_color: '#0f172a',
        background_color: '#0f172a',
        display: 'standalone',
        icons: [{ src: '/favicon.svg', sizes: 'any', type: 'image/svg+xml' }],
      },
      workbox: {
        // Caches the app shell so the dashboard still loads offline; API calls still
        // need connectivity (or the mobile app's own offline queue) to reach fresh data.
        runtimeCaching: [
          {
            urlPattern: ({ url }) => url.pathname.startsWith('/api/'),
            handler: 'NetworkFirst',
            options: { cacheName: 'ner-shield-api-cache', networkTimeoutSeconds: 5 },
          },
        ],
      },
    }),
  ],
})
