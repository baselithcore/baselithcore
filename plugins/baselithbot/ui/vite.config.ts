import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ command }) => ({
  plugins: [react()],
  base: command === 'build' ? '/baselithbot/ui/' : '/',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: false,
    chunkSizeWarningLimit: 900,
  },
  server: {
    port: 5180,
    open: '/',
    proxy: {
      '/baselithbot/dash': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/baselithbot/run': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/baselithbot/status': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/baselithbot/metrics': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/baselithbot/inbound': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/baselithbot/ws': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        ws: true,
      },
    },
  },
}));
