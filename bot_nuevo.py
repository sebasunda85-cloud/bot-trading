import time
from datetime import datetime, timedelta
import threading
from flask import Flask
import ccxt
import requests

# ------------------------------------------------------------------
# CONFIGURACIÓN GENERAL Y FLASK
# ------------------------------------------------------------------
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot Cazador Spot en ejecución.", 200

def run_flask():
    app.run(host='0.0.0.0', port=8080)

# ------------------------------------------------------------------
# CONFIGURACIÓN DE TELEGRAM Y PARÁMETROS
# ------------------------------------------------------------------
# Coloca aquí tus credenciales de Telegram
TELEGRAM_BOT_TOKEN = "TU_TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_ID = "TU_TELEGRAM_CHAT_ID"

PAIRS = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT']
TRADE_AMOUNT_USDT = 5.0  # Monto fijo de $5 USDT por operación
MAX_ACTIVE_TRADES_PER_PAIR = 4  # Máximo 4 alertas acumuladas ($20 USDT expuestos)
COOLDOWN_MINUTES = 30  # Bloqueo de 30 minutos por par tras enviar alerta

# Control de estado interno
cooldown_tracker = {pair: None for pair in PAIRS}
active_trades_counter = {pair: 0 for pair in PAIRS}
last_heartbeat = datetime.now()

exchange = ccxt.binance({'enableRateLimit': True})

def send_telegram_msg(message):
    """Envía un mensaje a Telegram."""
    if TELEGRAM_BOT_TOKEN == "TU_TELEGRAM_BOT_TOKEN":
        print(f"[SIMULACIÓN TELEGRAM]:\n{message}\n")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Error enviando mensaje a Telegram: {e}")

# ------------------------------------------------------------------
# INDICADORES TÉCNICOS Y FILTROS DE INTELIGENCIA DE MERCADO
# ------------------------------------------------------------------
def calculate_rsi(candles, period=14):
    """Calcula el RSI de 14 periodos."""
    closes = [c[4] for c in candles]
    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        if diff >= 0:
            gains.append(diff)
            losses.append(0)
        else:
            gains.append(abs(diff))
            losses.append(abs(diff))
    
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def check_anti_knife_filter(candle):
    """
    Filtro Anti-Cuchillo Cayendo:
    Verifica que la vela no sea un cuerpo rojo gigante sin mecha inferior (caída libre).
    """
    open_p, high_p, low_p, close_p = candle[1], candle[2], candle[3], candle[4]
    cuerpo = abs(close_p - open_p)
    mecha_inferior = min(open_p, close_p) - low_p
    rango_total = high_p - low_p

    if rango_total == 0:
        return False

    # Rechaza si el cuerpo representa más del 80% del rango total y casi no hay mecha inferior de frenado
    if (cuerpo / rango_total > 0.80) and (mecha_inferior / rango_total < 0.10) and (close_p < open_p):
        return False  # Cuchillo cayendo detectado
    return True

def check_volume_absorption(candles):
    """
    Filtro de Volumen de Absorción:
    Verifica que el volumen de la vela actual sea superior al promedio de las últimas 5 velas.
    """
    volumes = [c[5] for c in candles[-6:-1]]
    current_volume = candles[-1][5]
    avg_volume = sum(volumes) / len(volumes) if volumes else 0
    return current_volume > avg_volume

# ------------------------------------------------------------------
# LÓGICA DE ESCANEO SPOT CAZADOR
# ------------------------------------------------------------------
def analyze_market():
    global last_heartbeat
    found_opportunity = False

    for pair in PAIRS:
        now = datetime.now()

        # 1. Verificar Cooldown
        if cooldown_tracker[pair] and now < cooldown_tracker[pair]:
            continue

        # 2. Verificar Límite de Trades Activos
        if active_trades_counter[pair] >= MAX_ACTIVE_TRADES_PER_PAIR:
            continue

        try:
            # Obtener velas de 5m
            candles = exchange.fetch_ohlcv(pair, timeframe='5m', limit=30)
            if len(candles) < 20:
                continue

            current_candle = candles[-1]
            price = current_candle[4]
            rsi = calculate_rsi(candles)

            # Condición de compra: RSI en zona de oportunidad (< 40)
            if rsi < 40:
                # Aplicar Filtro Anti-Cuchillo Cayendo
                if not check_anti_knife_filter(current_candle):
                    continue

                # Aplicar Filtro de Volumen de Absorción
                if not check_volume_absorption(candles):
                    continue

                # --- ¡SEÑAL VÁLIDA ENCONTRADA! ---
                found_opportunity = True
                
                # Cálculo de Take Profit (+1.2% a +1.5%)
                tp_price_low = price * 1.012
                tp_price_high = price * 1.015
                
                # Cantidad exacta de tokens para $5 USDT
                token_amount = TRADE_AMOUNT_USDT / price
                symbol_clean = pair.replace('/', '_')
                binance_link = f"https://www.binance.com/es/trade/{symbol_clean}?type=spot"

                # Estado de RSI Extremo
                rsi_extra_info = "⚠️ *¡RSI EXTREMO DETECTADO!*" if rsi < 30 else "Normal en sobreventa"

                # Construcción del mensaje de alerta
                alert_msg = (
                    f"🟢 *SEÑAL DE COMPRA SPOT*\n\n"
                    f"📌 *Par:* `{pair}`\n"
                    f"💵 *Precio de Entrada:* `{price:.4f}` USDT\n"
                    f"🎯 *Take Profit Target (+1.2% - +1.5%):*\n"
                    f"   └ `{tp_price_low:.4f}` - `{tp_price_high:.4f}` USDT\n\n"
                    f"🛍️ *Monto Sugerido:* ${TRADE_AMOUNT_USDT} USDT\n"
                    f"🔢 *Tokens a Comprar:* `{token_amount:.6f}`\n"
                    f"📊 *RSI (14):* `{rsi:.2f}` ({rsi_extra_info})\n\n"
                    f"🔗 [Abrir en Binance]({binance_link})"
                )

                send_telegram_msg(alert_msg)

                # Actualizar contadores y cooldown
                cooldown_tracker[pair] = now + timedelta(minutes=COOLDOWN_MINUTES)
                active_trades_counter[pair] += 1
                last_heartbeat = now  # Reiniciar temporizador del heartbeat

        except Exception as e:
            print(f"Error analizando {pair}: {e}")

    # ------------------------------------------------------------------
    # HEARTBEAT DE PACIENCIA (CADA 10 MINUTOS)
    # ------------------------------------------------------------------
    if not found_opportunity and (datetime.now() - last_heartbeat) >= timedelta(minutes=10):
        heartbeat_msg = "Gracias por tu espera y disciplina, aun no se a encontrado patrones"
        send_telegram_msg(heartbeat_msg)
        last_heartbeat = datetime.now()

# ------------------------------------------------------------------
# BUCLE PRINCIPAL
# ------------------------------------------------------------------
def main_loop():
    print("🚀 Bot Cazador Spot iniciado correctamente...")
    while True:
        try:
            analyze_market()
        except Exception as e:
            print(f"Error en bucle principal: {e}")
        time.sleep(60)  # Revisa el mercado cada 1 minuto

if __name__ == "__main__":
    # Iniciar servidor Flask en hilo secundario (para Render)
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()

    # Iniciar bucle del bot
    main_loop()