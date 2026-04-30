# Jira → Discord 알림 봇

Jira의 모든 이벤트를 Discord 채널에 예쁜 Embed로 전송하는 Webhook 서버입니다.

## 지원 이벤트

| 카테고리 | 이벤트 |
|----------|--------|
| **이슈** | 생성 / 수정 / 삭제 / 상태 변경 |
| **댓글** | 작성 / 수정 / 삭제 |
| **스프린트** | 생성 / 시작 / 종료 / 수정 / 삭제 |
| **버전·릴리즈** | 생성 / 수정 / 삭제 / 릴리즈 / 릴리즈 취소 / 이동 / 아카이브 |
| **워크로그** | 추가 / 수정 / 삭제 |
| **프로젝트** | 생성 / 수정 / 삭제 / 복원 |
| **사용자** | 생성 / 수정 / 삭제 |
| **보드** | 생성 / 수정 / 삭제 / 설정 변경 |

---

## 배포 방법

### 1. Discord Webhook URL 생성

Discord 채널 → 설정 → 연동 → **웹후크 만들기** → URL 복사

### 2. Railway 배포 (추천)
[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app)

```bash
npm install -g @railway/cli
railway login
railway init
railway up
```

Railway 대시보드에서 환경변수 설정:

```
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
JIRA_WEBHOOK_SECRET=your_secret   # 선택사항 (보안 강화)
```

### 3. Render 배포 (대안)

- Build Command: `pip install -r requirements.txt`
- Start Command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- 환경변수 동일하게 설정

---

## Jira Webhook 설정

1. Jira → **Settings → System → Webhooks → Create a WebHook**
2. URL: `https://your-app.railway.app/webhook`
3. **Events** → 원하는 이벤트 모두 체크 (전체 선택 가능)
4. Secret: 환경변수 `JIRA_WEBHOOK_SECRET`에 설정한 값과 동일하게 (선택)
5. **Save**

---

## 로컬 테스트

```bash
# 의존성 설치
pip install -r requirements.txt

# 환경변수 설정
cp .env.example .env
# .env 파일에 DISCORD_WEBHOOK_URL 입력

# 서버 실행
uvicorn main:app --reload

# ngrok으로 외부 노출 (Jira Webhook 테스트용)
ngrok http 8000
# ngrok URL을 Jira Webhook URL에 입력
```

### curl로 직접 테스트

```bash
curl -X POST http://localhost:8000/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "webhookEvent": "jira:issue_created",
    "user": {"displayName": "홍길동"},
    "issue": {
      "key": "TEST-1",
      "self": "https://yoursite.atlassian.net/rest/api/2/issue/TEST-1",
      "fields": {
        "summary": "테스트 이슈입니다",
        "status": {"name": "To Do"},
        "priority": {"name": "Medium"},
        "issuetype": {"name": "Bug"},
        "assignee": {"displayName": "담당자"},
        "reporter": {"displayName": "보고자"},
        "project": {"name": "테스트 프로젝트"},
        "description": "이슈 설명입니다.",
        "labels": ["backend", "urgent"]
      }
    }
  }'
```

---

## 파일 구조

```
jira-discord-bot/
├── main.py           # FastAPI 서버, 서명 검증, Discord 전송
├── formatters.py     # 이벤트별 Discord Embed 포맷터
├── requirements.txt
├── Procfile          # Railway/Heroku용
├── railway.toml      # Railway 설정
└── .env.example
```

---

## 환경변수

| 변수명 | 필수 | 설명 |
|--------|------|------|
| `DISCORD_WEBHOOK_URL` | ✅ | Discord 채널 Webhook URL |
| `JIRA_WEBHOOK_SECRET` | ❌ | Jira Webhook Secret (서명 검증용) |
