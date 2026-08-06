import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // 백엔드에 CORS 설정이 없어 브라우저가 :5173 → :8000 요청을 막는다.
    // dev 에서는 여기서 우회한다. 배포 때는 리버스 프록시나 CORSMiddleware 가 따로 필요하다.
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        // 백엔드 라우터는 /events, /sessions 처럼 접두사가 없다.
        // 맨 앞의 /api 만 지운다. 앵커가 없으면 쿼리스트링에 낀 api 까지 날아간다.
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
