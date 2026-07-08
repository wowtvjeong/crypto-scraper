#!/bin/bash
# Oracle Cloud Always Free VM (Ubuntu) 배포 스크립트
# 사용법:
#   1) VM에 SSH 접속
#   2) git clone <레포주소> && cd ai-article-scraper
#   3) chmod +x deploy_oracle.sh && ./deploy_oracle.sh 발급받은_GROQ_API_KEY
set -e

GROQ_KEY="$1"
if [ -z "$GROQ_KEY" ]; then
  echo "사용법: ./deploy_oracle.sh <GROQ_API_KEY>"
  exit 1
fi

echo "[1/4] Docker 설치..."
if ! command -v docker &> /dev/null; then
  sudo apt-get update -y
  sudo apt-get install -y docker.io
  sudo systemctl enable docker
  sudo systemctl start docker
fi

echo "[2/4] 방화벽(80 포트) 열기..."
sudo iptables -I INPUT -p tcp --dport 80 -j ACCEPT || true
sudo netfilter-persistent save 2>/dev/null || true
echo "※ Oracle Cloud 콘솔 → VCN → Security List에서도 0.0.0.0/0, 포트 80 Ingress Rule을 반드시 추가하세요."

echo "[3/4] 이미지 빌드..."
sudo docker build -t scraper .

echo "[4/4] 기존 컨테이너 정리 후 재실행..."
sudo docker rm -f scraper-app 2>/dev/null || true
mkdir -p ./data
sudo docker run -d \
  --name scraper-app \
  --restart always \
  -p 80:8811 \
  -v "$(pwd)/data:/app/data" \
  -e GROQ_API_KEY="$GROQ_KEY" \
  scraper

echo ""
echo "완료. 아래 명령으로 이 서버의 공인 IP를 확인하세요:"
echo "  curl ifconfig.me"
echo "그 IP를 브라우저에 입력하면 대시보드가 뜹니다. (예: http://123.45.67.89)"
