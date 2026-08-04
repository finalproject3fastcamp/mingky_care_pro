# Frontend

관제 대시보드(의료진 · 엔지니어) 프론트엔드.

## 스택

- React 19 + TypeScript
- Vite
- React Router
- axios

기술 스택 선택 근거는 [`../docs/monitoring-spec.md`](../docs/monitoring-spec.md) 2장 참고.

## 실행

```bash
cd frontend        # 프로젝트 루트에서
nvm use            # .nvmrc → Node 24
cp .env.example .env
npm install
npm run dev
```

기본 접속: <http://localhost:5173>

- `/medical` — 의료진 대시보드
- `/engineer` — 엔지니어 대시보드

## 디렉터리

```
src/
├── components/   공용 UI (Layout 등)
├── routes/       라우트별 페이지 컴포넌트
├── lib/          API 클라이언트 등 공용 로직
├── App.tsx       라우터 정의
└── main.tsx      진입점
```

## 환경 변수

| 키 | 설명 | 기본값 |
| --- | --- | --- |
| `VITE_API_BASE_URL` | 백엔드 FastAPI 주소 | `http://localhost:8000` |
