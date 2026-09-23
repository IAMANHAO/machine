import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 开发期前端走 5173，API 代理到本地 FastAPI；打包后由 FastAPI 直接伺服 dist/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': { target: 'http://127.0.0.1:8756', changeOrigin: true } },
  },
  build: { outDir: 'dist', emptyOutDir: true },
})
