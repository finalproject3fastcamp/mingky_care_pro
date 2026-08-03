# Commit & PR 워크플로우

이 문서는 Claude Code, Codex CLI 등 어떤 AI 도구를 쓰더라도 동일하게 따라야 하는 커밋·PR 생성 절차다. 사용자가 "commit 해줘", "PR 만들어줘" 같은 요청을 하면 이 절차대로 실행한다.

## 실행 순서

### 1. 사전 점검
- `git status`, `git branch --show-current`로 현재 상태 파악
- 변경된 파일이 없으면 사용자에게 알리고 종료
- 스테이징 대상이 애매하면 (untracked/modified 혼재) 반드시 사용자에게 커밋 스코프 확인

### 2. main 브랜치 최신화 확인
- `git fetch origin main`
- 현재 브랜치가 main이면 `git pull --ff-only origin main`으로 최신화
- 현재 브랜치가 main이 아니면 `git rev-list --count HEAD..origin/main`으로 뒤처짐 여부 확인
  - 뒤처진 커밋이 있으면 사용자에게 알리고 rebase/merge 진행 여부 확인 (자동 rebase 금지, 반드시 승인 필요)

### 3. 작업 브랜치 결정
- **현재 브랜치가 main인 경우**: main에는 직접 커밋 금지
  - 변경사항 성격을 파악해 브랜치명 제안: `feat/xxx`, `fix/xxx`, `docs/xxx`, `chore/xxx` 등
  - 사용자에게 브랜치명 확인받고 `git checkout -b <branch>`
- **다른 브랜치인 경우**: 그 브랜치에서 계속 진행

### 4. 커밋
- 커밋 대상 파일을 사용자에게 명확히 보여주고 승인받기
- 최근 커밋 스타일 확인: `git log --oneline -5`
- 커밋 메시지는 HEREDOC로 작성
- 커밋 메시지 마지막 줄에 도구별 Co-Authored-By 태그 포함:
  - **Claude Code**: `Co-Authored-By: Claude <noreply@anthropic.com>` (필요 시 모델명 명시)
  - **Codex CLI**: `Co-Authored-By: Codex <noreply@openai.com>`
  - 기타 도구는 해당 도구의 관례를 따를 것

### 5. 원격 푸시
- `git push -u origin <branch>` (첫 푸시라면 upstream 설정)
- 이미 있는 브랜치면 그냥 `git push`

### 6. PR 생성 (두 가지 경로)
`gh auth status` 로 gh CLI 인증 상태를 먼저 확인한다.

**경로 A — gh CLI 사용 가능:**
- PR 제목(70자 이내)과 본문은 **한글로** 작성 (사용자가 다른 언어를 명시한 경우에만 예외)
- 본문 형식:
  ```
  ## Summary
  <1-3 bullet>
  ```
- Test plan 섹션은 사용하지 않는다 (사용자가 명시적으로 요청한 경우에만 추가)
- `gh pr create --title "..." --body "$(cat <<'EOF' ... EOF)"` 로 생성
- 생성된 PR URL 출력

**경로 B — gh CLI 미설치·미인증·실패:**
- `git remote get-url origin`으로 owner/repo 파싱
- compare URL을 출력해서 사용자가 브라우저에서 직접 PR 생성:
  ```
  https://github.com/<owner>/<repo>/compare/main...<branch>?expand=1
  ```
- 참고용으로 PR 제목·본문 초안도 함께 출력해 복붙 가능하게 할 것

## 절대 금지
- `main` 브랜치에 직접 커밋/푸시
- `git push --force`, `git reset --hard`, `git rebase -i` 등 파괴적 명령 (사용자가 명시적으로 요청하지 않는 한)
- `--no-verify`로 훅 우회
- 커밋 스코프가 애매한 상태에서 임의로 `git add -A` 실행 — 반드시 사용자 확인

## 팁
- 사용자가 이미 브랜치를 만들어놓고 이 명령을 실행한 경우가 흔하니, 현재 브랜치를 존중할 것
- 변경 파일 수가 많으면 요약해서 보여주고 상세는 필요 시 열어볼 것
