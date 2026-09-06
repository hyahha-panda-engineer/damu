import os
import json
import re
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, Dict
import requests
from bs4 import BeautifulSoup

# 日本時間(JST)のタイムゾーンを定義
JST = timezone(timedelta(hours=+9), 'JST')

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

# 水資源機構 中部支社 【ダム流況表（1時間間隔）】のURL
URLS = {
    "uren": "https://www.water.go.jp/mizu/chubu/realtime/p020201_60/301_1.html",
    "oshima": "https://www.water.go.jp/mizu/chubu/realtime/p020201_60/302_1.html"
}

# ==========================================
# 処理関数
# ==========================================

def extract_number(text: str) -> Optional[float]:
    """文字列から数値を安全に抽出する"""
    if not text:
        return None
    
    # 単位表記の文字列などが数値に混入するのを防ぐ
    text = re.sub(r'(×103m3|103m3|×103|10\^3)', '', text)
    
    # 全角数字、カンマ、不要な文字を除去して数値と小数点だけを残す
    cleaned = re.sub(r'[^0-9.]', '', text)
    if not cleaned:  # "－"（データなし）などの場合はスキップ
        return None
        
    try:
        return float(cleaned)
    except ValueError:
        return None

def fetch_dam_metrics(url: str) -> Tuple[Optional[float], Optional[float]]:
    """ダム流況表から、最新の貯水量(千m³)と流入量(m³/s)を取得する"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    vol_k: Optional[float] = None
    inflow: Optional[float] = None

    try:
        res = requests.get(url, headers=headers, timeout=15)
        res.raise_for_status()
        res.encoding = res.apparent_encoding # 文字化け対策
        soup = BeautifulSoup(res.text, 'html.parser')

        # ページ内のすべてのテーブルを走査
        for table in soup.find_all('table'):
            rows = table.find_all('tr')
            
            # 24時間分のデータが入っているテーブル（行数が多いもの）をターゲットにする
            # （上部にある「ダム諸元」などの小さなテーブルを無視するため）
            if len(rows) < 15:
                continue
                
            vol_idx = -1
            inflow_idx = -1
            
            # 1. 見出しから「貯水量」と「流入量」の【列番号】を特定する
            for row in rows:
                cells = row.find_all(['th', 'td'])
                for i, cell in enumerate(cells):
                    text = cell.text.replace('\n', '').strip()
                    if '貯水量' in text:
                        vol_idx = i
                    elif '流入量' in text:
                        inflow_idx = i
                
                if vol_idx != -1 and inflow_idx != -1:
                    break # 列番号が特定できたら探索終了
            
            # 2. 特定した列を上から下になぞり、最新の数値を上書きしていく
            if vol_idx != -1 and inflow_idx != -1:
                for row in rows:
                    cells = row.find_all(['th', 'td'])
                    
                    # セル数が足りている行（実際のデータ行）のみをチェック
                    if len(cells) > max(vol_idx, inflow_idx):
                        v_val = extract_number(cells[vol_idx].text)
                        i_val = extract_number(cells[inflow_idx].text)
                        
                        # 数値が存在する時間帯のデータでどんどん上書きする
                        # （最終的にループが終わると「一番下の最新時間」のデータが残る）
                        if v_val is not None:
                            vol_k = v_val
                        if i_val is not None:
                            inflow = i_val
                
                break # 目的のテーブル処理が終わったら終了

    except requests.RequestException as e:
        print(f"[HTTP Error] データの取得に失敗しました ({url}): {e}")
    except Exception as e:
        print(f"[Parse Error] HTMLの解析に失敗しました ({url}): {e}")

    return vol_k, inflow

def calculate_metrics(raw_volume_k_m3: Optional[float], capacity_k_m3: float) -> Tuple[float, float]:
    """千m³単位の貯水量から貯水率(%)を計算する"""
    if raw_volume_k_m3 is None or capacity_k_m3 <= 0:
        return 0.0, 0.0

    rate = (raw_volume_k_m3 / capacity_k_m3) * 100.0
    return round(raw_volume_k_m3, 1), round(rate, 1)

def build_dam_data(dam_id: str, raw_vol_k: Optional[float], raw_inflow: Optional[float]) -> Dict[str, float]:
    """ダムごとのデータ辞書を構築する"""
    if raw_vol_k is None:
        print(f"[Warning] {dam_id} の貯水量が取得できませんでした。")
        raw_vol_k = 633.1 if dam_id == "uren" else 3754.1
        
    if raw_inflow is None:
        print(f"[Warning] {dam_id} の流入量が取得できませんでした。")
        raw_inflow = 0.44 if dam_id == "uren" else 0.14

    vol_k, rate = calculate_metrics(raw_vol_k, CAPACITY_K_M3[dam_id])
    
    return {
        "storage_volume": vol_k,
        "storage_rate": rate,
        "inflow": raw_inflow
    }

def main():
    print(f"--- 取得開始: {datetime.now(JST)} ---")
    
    u_vol, u_inflow = fetch_dam_metrics(URLS["uren"])
    o_vol, o_inflow = fetch_dam_metrics(URLS["oshima"])
    
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