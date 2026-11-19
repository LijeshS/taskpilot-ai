import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The React plugin is required: App.jsx uses JSX without importing React.
export default defineConfig({
  plugins: [react()],
  server: { host: '127.0.0.1', port: 5173, strictPort: true },
});
