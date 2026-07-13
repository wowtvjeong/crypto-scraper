"""
mempool.space 공개 API로 비트코인 온체인 네트워크 지표를 가져온다.
완전 무료, 로그인·API키 불필요, 실시간(초~분 단위 갱신).
https://mempool.space/docs/api/rest

이전에 만든 "Bitcoin on-chain data 시각화" 방송 화면 위젯과 같은 데이터 소스를 쓴다.
그 위젯은 화면에 보여주는 용도였고, 여기서는 그 숫자를 대본 문장으로 녹여 넣는 용도.
"""
import httpx
from datetime import datetime, timezone
from collections import defaultdict
from typing import Optional

BASE_URL = "https://mempool.space/api"
COINGECKO_URL = "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                  " (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
}


async def fetch_onchain_snapshot() -> Optional[dict]:
    """실패해도 None만 반환하고 예외를 던지지 않는다 (전체 대본 생성이 이것 때문에
    멈추면 안 되므로 — 이 지표는 '있으면 좋은 것'이지 필수가 아님)."""
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=10.0) as client:
            mempool_resp = await client.get(f"{BASE_URL}/mempool")
            fees_resp = await client.get(f"{BASE_URL}/v1/fees/recommended")
            height_resp = await client.get(f"{BASE_URL}/blocks/tip/height")
            hashrate_resp = await client.get(f"{BASE_URL}/v1/mining/hashrate/3d")

            mempool_resp.raise_for_status()
            fees_resp.raise_for_status()
            height_resp.raise_for_status()
            hashrate_resp.raise_for_status()

        mempool_data = mempool_resp.json()
        fees_data = fees_resp.json()
        height = int(height_resp.text.strip())
        hashrate_data = hashrate_resp.json()

        hashrate_ehs = hashrate_data.get("currentHashrate", 0) / 1e18

        return {
            "mempool_count": mempool_data.get("count"),
            "fastest_fee_satvb": fees_data.get("fastestFee"),
            "economy_fee_satvb": fees_data.get("economyFee"),
            "latest_block_height": height,
            "hashrate_ehs": round(hashrate_ehs, 1),
        }
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        print(f"[onchain] mempool.space 조회 실패: {e}")
        return None


def format_snapshot_for_prompt(snapshot: Optional[dict]) -> str:
    if not snapshot:
        return "(실시간 온체인 네트워크 지표를 가져오지 못했습니다 — 이 부분은 생략하고 뉴스 위주로만 작성)"
    return (
        f"- 현재 비트코인 네트워크 해시레이트: 약 {snapshot['hashrate_ehs']} EH/s\n"
        f"- 멤풀(미확인 거래) 대기 건수: 약 {snapshot['mempool_count']:,}건\n"
        f"- 권장 거래 수수료: 빠른 처리 기준 {snapshot['fastest_fee_satvb']} sat/vB, "
        f"저렴한 처리 기준 {snapshot['economy_fee_satvb']} sat/vB\n"
        f"- 최신 블록 높이: {snapshot['latest_block_height']:,}"
    )


async def fetch_200w_ma() -> Optional[dict]:
    """CoinGecko 무료 API로 비트코인 일별 가격을 받아 직접 200주 이동평균선을 계산한다.
    (bitcoinmagazinepro.com 같은 차트 사이트를 스크래핑하지 않고, 원본 가격 데이터로
    우리가 직접 계산하는 방식 — 로그인/유료 여부 확인이 필요 없고 사이트 구조 변경에도
    영향을 안 받아서 더 안정적이다.)

    색상 구간(저평가/고평가 존)은 재현하지 않는다 — 정확한 색상 경계 기준을 확신할 수
    없어서, 대신 '현재가가 200주 평균 대비 몇 % 위/아래' 라는 원본 수치만 제공한다.
    """
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=15.0) as client:
            resp = await client.get(
                COINGECKO_URL,
                params={"vs_currency": "usd", "days": 1460},  # 200주 + 여유분(약 4년)
            )
            resp.raise_for_status()
            data = resp.json()

        prices = data.get("prices", [])  # [[timestamp_ms, price], ...]
        if len(prices) < 200:
            print("[onchain] CoinGecko 가격 데이터가 200주치보다 부족함")
            return None

        # 일별 데이터를 ISO 주 단위로 묶어서 주별 평균가 계산
        weekly_prices = defaultdict(list)
        for ts_ms, price in prices:
            dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            year, week, _ = dt.isocalendar()
            weekly_prices[(year, week)].append(price)

        weekly_avgs = [
            sum(v) / len(v) for k, v in sorted(weekly_prices.items())
        ]

        if len(weekly_avgs) < 200:
            print("[onchain] 주 단위로 묶었더니 200주치보다 부족함")
            return None

        last_200_weeks = weekly_avgs[-200:]
        ma_200w = sum(last_200_weeks) / len(last_200_weeks)
        current_price = prices[-1][1]
        pct_diff = ((current_price - ma_200w) / ma_200w) * 100

        return {
            "ma_200w": round(ma_200w, 0),
            "current_price": round(current_price, 0),
            "pct_diff": round(pct_diff, 1),
        }
    except (httpx.HTTPError, ValueError, KeyError, TypeError, ZeroDivisionError) as e:
        print(f"[onchain] 200주 이평선 계산 실패: {e}")
        return None


def format_200w_ma_for_prompt(ma_data: Optional[dict]) -> str:
    if not ma_data:
        return ""
    direction = "위" if ma_data["pct_diff"] >= 0 else "아래"
    return (
        f"- 비트코인 200주 이동평균선: 약 {ma_data['ma_200w']:,.0f}달러 "
        f"(현재가 {ma_data['current_price']:,.0f}달러는 이 평균선 대비 "
        f"{abs(ma_data['pct_diff'])}% {direction})"
    )
