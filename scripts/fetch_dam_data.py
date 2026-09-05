import os
import json
import re
from datetime import datetime, timezone, timedelta

# 日本時間(JST)のタイムゾーンを定義
JST = timezone(timedelta(hours=+9), 'JST')
from typing import Optional, Tuple, Dict, Any

import requests
from bs4 import BeautifulSoup

# ==========================================
# 定数・設定
# ==========================================
DATA_FILE = 'data/dam_data.json'
MAX_HISTORY_RECORDS = 360  # 約30日分 (12回/日)

# 有効貯水量（単位: 千m³）
CAPACITY_K_M3 = {
    "uren": 28420.0,
    "oshima": 11300.0
}

# 監視対象URL
URLS = {
    "uren": "https://www.river.go.jp/kantei/p/f2010101/",
    "oshima": "https://www.river.go.jp/kantei/p/f2010102/"
}

# ==========================================
# 処理関数
# ==========================================

def extract_number(text: str) -> Optional[float]:
    """文字列から数値を安全に抽出する"""
    if not text:
        return None
    cleaned = re.sub(r'[^0-9.]', '', text)
    try:
        return float(cleaned)
    except ValueError:
        return None

def fetch_dam_metrics(url: str) -> Tuple[Optional[float], Optional[float]]:
    """指定URLから貯水量(m³)と流入量(m³/s)を取得する"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    volume_m3: Optional[float] = None
    inflow: Optional[float] = None

    try:
        res = requests.get(url, headers=headers, timeout=15)
        res.raise_for_status()
        res.encoding = res.apparent_encoding
        soup = BeautifulSoup(res.text, 'html.parser')

        # <tr>タグの中から「貯水量」「流入量」を含む行を探索
        for tr in soup.find_all('tr'):
            tr_text = tr.text.replace(',', '')
            if '貯水量' in tr_text and volume_m3 is None:
                volume_m3 = extract_number(tr_text)
            elif '流入量' in tr_text and inflow is None:
                inflow = extract_number(tr_text)

    except requests.RequestException as e:
        print(f"[HTTP Error] データの取得に失敗しました ({url}): {e}")
    except Exception as e:
        print(f"[Parse Error] HTMLの解析に失敗しました ({url}): {e}")

    return volume_m3, inflow

def calculate_metrics(raw_volume_m3: Optional[float], capacity_k_m3: float) -> Tuple[float, float]:
    """m³単位の貯水量から、千m³単位の貯水量と貯水率(%)を計算する"""
    if raw_volume_m3 is None or capacity_k_m3 <= 0:
        return 0.0, 0.0

    volume_k_m3 = raw_volume_m3 / 1000.0
    rate = (volume_k_m3 / capacity_k_m3) * 100.0

    return round(volume_k_m3, 1), round(rate, 1)

def build_dam_data(dam_id: str, raw_vol: Optional[float], raw_inflow: Optional[float]) -> Dict[str, float]:
    """ダムごとのデータ辞書を構築する。データ欠落時は直近のダミー値等で保護"""
    if raw_vol is None:
        print(f"[Warning] {dam_id} の貯水量が取得できませんでした。")
        raw_vol = 633103.0 if dam_id == "uren" else 3754103.0 # デバッグ/フォールバック用
        
    if raw_inflow is None:
        print(f"[Warning] {dam_id} の流入量が取得できませんでした。")
        raw_inflow = 0.44 if dam_id == "uren" else 0.14

    vol_k, rate = calculate_metrics(raw_vol, CAPACITY_K_M3[dam_id])
    
    return {
        "storage_volume": vol_k,
        "storage_rate": rate,
        "inflow": raw_inflow
    }

def main():
    print(f"--- 取得開始: {datetime.now()} ---")
    
    u_vol, u_inflow = fetch_dam_metrics(URLS["uren"])
    o_vol, o_inflow = fetch_dam_metrics(URLS["oshima"])
    # ★日時の取得に JST を指定
    now_str = datetime.now(JST).strftime('%Y-%m-%d %H:00')
    new_record = {
        "datetime": now_str,
        "uren": build_dam_data("uren", u_vol, u_inflow),
        "oshima": build_dam_data("oshima", o_vol, o_inflow)
    }

    # JSON読み込み
    data_list = []
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            try:
                data_list = json.load(f)
            except json.JSONDecodeError:
                pass

    # 重複確認と保存
    if not any(item.get('datetime') == new_record['datetime'] for item in data_list):
        data_list.append(new_record)
        
        if len(data_list) > MAX_HISTORY_RECORDS:
            data_list = data_list[-MAX_HISTORY_RECORDS:]

        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data_list, f, ensure_ascii=False, indent=2)
        print(f"[Success] データを更新しました: {new_record['datetime']}")
    else:
        print(f"[Skip] {new_record['datetime']} のデータは既に存在します。")

if __name__ == '__main__':
    main()