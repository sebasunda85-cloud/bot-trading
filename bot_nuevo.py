import ccxt
import requests
import pandas as pd
import time
import threading
from datetime import datetime

# ==========================================
# CREDENCIALES Y CONFIGURACIÓN
# ==========================================
TELEGRAM_TOKEN = "8891876215:AAGZr42rM9YFuEwnHWkhC3UVH_XUTUdIB9k"
CHAT_ID = "8269698271"

PARES = ['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT']
TEMPORALIDADES = ['1d', '4h', '1h', '15m']

# FILTROS DE CALIDAD Y GANANCIA
UMBRAL_PUNTAJE_MINIMO = 65    # Probabilidad mínima del trade (score)
GANANCIA_MINIMA_PCT = 1.0     # Porcentaje mínimo de ganancia proyectada en el precio (1%)

exchange = ccxt.binance({
    'enableRateLimit': True,
    'options': {'defaultType': 'spot'}
})

def enviar_telegram(mensaje):
    """Envía alertas a Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": mensaje, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Error en Telegram: {e}")

def obtener_velas(symbol, timeframe, limit=100):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
    except Exception as e:
        print(f"Error al obtener datos para {symbol} ({timeframe}): {e}")
        return None

def obtener_tendencia_macro(symbol):
    """Determina el sesgo macro con EMA 50 en 4h"""
    df = obtener_velas(symbol, timeframe='4h', limit=60)
    if df is None or len(df) < 50:
        return 'NEUTRAL'
    
    ema50 = df['close'].ewm(span=50, adjust=False).mean().iloc[-1]
    precio_actual = df['close'].iloc[-1]
    
    return 'ALCISTA' if precio_actual > ema50 else 'BAJISTA'

def analizar_orderbook(symbol, limit=20):
    """Analiza la presión real del Orderbook"""
    try:
        orderbook = exchange.fetch_order_book(symbol, limit)
        vol_bids = sum([bid[1] for bid in orderbook['bids']])
        vol_asks = sum([ask[1] for ask in orderbook['asks']])
        vol_total = vol_bids + vol_asks
        
        if vol_total == 0:
            return 50.0, 50.0, "NEUTRAL"
            
        pct_bids = (vol_bids / vol_total) * 100
        pct_asks = (vol_asks / vol_total) * 100
        
        if pct_bids > 58:
            presion = "COMPRADORA"
        elif pct_asks > 58:
            presion = "VENDEDORA"
        else:
            presion = "NEUTRAL"
            
        return round(pct_bids, 1), round(pct_asks, 1), presion
    except Exception as e:
        return 50.0, 50.0, "NEUTRAL"

def calcular_puntaje(direccion_patron, tipo_patron, tendencia_macro, pct_bids, pct_asks):
    score = 0
    if (direccion_patron == 'ALCISTA' and tendencia_macro == 'ALCISTA') or \
       (direccion_patron == 'BAJISTA' and tendencia_macro == 'BAJISTA'):
        score += 30
    elif tendencia_macro == 'NEUTRAL':
        score += 15

    if direccion_patron == 'ALCISTA':
        if pct_bids >= 55: score += 30
        elif pct_bids >= 48: score += 15
    else:
        if pct_asks >= 55: score += 30
        elif pct_asks >= 48: score += 15

    if "BARRIDO" in tipo_patron: score += 40
    elif "ORDER BLOCK" in tipo_patron: score += 30
    elif "FVG" in tipo_patron: score += 25

    return min(score, 95)

def calcular_puntos_salida(direccion, entrada, zona_min, zona_max):
    if direccion == 'ALCISTA':
        sl = zona_min * 0.997
        riesgo = entrada - sl
        if riesgo <= 0: 
            riesgo = entrada * 0.005
            sl = entrada - riesgo
        tp1 = entrada + (riesgo * 1.5)
        tp2 = entrada + (riesgo * 3.0)
        rrr = 3.0
        # Porcentaje de ganancia hasta el TP1
        pct_ganancia = ((tp1 - entrada) / entrada) * 100
    else:
        sl = zona_max * 1.003
        riesgo = sl - entrada
        if riesgo <= 0: 
            riesgo = entrada * 0.005
            sl = entrada + riesgo
        tp1 = entrada - (riesgo * 1.5)
        tp2 = entrada - (riesgo * 3.0)
        rrr = 3.0
        # Porcentaje de ganancia hasta el TP1
        pct_ganancia = ((entrada - tp1) / entrada) * 100

    return round(sl, 2), round(tp1, 2), round(tp2, 2), rrr, round(pct_ganancia, 2)

def detectar_smc(df, tendencia_macro, pct_bids, pct_asks):
    alertas = []
    if df is None or len(df) < 15:
        return alertas
        
    precio_actual = df['close'].iloc[-1]
    minimo_previo = df['low'].iloc[-15:-1].min()
    maximo_previo = df['high'].iloc[-15:-1].max()

    # FVG Alcista
    if df['low'].iloc[-1] > df['high'].iloc[-3]:
        if tendencia_macro in ['ALCISTA', 'NEUTRAL']:
            z_min, z_max = df['high'].iloc[-3], df['low'].iloc[-1]
            puntaje = calcular_puntaje('ALCISTA', 'FVG ALCISTA', tendencia_macro, pct_bids, pct_asks)
            sl, tp1, tp2, rrr, pct_ganancia = calcular_puntos_salida('ALCISTA', precio_actual, z_min, z_max)
            alertas.append({
                'tipo': 'FVG ALCISTA', 'zona_min': z_min, 'zona_max': z_max,
                'puntaje': puntaje, 'sl': sl, 'tp1': tp1, 'tp2': tp2, 'rrr': rrr, 
                'pct_ganancia': pct_ganancia, 'dir': 'ALCISTA'
            })
            
    # FVG Bajista
    if df['high'].iloc[-1] < df['low'].iloc[-3]:
        if tendencia_macro in ['BAJISTA', 'NEUTRAL']:
            z_min, z_max = df['high'].iloc[-1], df['low'].iloc[-3]
            puntaje = calcular_puntaje('BAJISTA', 'FVG BAJISTA', tendencia_macro, pct_bids, pct_asks)
            sl, tp1, tp2, rrr, pct_ganancia = calcular_puntos_salida('BAJISTA', precio_actual, z_min, z_max)
            alertas.append({
                'tipo': 'FVG BAJISTA', 'zona_min': z_min, 'zona_max': z_max,
                'puntaje': puntaje, 'sl': sl, 'tp1': tp1, 'tp2': tp2, 'rrr': rrr, 
                'pct_ganancia': pct_ganancia, 'dir': 'BAJISTA'
            })

    # Order Block Alcista
    if (df['close'].iloc[-3] < df['open'].iloc[-3]) and (df['close'].iloc[-2] > df['high'].iloc[-3]):
        if tendencia_macro in ['ALCISTA', 'NEUTRAL']:
            z_min, z_max = df['low'].iloc[-3], df['high'].iloc[-3]
            puntaje = calcular_puntaje('ALCISTA', 'ORDER BLOCK ALCISTA', tendencia_macro, pct_bids, pct_asks)
            sl, tp1, tp2, rrr, pct_ganancia = calcular_puntos_salida('ALCISTA', precio_actual, z_min, z_max)
            alertas.append({
                'tipo': 'ORDER BLOCK ALCISTA', 'zona_min': z_min, 'zona_max': z_max,
                'puntaje': puntaje, 'sl': sl, 'tp1': tp1, 'tp2': tp2, 'rrr': rrr, 
                'pct_ganancia': pct_ganancia, 'dir': 'ALCISTA'
            })
            
    # Order Block Bajista
    if (df['close'].iloc[-3] > df['open'].iloc[-3]) and (df['close'].iloc[-2] < df['low'].iloc[-3]):
        if tendencia_macro in ['BAJISTA', 'NEUTRAL']:
            z_min, z_max = df['low'].iloc[-3], df['high'].iloc[-3]
            puntaje = calcular_puntaje('BAJISTA', 'ORDER BLOCK BAJISTA', tendencia_macro, pct_bids, pct_asks)
            sl, tp1, tp2, rrr, pct_ganancia = calcular_puntos_salida('BAJISTA', precio_actual, z_min, z_max)
            alertas.append({
                'tipo': 'ORDER BLOCK BAJISTA', 'zona_min': z_min, 'zona_max': z_max,
                'puntaje': puntaje, 'sl': sl, 'tp1': tp1, 'tp2': tp2, 'rrr': rrr, 
                'pct_ganancia': pct_ganancia, 'dir': 'BAJISTA'
            })

    # Barrido Alcista
    if df['low'].iloc[-1] < minimo_previo and df['close'].iloc[-1] > minimo_previo:
        if tendencia_macro in ['ALCISTA', 'NEUTRAL']:
            z_min, z_max = df['low'].iloc[-1], minimo_previo
            puntaje = calcular_puntaje('ALCISTA', 'BARRIDO DE LIQUIDEZ ALCISTA', tendencia_macro, pct_bids, pct_asks)
            sl, tp1, tp2, rrr, pct_ganancia = calcular_puntos_salida('ALCISTA', precio_actual, z_min, z_max)
            alertas.append({
                'tipo': 'BARRIDO DE LIQUIDEZ ALCISTA', 'zona_min': z_min, 'zona_max': z_max,
                'puntaje': puntaje, 'sl': sl, 'tp1': tp1, 'tp2': tp2, 'rrr': rrr, 
                'pct_ganancia': pct_ganancia, 'dir': 'ALCISTA'
            })
            
    # Barrido Bajista
    if df['high'].iloc[-1] > maximo_previo and df['close'].iloc[-1] < maximo_previo:
        if tendencia_macro in ['BAJISTA', 'NEUTRAL']:
            z_min, z_max = maximo_previo, df['high'].iloc[-1]
            puntaje = calcular_puntaje('BAJISTA', 'BARRIDO DE LIQUIDEZ BAJISTA', tendencia_macro, pct_bids, pct_asks)
            sl, tp1, tp2, rrr, pct_ganancia = calcular_puntos_salida('BAJISTA', precio_actual, z_min, z_max)
            alertas.append({
                'tipo': 'BARRIDO DE LIQUIDEZ BAJISTA', 'zona_min': z_min, 'zona_max': z_max,
                'puntaje': puntaje, 'sl': sl, 'tp1': tp1, 'tp2': tp2, 'rrr': rrr, 
                'pct_ganancia': pct_ganancia, 'dir': 'BAJISTA'
            })

    return alertas

def ejecutar_escaneo_multipar():
    hora_actual = datetime.now().strftime('%I:%M:%S %p')
    print("\n[" + hora_actual + "] Escaneando mercado con doble filtro (Puntaje >= " + str(UMBRAL_PUNTAJE_MINIMO) + "% y Ganancia >= " + str(GANANCIA_MINIMA_PCT) + "%)...")
    hallazgos = []
    
    for simbolo in PARES:
        tendencia_macro = obtener_tendencia_macro(simbolo)
        pct_bids, pct_asks, presion = analizar_orderbook(simbolo)
        
        for tf in TEMPORALIDADES:
            df = obtener_velas(simbolo, tf)
            if df is not None:
                precio_actual = df['close'].iloc[-1]
                alertas_smc = detectar_smc(df, tendencia_macro, pct_bids, pct_asks)
                
                for al in alertas_smc:
                    # DOBLE FILTRO: Puntaje mínimo Y Porcentaje de ganancia mínimo del 1%
                    if al['puntaje'] >= UMBRAL_PUNTAJE_MINIMO and al['pct_ganancia'] >= GANANCIA_MINIMA_PCT:
                        hallazgos.append({
                            'par': simbolo,
                            'direccion': al['dir'],
                            'precio': precio_actual,
                            'tp1': al['tp1'],
                            'rrr': al['rrr'],
                            'presion': presion,
                            'puntaje': al['puntaje'],
                            'pct_ganancia': al['pct_ganancia']
                        })

    if hallazgos:
        for h in hallazgos:
            if h['direccion'] == 'ALCISTA':
                ejecutar_str = "COMPRA 🟢"
            else:
                ejecutar_str = "VENTA 🔴"

            # Mensaje que incluye ambas métricas claramente separadas
            msg = (
                f"Par: {h['par']}\n"
                f"Ejecutar: {ejecutar_str}\n"
                f"Probabilidad del trade: {h['puntaje']}%\n"
                f"Porcentaje de ganancia: +{h['pct_ganancia']}%\n"
                f"Precio de entrada: ${h['precio']:,.2f}\n"
                f"Precio de salida: ${h['tp1']:,.2f}\n"
                f"Ratio rr: 1:{h['rrr']}\n"
                f"Orderbook: {h['presion']}\n"
                f"Hora y fecha: {datetime.now().strftime('%I:%M:%S %p - %d/%m/%Y')}"
            )
            enviar_telegram(msg)
        print(f"✅ {len(hallazgos)} señal(es) válidas enviadas a Telegram.")
    else:
        print("ℹ️ Escaneo finalizado sin señales que cumplan los filtros.")

def consulta_express_par(simbolo):
    tendencia_macro = obtener_tendencia_macro(simbolo)
    pct_bids, pct_asks, presion = analizar_orderbook(simbolo)
    df = obtener_velas(simbolo, '15m')
    precio = df['close'].iloc[-1] if df is not None else 0.0
    
    msg = (
        f"Par: {simbolo}\n"
        f"Tendencia Macro: {tendencia_macro}\n"
        f"Precio Actual: ${precio:,.2f}\n"
        f"Orderbook: {presion}\n"
        f"Hora y fecha: {datetime.now().strftime('%I:%M:%S %p - %d/%m/%Y')}"
    )
    enviar_telegram(msg)

def escuchar_comandos_telegram():
    offset = 0
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    
    while True:
        try:
            res = requests.get(url, params={"offset": offset, "timeout": 10}, timeout=15)
            data = res.json()
            
            if data.get("ok"):
                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    message = update.get("message", {})
                    text = message.get("text", "").strip().lower()
                    chat_id = str(message.get("chat", {}).get("id"))
                    
                    if chat_id != CHAT_ID:
                        continue

                    if text in ['/escaneo', '/scan']:
                        enviar_telegram("⚡ *Iniciando escaneo con doble filtro...*")
                        ejecutar_escaneo_multipar()
                    elif text in ['/btc', 'btc']:
                        consulta_express_par('BTC/USDT')
                    elif text in ['/eth', 'eth']:
                        consulta_express_par('ETH/USDT')
                    elif text in ['/bnb', 'bnb']:
                        consulta_express_par('BNB/USDT')
                    elif text in ['/sol', 'sol']:
                        consulta_express_par('SOL/USDT')

        except Exception as e:
            time.sleep(3)
        time.sleep(1)

if __name__ == "__main__":
    print(f"🚀 Escáner con Doble Filtro (Probabilidad + Ganancia >= 1%) Activo...")
    
    hilo_telegram = threading.Thread(target=escuchar_comandos_telegram, daemon=True)
    hilo_telegram.start()
    
    while True:
        try:
            ejecutar_escaneo_multipar()
            time.sleep(120)
        except Exception as e:
            print(f"Error en bucle principal: {e}")
            time.sleep(10)