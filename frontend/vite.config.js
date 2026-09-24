import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'
import process from 'node:process'
import { defineConfig } from 'vite'

// edge-api address as seen from the dev/preview server (the tablet only talks to this server).
const EDGE = process.env.EDGE_API_URL || 'http://localhost:8000'

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['pwa-192.svg', 'pwa-512.svg'],
      manifest: {
        name: 'Operator Companion',
        short_name: 'Operator',
        description: 'Field operator safety and task companion',
        theme_color: '#111827',
        background_color: '#f5f5f5',
        display: 'standalone',
        orientation: 'portrait',
        start_url: '/',
        scope: '/',
        icons: [
          { src: 'pwa-192.svg', type: 'image/svg+xml', sizes: '192x192', purpose: 'any' },
          { src: 'pwa-512.svg', type: 'image/svg+xml', sizes: '512x512', purpose: 'any' },
        ],
      },
    }),
  ],
  server: {
    host: '0.0.0.0',
    port: 5173,
    fs: { allow: ['..'] }, // contracts/i18n/en.json lives outside frontend/
    proxy: {
      '/ws': {
        target: EDGE.replace(/^http/, 'ws'),
        ws: true,
        changeOrigin: true,
      },
      '/api': {
        target: EDGE,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
