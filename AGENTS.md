# Agent Instructions

이 저장소에서 활동하는 모든 AI 코딩 에이전트(Claude Code, Codex CLI 등)를 위한 공통 지침이다.

## Commit & PR 워크플로우

사용자가 "커밋해줘", "commit", "PR 만들어줘", "push and PR" 같은 커밋·PR 관련 요청을 하면 반드시 아래 문서의 절차를 따를 것.

**참조 문서**: [`docs/workflows/commit-and-pr.md`](docs/workflows/commit-and-pr.md)

핵심 규칙:
- `main` 브랜치에 직접 커밋/푸시 금지 — 반드시 새 브랜치 생성 후 진행
- 커밋 전 `origin/main` 최신화 확인
- 커밋 스코프가 애매하면 사용자에게 반드시 확인
- 파괴적 명령(`--force`, `reset --hard` 등) 사용 금지
- `gh` CLI 사용 불가 시 GitHub compare URL로 안내 (일부 개발자는 로컬에서 PR 생성이 불가능하므로 필수)
- 커밋 메시지의 `Co-Authored-By` 태그는 현재 사용 중인 도구에 맞춰 붙일 것
  - Claude Code: `Co-Authored-By: Claude <noreply@anthropic.com>`
  - Codex CLI: `Co-Authored-By: Codex <noreply@openai.com>`

## Claude Code 사용자용 슬래시 커맨드

Claude Code에서는 `/commit` 슬래시 커맨드로 이 워크플로우를 바로 호출할 수 있다. (`.claude/commands/commit.md`)

## Codex CLI 사용자용

Codex CLI는 이 `AGENTS.md`를 자동으로 로드하므로, "커밋해줘" 같은 자연어 요청 시 위 워크플로우 문서를 참조해 실행하면 된다.
