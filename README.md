# Telegram 다국어 번역 봇

## 개요
- 한국어/중국어/베트남어/크메르어 자동 감지·번역
- 사용자 등록·연장·소유자 관리
- Flask 대시보드

## 배포(Cloud Run)
1. GitHub → Secrets 등록
   - GCP_SA_JSON: 서비스계정 키(JSON) 원본
   - GCP_PROJECT_ID, TELEGRAM_TOKEN, RONGRID_API_KEY, OWNER_PASSWORD
2. `main` 브랜치 푸시 → Actions 자동 실행  
3. Cloud Run URL 확인
