# AI 기사 스크랩 봇

텔레그램 채널 + 언론사 홈페이지에서 기사를 주기적으로 수집하고,
AI(Groq)가 크립토 관련성/중요도를 판단해서 자동으로 걸러주는 도구.
속보급 기사는 텔레그램으로 폰에 바로 알림이 옵니다.

**완전 무료 · 카드 등록 불필요 · PC 안 켜도 됨** 구성이 기본입니다
(GitHub Actions + GitHub Pages + 텔레그램 봇 조합, 별도 서버·DB·클라우드 계정 불필요).

## 구조

```
ai-article-scraper/
├── config.yaml               # 채널/언론사/필터/속보 기준 설정
├── requirements.txt
├── .github/workflows/
│   └── scrape.yml            # 30분마다 자동 실행되는 GitHub Actions cron
├── scripts/
│   ├── run_pipeline.py       # 1회성 수집+필터링+알림 스크립트 (서버 아님)
│   └── notify_telegram.py    # 속보 텔레그램 전송
├── docs/                     # GitHub Pages가 그대로 서빙하는 폴더
│   ├── index.html            # 대시보드 (정적, articles.json을 읽음)
│   └── articles.json         # Actions가 매번 갱신하는 결과 데이터
└── app/                      # 수집 로직 본체 (scripts/에서 재사용)
    ├── telegram_scraper.py
    ├── press_scraper.py
    ├── ai_filter.py
    └── models.py
```

로컬 서버로 직접 운용하고 싶은 경우를 위해 `app/main.py`(FastAPI), `Dockerfile`,
`deploy_oracle.sh`도 남겨뒀습니다 (맨 아래 "옵션 B" 참고).

---

## 완전 무료 구성 설정법

### 1. GitHub repo 만들기

이 폴더를 GitHub repo로 push 하세요 (private repo도 무료로 가능).

```bash
cd ai-article-scraper
git init
git add .
git commit -m "init"
git remote add origin <본인의 repo 주소>
git push -u origin main
```

### 2. 텔레그램 봇 만들기 (속보 알림용)

1. 텔레그램에서 **@BotFather** 검색 → `/newbot` → 이름 정하면 토큰 발급
2. 만든 봇과 대화 시작 (아무 메시지나 1번 전송, 예: `/start`)
3. 브라우저로 아래 접속해서 `chat.id` 값 확인
   ```
   https://api.telegram.org/bot<발급받은토큰>/getUpdates
   ```
   응답에서 `"chat":{"id": 123456789, ...}` 의 숫자가 chat id

### 3. GitHub Secrets 등록

repo → **Settings → Secrets and variables → Actions → New repository secret**

| 이름 | 값 |
|---|---|
| `GROQ_API_KEY` | console.groq.com에서 발급 |
| `TELEGRAM_BOT_TOKEN` | 2번 단계에서 발급받은 토큰 |
| `TELEGRAM_CHAT_ID` | 2번 단계에서 확인한 chat id |

### 4. GitHub Pages 활성화

repo → **Settings → Pages** → Source: `Deploy from a branch` → Branch: `main`, 폴더 `/docs` 선택 → Save

몇 분 뒤 `https://사용자명.github.io/저장소명/` 으로 대시보드가 뜹니다.

### 5. 대시보드에 Actions 링크 연결 (선택)

`docs/index.html`에서 아래 줄을 본인 repo 주소로 바꿔주세요 (수동 실행 버튼용).
```js
const ACTIONS_URL = 'https://github.com/YOUR_USERNAME/YOUR_REPO/actions';
```

### 6. 동작 확인

repo → **Actions 탭** → `기사 수집 및 속보 알림` 워크플로우 선택 →
**Run workflow** 버튼으로 1회 수동 실행 → 로그에서 수집 결과 확인 →
`docs/articles.json`이 커밋되면 대시보드에 반영됨.

이후로는 `.github/workflows/scrape.yml`의 cron(`*/30 * * * *`, 30분마다)에 따라
GitHub 서버가 알아서 수집 → AI 필터링 → (속보면) 텔레그램 알림 → 결과 저장까지 전부 자동으로 돕니다.
**PC를 꺼둬도 계속 동작합니다.**

> ⚠️ GitHub Pages 무료 플랜은 URL을 아는 사람은 누구나 볼 수 있는 공개 상태입니다
> (완전 비공개는 GitHub Pro/Team 이상 필요). URL을 따로 공유하지 않으면 사실상 문제없지만,
> 민감한 용도면 참고하세요.

## 설정 (`config.yaml`)

```yaml
telegram_channels:
  - name: "coindeskkorea"      # t.me/s/coindeskkorea 의 채널명 부분만

press_sites:
  - name: "tokenpost"
    rss: "https://www.tokenpost.kr/rss"
  # RSS 없는 언론사는 press_scraper.py의 selector 방식으로 추가 가능

filter:
  min_relevance_score: 7    # 이 점수 이상만 대시보드에 저장 (0~10)
  breaking_score: 9         # 이 점수 이상 + 신규 기사면 텔레그램 즉시 알림
  keywords_boost:
    - "비트코인"
    - "이더리움"
    - "SEC"
    - "ETF"
```

## 확장 아이디어

- 카드뉴스/스크립트 파이프라인이 필요해지면 `docs/articles.json`을 그대로 읽어서 연결
- 텔레그램 비공개 채널이 필요하면 `app/telegram_scraper.py`를 Telethon 기반으로 교체
- 알림을 Slack/Discord로도 받고 싶으면 `scripts/notify_telegram.py`처럼 webhook 함수 추가

---

## 옵션 B: 로컬/서버로 직접 운용하고 싶은 경우

실시간성이 더 중요하거나(즉시 "지금 수집" 트리거, 실시간 상태 확인) 직접 서버를 관리하고 싶다면
FastAPI 상시 서버 버전도 남겨뒀습니다.

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # GROQ_API_KEY 입력
uvicorn app.main:app --reload --port 8811
```

`http://localhost:8811` 접속 시 대시보드, `/docs`에서 API 확인 가능.

클라우드에 올려서 PC 없이 돌리고 싶다면:

- **Oracle Cloud Always Free** (카드 등록 필요하지만 과금 없음): `Dockerfile` + `deploy_oracle.sh` 사용
  ```bash
  git clone <레포주소> && cd ai-article-scraper
  chmod +x deploy_oracle.sh
  ./deploy_oracle.sh 발급받은_GROQ_API_KEY
  ```
- Railway/Fly.io/일반 VPS: 월 $2~6, `Dockerfile` 그대로 사용 가능

### API (옵션 B 서버 버전)

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/articles` | 저장된 기사 목록 (쿼리: `min_score`, `source_type`, `limit`) |
| GET | `/status` | 다음 수집 시각, 마지막 수집 결과 |
| POST | `/scrape/now` | 즉시 1회 수집 실행 |
| GET | `/health` | 상태 확인 |
