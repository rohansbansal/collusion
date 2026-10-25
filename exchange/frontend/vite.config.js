import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

export default defineConfig({
  plugins: [svelte()],
  server: {
    proxy: {
      '/market': 'http://127.0.0.1:8000',
      '/run_experiment': 'http://127.0.0.1:8000'
    }
  }
});