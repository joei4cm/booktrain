import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 构建产物落到 Python 包里，后端直接静态托管（wheel 自带前端）
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../src/booktran/web/dist",
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8787" },
  },
});
