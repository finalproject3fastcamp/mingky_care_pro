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
        // WebSocket 업그레이드도 넘긴다. 이게 없으면 vite 는 이 경로의
        // upgrade 요청을 그냥 흘려보내고 핸드셰이크가 타임아웃난다 — HTTP 만
        // 200 으로 통해서 "프록시는 되는데 지도만 안 뜬다" 로 보인다.
        //
        // 조작 소켓(/api/robots/{id}/teleop/operator)과 전체 위치 스트림
        // (/api/fleet/poses/stream)이 둘 다 여기에 걸린다. 배포에서는 nginx 의
        // location / 이 이미 Upgrade 헤더를 넘기고 있어 해당 사항이 없다.
        //
        // vite 자신의 HMR 소켓은 루트 경로라 /api 범위와 겹치지 않는다.
        ws: true,
        // 백엔드 라우터는 /events, /sessions 처럼 접두사가 없다.
        // 맨 앞의 /api 만 지운다. 앵커가 없으면 쿼리스트링에 낀 api 까지 날아간다.
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
