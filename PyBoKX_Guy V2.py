import tkinter
import threading
import FreeSimpleGUI as sg
import PyBoKX
import time
log_lines = []
log_file = "/storage/emulated/0/PyBoKX/bot_status_log.txt"
sell_count = 0  # Falls nicht da
profit_eur = 0.0

def save_status_log():  # ← DEFINIERE HIER!
    """Sicheres TXT-Logging"""
    global sell_count, profit_eur, log_lines
    try:
        with open(log_file, "a") as f:  # Anhängen
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Sells: {sell_count} Profit: {profit_eur:.4f}€")
            if log_lines:f.write("".join(log_lines[-10:]) + " ")
        print(f"TXT saved: {log_file}")  # Konsole-Feedback
    except Exception as e:
        print(f"TXT-Error: {e}")

# Globale Steuerung
bot_running = False
bot_thread = None
stop_event = threading.Event()
window = None
current_mode = "WIF"
price_decimals = 4

def status_callback(msg):
    global window, log_lines
    if window:
        window.write_event_value("-STATUS_UPDATE-", msg)
        log_lines.append(str(msg))
        # Auto-save bei STATS/SELL
        if isinstance(msg, tuple) and msg[0] == "STATS":
            save_status_log()
        elif "SELL Grid" in str(msg):
            save_status_log()
 
def format_price(price):
    return f"{float(price):.{price_decimals}f}"

sg.theme("NeonGreen1")

# Layout (erweitert für bessere Übersicht)
layout = [
    [sg.Text("          PyBoKX Grid Bot     ", font=("Helvetica", 20, "bold"))],
    
    [sg.Combo(["SOL", "WIF"], key="-MODE-", default_value="WIF", enable_events=True, font=("Helvetica", 14, "bold"), size=(8,1)),
     sg.Text(" ", font=("Helvetica", 14, "bold")),
     sg.Input("WIF/EUR", key="-PAIR-", size=(10,1), font=("Helvetica", 14, "bold")), 
     sg.Text(" ", key="-PRICE-", font=("Helvetica", 20, "bold"), text_color="lime"),
     sg.Text("€", font=("Helvetica", 20), text_color="lime")],
    
    [sg.Text("High:"), sg.Input("0.3500", key="-LOWER-", size=(12,1)), sg.Text("Low:"), sg.Input("0.2950", key="-UPPER-", size=(12,1))],
    [sg.Text("Capital €:"), sg.Input("50", key="-CAPITAL-", size=(8,1)), sg.Text("Grids:"), sg.Input("25", key="-GRID-", size=(6,1)),
     sg.Text("Sell %:"), sg.Input("1.01", key="-SELLFACTOR-", size=(6,1))],
    
    [sg.Text("Grid-Win €:"), sg.Text("0.00", key="-GRIDPROFIT-", font=("Helvetica", 14, "bold"), text_color="gold")],
       
    [sg.Button("Grids Preview", key="-PREVIEW-", font=("Helvetica", 10, "bold"), button_color=("white", "green")),
     sg.Button("Reset failed", key="-RESET_FAIL-", font=("Helvetica", 10, "bold"), button_color=("black", "yellow"))],
     #sg.Button("Reset Grids", key="-RESET_GRIDS-", font=("Helvetica", 10, "bold"), button_color=("white", "orange"))],
    
    [sg.Button("Start", font=("Helvetica", 14, "bold"), button_color=("black", "lime")),
     sg.Button("Stop", font=("Helvetica", 14, "bold"), button_color=("white", "red"), disabled=False),
     sg.Text("                           "),
     sg.Button("Beenden", font=("Helvetica", 14, "bold"), button_color=("black", "white"))],
   
    [sg.Multiline(size=(70, 15), key="-LOG-", autoscroll=True, disabled=True, font=("Courier", 6))],
    
    [sg.Text("Buys offen:"), sg.Text("0", key="-OPENBUYS-", font=("Helvetica", 12, "bold"), text_color="cyan"),
     sg.Text("   Sells OK:"), sg.Text("0", key="-SELLCOUNT-", font=("Helvetica", 12, "bold"), text_color="yellow"),
     sg.Text("                    Profit €:"), sg.Text("0.00", key="-PROFIT-", font=("Helvetica", 12, "bold"), text_color="lime")]
]

window = sg.Window("PyBoKX Grid Bot v2", layout, finalize=True, resizable=True)


# Initial-Preis

try:
    pair = window["-PAIR-"].get()
    first_price = PyBoKX.get_last_price(pair)
    window["-PRICE-"].update(format_price(first_price))
except Exception as e:
    window["-LOG-"].update(f"Preis konnte nicht geladen werden: {e}", append=True)

window["-LOG-"].update("Bot bereit. Parameter eingeben und Start drücken.")

#____-- LogTXT--______

def save_profit_log():
    """GUI-Profit + Logs"""
    global log_lines, sell_count, profit_eur  # Deine Globals
    try:
        with open(log_file, "w") as f:
            f.write(f"=== GUI Profit Log | {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
            f.write(f"Sells: {sell_count} | Profit: {profit_eur:.4f}€")
            f.write("".join(log_lines[-50:]))
        print(f"Log: {log_file}")
    except:
        pass

# Event-Loop
while True:
    event, values = window.read(timeout=2000)

    if event == sg.WIN_CLOSED or event == "Beenden":
        stop_event.set()
        break

    # MODE-Wechsel → AUTO-FILL + Dezimalstellen
    if event == "-MODE-":
        current_mode = values["-MODE-"]
        window["-LOG-"].update(f"Mode: {current_mode} geladen", append=True)
        
        if current_mode == "SOL":
            price_decimals = 2
            window["-PAIR-"].update("SOL-EUR")
            window["-LOWER-"].update("120.00")
            window["-UPPER-"].update("100.00")
            window["-GRID-"].update("20")
            window["-CAPITAL-"].update("80")
        elif current_mode == "WIF":
            price_decimals = 4
            window["-PAIR-"].update("WIF-EUR")
            window["-LOWER-"].update("0.3500")
            window["-UPPER-"].update("0.2950")
            window["-GRID-"].update("25")
            window["-CAPITAL-"].update("50")

    # Grids Preview (unverändert, nur kosmetisch )
    if event == "-PREVIEW-":
        try:
            lower = float(values["-LOWER-"])
            upper = float(values["-UPPER-"])
            grid_n = int(values["-GRID-"])
            start_capital = float(values["-CAPITAL-"])
            sell_factor = float(values["-SELLFACTOR-"])

            levels, step = PyBoKX.build_grid(lower, upper, grid_n)
            pairs, buy_levels, eur_per_buy = PyBoKX.build_grid_pairs(levels, start_capital, sell_factor)
        
            lines = ["", "", ""]
            total_profit = 0.0
        
            for i, pd in enumerate(pairs, start=1):
                buy_p = pd["buy_price"]
                sell_p = pd["sell_price"]
                size = pd["size_wif"]
                profit = (sell_p - buy_p) * size * 0.996
                total_profit += profit
                lines.append(f"Grid {i:2d}: BUY {format_price(pd['buy_price'])} ---→ SELL {format_price(pd['sell_price'])}| Size:{pd['size_wif']:.4f} | +{profit:.4f}€       ")

        
            preview_text = "".join(lines)
            preview_layout = [
                [sg.Text("Grid Preview & Profit", font=("Arial", 16, "bold"))],
                [sg.Multiline(preview_text, size=(64, 15), disabled=True,
                              font=("Courier", 8), autoscroll=True)],
                [sg.Text(f"GRIDPROFIT: {profit:.4f}€",
                         font=("Arial", 14, "bold"), text_color="green")],
                [sg.Button("Schließen")]
            ]
            preview_win = sg.Window("Grids Preview", preview_layout,
                                    modal=True, keep_on_top=True, finalize=True)
        
            while True:
                p_event, _ = preview_win.read()
                if p_event == sg.WIN_CLOSED or p_event == "Schließen":
                    break
            preview_win.close()
            window["-GRIDPROFIT-"].update(f"{profit:.4f}")
        
        except Exception as e:
            sg.popup_error(f"Preview-Fehler: {e}", keep_on_top=True)

    # Zyklischer Preis-Refresh
    if event == sg.TIMEOUT_EVENT:
        try:
            pair = values["-PAIR-"]
            p = PyBoKX.get_last_price(pair)
            window["-PRICE-"].update(format_price(p))  # FIX: p benutzen
        except:
            pass

    # Bot starten
    if event == "Start" and bot_thread is None:
        try:
            pair = values["-PAIR-"]
            lower = float(values["-LOWER-"])
            upper = float(values["-UPPER-"])
            grid_n = int(values["-GRID-"])
            capital = float(values["-CAPITAL-"])
            sell_factor = float(values["-SELLFACTOR-"])

            bot_state = PyBoKX.init_bot(pair, lower, upper, grid_n, capital, sell_factor)

            def bot_runner():
                PyBoKX.run_bot(bot_state, stop_event, status_callback)

            stop_event.clear()
            bot_thread = threading.Thread(target=bot_runner, daemon=True)
            bot_thread.start()
            window["-LOG-"].update("Bot gestartet...", append=True)
            status_callback("TEST GRID 1 waiting_buy")  # Sofort-Test
        except ValueError as e:
            window["-LOG-"].update(f"Fehler: {e}", append=True)

    # Bot stoppen
    if event == "Stop" and bot_thread:
        stop_event.set()
        window["-LOG-"].update("Stop angefordert...", append=True)
        bot_thread = None

    # Reset failed
    if event == "-RESET_FAIL-":
        try:
            if bot_state is not None:
                msg = PyBoKX.reset_sell_failed(bot_state["pairs"], values["-PAIR-"], status_callback)
                # Falls reset_sell_failed selbst über status_callback loggt, hier optional:
                if msg:
                    window["-LOG-"].update(str(msg) + "", append=True)
            else:
                window["-LOG-"].update("Bot nicht aktiv", append=True)
        except Exception as e:
            window["-LOG-"].update(f"Reset-Fail-Fehler: {e}", append=True)

    if event == "-STATUS_UPDATE-":
        msg = values[event]
        if isinstance(msg, tuple) and msg[0] == "PRICE":
            _, price, open_count = msg
            window["-PRICE-"].update(format_price(price))
            window["-OPENBUYS-"].update(str(open_count))
        elif isinstance(msg, tuple) and msg[0] == "STATS":
            _, sell_count, profit_eur = msg
            window["-SELLCOUNT-"].update(str(sell_count))
            window["-PROFIT-"].update(f"{profit_eur:.2f}")
        else:
            window["-LOG-"].update(str(msg) + "", append=False)
        # Safe-Log (nach if/else)
        try:
            log_lines.append(f"[{time.strftime('%H:%M:%S')}] {msg}")
        except:
            pass  # Ignoriert Errors

window.close()
