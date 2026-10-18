import path from 'node:path';
import { fileURLToPath } from 'node:url';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

const directory = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  root: directory,
  base: './',
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(directory, '..'),
    },
  },
  build: {
    outDir: path.resolve(directory, '..', 'desktop-dist'),
    emptyOutDir: true,
    sourcemap: false,
  },
});
