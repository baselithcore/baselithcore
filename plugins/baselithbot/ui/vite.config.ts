import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ command }) => ({
  plugins: [react()],
  base: command === 'build' ? '/baselithbot/ui/' : '/',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    // 'hidden': maps are emitted for error symbolication but no
    // `//# sourceMappingURL` comment points browsers at them.
    sourcemap: 'hidden',
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      output: {
        // Vendor code changes far less often than the dashboard itself; giving
        // it stable chunks means a UI-only release does not force every
        // browser to re-download React, the router and react-query. chart.js
        // stays in its own chunk and is only fetched by the pages that chart.
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined;
          if (/[\\/]node_modules[\\/](chart\.js|@kurkle|react-chartjs-2)[\\/]/.test(id)) {
            return 'vendor-charts';
          }
          if (/[\\/]node_modules[\\/]@tanstack[\\/]/.test(id)) return 'vendor-query';
          if (
            /[\\/]node_modules[\\/](react|react-dom|scheduler|react-router|react-router-dom|@remix-run)[\\/]/.test(
              id
            )
          ) {
            return 'vendor-react';
          }
          return undefined;
        },
      },
    },
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
