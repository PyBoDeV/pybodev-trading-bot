import time
import json
import hmac
import base64
import hashlib
import requests
from datetime import datetime, timezone

# ========= KONFIG =========

API_KEY = "babde86f-933e-4c7e-9281-f89cbea146ce"
API_SECRET = "E3F8A86A28F648C34BA0B4620FB804DB"
API_PASSPHRASE = "MamorTisch01."
BASE_URL = "https://eea.okx.com"

MAX_OPEN_BUYS = 6  # Sicherheitslimit

# ========= HILFSFUNKTIONEN =========

def get_timestamp():
    now = datetime.now(timezone.utc)
    ts = now.isoformat(timespec="milliseconds")
    if not ts.endswith("Z"):
        ts = ts.replace("+00:00", "Z")
    return ts
    
def status_callback(msg):
    global window
    if window:
        window.write_event_value("-STATUS_UPDATE-", msg)
    # TXT temporär AUSKOMMENTIEREN!
    # save_status_log()  # ← Das crasht!

def sign(message, secret_key):
    mac = hmac.new(secret_key.encode(), message.encode(), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode()

def get_headers(method, path, body=""):
    ts = get_timestamp()
    msg = ts + method + path + body
    sig = sign(msg, API_SECRET)
    return {
        "OK-ACCESS-KEY": API_KEY,
        "OK-ACCESS-SIGN": sig,
        "OK-ACCESS-TIMESTAMP": ts,
        "OK-ACCESS-PASSPHRASE": API_PASSPHRASE,
        "Content-Type": "application/json",
    }

def fetch_open_orders(inst_id):
    path = f"/api/v5/trade/orders-pending?instId={inst_id}"
    url = BASE_URL + path
    headers = get_headers("GET", path, "")
    resp = requests.get(url, headers=headers, timeout=10)
    return resp.json()

def fetch_order(inst_id, ord_id):
    """Einzelne Order laden, um gefüllte Menge zu kennen."""
    path = f"/api/v5/trade/order?instId={inst_id}&ordId={ord_id}"
    url = BASE_URL + path
    headers = get_headers("GET", path, "")
    resp = requests.get(url, headers=headers, timeout=10)
    return resp.json()

def get_last_price(inst_id):
    path = f"/api/v5/market/ticker?instId={inst_id}"
    url = BASE_URL + path
    headers = get_headers("GET", path, "")
    resp = requests.get(url, headers=headers, timeout=10)
    data = resp.json()
    last = float(data["data"][0]["last"])
    return last

# ========= GRID-BERECHNUNG =========

def build_grid(lower, upper, grid_n):
    step = (upper - lower) / (grid_n - 1)
    levels = [lower + i * step for i in range(grid_n)]
    return levels, step

def build_grid_pairs(levels, start_capital, sell_factor):
    mid = sum(levels) / len(levels)
    buy_levels = [p for p in levels if p <= mid]
    eur_per_buy = start_capital / len(buy_levels)

    pairs = []
    for p in buy_levels:
        size_wif = eur_per_buy / p
        sell_price = p * sell_factor
        pairs.append(
            {
                "buy_price": p,
                "sell_price": sell_price,
                "size_wif": size_wif,      # theoretische Zielmenge
                "status": "waiting_buy",
                "buy_ordId": None,
                "sell_ordId": None,
            }
        )
    return pairs, buy_levels, eur_per_buy
    
def calc_real_grid_profit(value_buy_eur, fee_buy_eur, value_sell_eur, fee_sell_eur):
    """Realer Grid-Profit: (Sell-Wert - Sell-Fee) - (Buy-Wert + Buy-Fee)"""
    return (value_sell_eur - fee_sell_eur) - (value_buy_eur)

# ========= INITIALISIERUNG FÜR GUI =========

def init_bot(pair, lower_price, upper_price, grid_count, start_capital_eur, sell_factor):
    levels, step = build_grid(lower_price, upper_price, grid_count)
    pairs, buy_levels, eur_per_buy = build_grid_pairs(
        levels, start_capital_eur, sell_factor
    )
    return {
        "pair": pair,
        "levels": levels,
        "pairs": pairs,
        "buy_levels": buy_levels,
        "eur_per_buy": eur_per_buy,
        "sell_factor": sell_factor,
    }

# ========= OFFENE BUYS STORNIEREN =========

def cancel_all_buy_orders(inst_id, status_callback):
    open_orders = fetch_open_orders(inst_id)
    open_list = open_orders.get("data", [])
    if not open_list:
        status_callback("Keine offenen Orders zum Stornieren gefunden.")
        return

    trade_path = "/api/v5/trade/cancel-order"
    trade_url = BASE_URL + trade_path

    for o in open_list:
        if o.get("side") != "buy":
            continue
        cancel_body = {"instId": inst_id, "ordId": o["ordId"]}
        body = json.dumps(cancel_body)
        headers = get_headers("POST", trade_path, body)
        resp = requests.post(trade_url, headers=headers, data=body, timeout=10)
        data = resp.json()
        status_callback(f"Cancel BUY {o['ordId']}: {data}")
        time.sleep(0.2)

# ========= SELL FÜR KONKRETEN BUY =========

def _place_sell_for_buy(buy, pairs, status_callback):
    grid_idx = buy.get("grid_idx")
    pd = pairs[grid_idx] if grid_idx is not None else None

    # 1) echte gefüllte Menge aus der BUY‑Order holen
    ord_info = fetch_order(buy["instId"], buy["ordId"])
    od = (ord_info.get("data") or [{}])[0]
    filled_sz = float(od.get("fillSz", od.get("accFillSz", "0") or "0"))
    if filled_sz <= 0:
        status_callback(f"SELL abgebrochen: keine gefüllte Menge für {buy['ordId']}")
        return False

  # etwas weniger verkaufen als gekauft, z.B. 99 %
    sell_size = filled_sz * 0.99
    sell_size = float(f"{sell_size:.4f}")  # auf 4 Nachkommastellen runden

    sell_order = {
        "instId": buy["instId"],
        "tdMode": "cash",
        "side": "sell",
        "ordType": "limit",
        "px": f"{buy['sell_price']:.4f}",   # 4 Nachkommastellen
        "sz": f"{filled_sz:.4f}",           # exakt gekaufte Menge, 4 Nachkommastellen
    }

    body = json.dumps(sell_order)
    path = "/api/v5/trade/order"
    url = BASE_URL + path
    headers = get_headers("POST", path, body)

    resp = requests.post(url, headers=headers, data=body, timeout=10)
    data = resp.json()
    status_callback(
        f"SELL ERROR Grid {grid_idx+1}: HTTP={resp.status_code} "
        f"code={data.get('code')} msg={data.get('msg')}"
        f"px={sell_order['px']} sz={sell_order['sz']}"
    )

    if pd is not None and resp.status_code == 200 and data.get("code") == "0":
        pd["status"] = "waiting_sell"
        pd["sell_ordId"] = data["data"][0]["ordId"]
        return True
    else:
        if pd is not None:
            pd["status"] = "sell_failed"
            pd["sell_ordId"] = None
        return False

# ===== RESET FAIL        
                        
def reset_sell_failed(pairs, pair, status_callback):
    """Reset sell_failed Grids zu waiting_buy"""
    reset_count = 0
    for pd in pairs:
        if pd.get("status") == "sell_failed":
            if pd.get("sell_ordId"):
                cancel_order(pair, pd["sell_ordId"], status_callback)
            pd["status"] = "waiting_buy"
            pd["sell_ordId"] = None
            reset_count += 1
    if reset_count > 0:
        return f"Reset {reset_count} sell_failed Grids"
    return "Keine sell_failed Grids gefunden"        
    
#===== Cancel für Reset

def cancel_order(inst_id, ord_id, status_callback):
    """Einzelne Order stornieren"""
    body = json.dumps({"instId": inst_id, "ordId": ord_id})
    headers = get_headers("POST", "/api/v5/trade/cancel-order", body)
    resp = requests.post(BASE_URL + "/api/v5/trade/cancel-order", headers=headers, data=body)
    data = resp.json()
    status_callback(f"Cancel {ord_id}: {data}")        

# ========= BOT-LOOP =========

def run_bot(bot_state, stop_event, status_callback, sleep_seconds=5):
    """
    Grid-Bot:
    - überwacht BUYs und setzt SELLs
    - pro Grid erst neuer BUY, wenn SELL erledigt ist
    - MAX_OPEN_BUYS begrenzt parallele Buys
    """
    pair = bot_state["pair"]
    pairs = bot_state["pairs"]
    open_buys = []      # Liste der aktuell überwachten BUY-Orders
    sell_count = 0
    profit_eur = 0.0

    status_callback(f"=== Starte Grid-Bot für {pair} (max {MAX_OPEN_BUYS} offene BUYs) ===")

    try:
        while not stop_event.is_set():
            try:
                # 1) aktuellen Preis holen
                price = get_last_price(pair)
                status_callback(("PRICE", price, len(open_buys)))

                # 2) neue BUY-Orders setzen (nur wenn Platz laut MAX_OPEN_BUYS)
                if len(open_buys) < min(len(pairs), MAX_OPEN_BUYS):
                    trade_path = "/api/v5/trade/order"
                    trade_url = BASE_URL + trade_path
                    status_callback(f"Setze BUYs (aktuell {len(open_buys)})")

                    for idx, pd in enumerate(pairs, start=1):
                        if pd["status"] != "waiting_buy":
                            continue
                        if pd["buy_price"] > price:
                            continue

                        buy_order = {
                            "instId": pair,
                            "tdMode": "cash",
                            "side": "buy",
                            "ordType": "limit",
                            "px": f"{pd['buy_price']:.4f}",
                            "sz": f"{pd['size_wif']:.4f}",
                        }
                        body = json.dumps(buy_order)
                        headers = get_headers("POST", trade_path, body)
                        resp = requests.post(trade_url, headers=headers, data=body, timeout=10)
                        data = resp.json()
                        status_callback((f"Grid {idx} BUY: code={data.get('code')} msg={data.get('msg')}"))

                        if data.get("code") == "0":
                            ord_id = data["data"][0]["ordId"]
                            open_buys.append(
                                {
                                    "ordId": ord_id,
                                    "instId": pair,
                                    "sell_price": pd["sell_price"],
                                    "grid_idx": idx - 1,
                                }
                            )
                            pd["status"] = "waiting_fill_buy"
                            pd["buy_ordId"] = ord_id

                        if len(open_buys) >= MAX_OPEN_BUYS:
                            break

                        time.sleep(0.3)
                
                # 3) BUY-Status prüfen -> SELL setzen
                if open_buys:
                    open_resp = fetch_open_orders(pair)
                    open_list = open_resp.get("data", [])

                    for buy in list(open_buys):
                        match = next((o for o in open_list if o["ordId"] == buy["ordId"]), None)

                        # BUY nicht mehr pending -> gefüllt -> SELL setzen
                        if match is None:
                            status_callback(f"BUY gefüllt -> SELL setzen {buy['ordId']}")
                            ok = _place_sell_for_buy(buy, pairs, status_callback)
                            grid_idx = buy.get("grid_idx")
                            if not ok and grid_idx is not None:
                                # SELL fehlgeschlagen, Grid für manuellen Eingriff markieren
                                pd = pairs[grid_idx]
                                pd["status"] = "sell_failed"
                            open_buys.remove(buy)
                            continue

                        state = match.get("state")
                        if state in ("canceled", "cancelling"):
                            grid_idx = buy.get("grid_idx")
                            if grid_idx is not None:
                                pd = pairs[grid_idx]
                                pd["status"] = "waiting_buy"
                                pd["buy_ordId"] = None
                            status_callback(f"BUY storniert: {buy['ordId']}")
                            open_buys.remove(buy)

                # 4) SELL-Status prüfen -> Real-Profit zählen & Grid freigeben
                sell_open = fetch_open_orders(pair)
                sell_list = sell_open.get("data", [])

                for idx, pd in enumerate(pairs):
                    if pd.get("status") != "waiting_sell" or not pd.get("sell_ordId"):
                        continue

                    soid = pd["sell_ordId"]
                    smatch = next((o for o in sell_list if o["ordId"] == soid), None)

                    if smatch is None:
                        # ECHTE OKX-Werte aus Fills holen
                        sell_details = fetch_order(pair, soid)
                        buy_ord_id = pd.get("buy_ordId")
                        buy_details = fetch_order(pair, buy_ord_id) if buy_ord_id else {}

                        od_sell = (sell_details.get("data") or [{}])[0]
                        od_buy  = (buy_details.get("data") or [{}])[0]

                        # SELL: echter Wert und Fee
                        filled_sz_sell = float(od_sell.get("accFillSz", od_sell.get("fillSz", "0") or "0"))
                        if "fillNotional" in od_sell:
                            pd["value_sell_eur"] = float(od_sell["fillNotional"])
                        else:
                            pd["value_sell_eur"] = float(od_sell.get("avgPx", pd["sell_price"])) * filled_sz_sell
                        pd["fee_sell_eur"] = float(od_sell.get("fee", 0))

                        # BUY: echter Wert und Fee
                        filled_sz_buy = float(od_buy.get("accFillSz", od_buy.get("fillSz", "0") or "0"))
                        if "fillNotional" in od_buy:
                            pd["value_buy_eur"] = float(od_buy["fillNotional"])
                        else:
                            pd["value_buy_eur"] = float(od_buy.get("avgPx", pd["buy_price"])) * filled_sz_buy
                        pd["fee_buy_eur"] = float(od_buy.get("fee", 0))

                        trade_profit = calc_real_grid_profit(
                            pd["value_buy_eur"], pd["fee_buy_eur"],
                            pd["value_sell_eur"], pd["fee_sell_eur"]
                        )
                        sell_count += 1
                        profit_eur += trade_profit
                        status_callback(("STATS", sell_count, profit_eur))
                        status_callback(f"SELL fertig Grid {idx+1}: +{trade_profit:.4f}€ (Real)")

                        pd["status"] = "waiting_buy"
                        pd["buy_ordId"] = None
                        pd["sell_ordId"] = None
                    else:
                        if smatch.get("state") in ("canceled", "cancelling"):
                            status_callback(f"SELL storniert für Grid {idx+1}")
                            pd["status"] = "waiting_buy"
                            pd["sell_ordId"] = None

                # 5) Grid-Status loggen
                status_lines = []
                for i, pd in enumerate(pairs, start=1):
                    status_lines.append(f"Grid {i:02d}: BUY={pd['buy_price']:.2f}   |    SELL@{pd['sell_price']:.2f} ----> {pd['status']}")
                status_callback("                                                                                                     ".join(status_lines))

                time.sleep(sleep_seconds)

            except Exception as e:
                status_callback(f"Fehler im Loop: {e}")
                time.sleep(5)

    finally:
        status_callback("Stop-Flag erkannt -> offene BUY-Orders werden storniert...")
        cancel_all_buy_orders(pair, status_callback)
        status_callback("Bot gestoppt.")

# ========= TESTLAUF OHNE GUI =========

if __name__ == "__main__":
    state = init_bot("WIF-EUR", 0.34, 0.35, 10, 40.0, 1.01)
    print(json.dumps(state, indent=2))