import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import {defineConfig} from 'vite';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, '.'),
    },
  },
  
  server: { host: '127.0.0.1', port: 1420, strictPort: true, proxy:{'/api':'http://127.0.0.1:8000'} },
});
