# -*- coding: utf-8 -*-
"""
caudal_qc_core.py — geeSWAT
Lógica pura de control de calidad (QC) y relleno de vacíos para series
diarias de caudal observado (p. ej. SENAMHI).

Sin dependencias externas (solo stdlib) para no requerir pandas/numpy
dentro del entorno Python embebido de QGIS, siguiendo la convención ya
usada en el resto de módulos de geeSWAT (climaticos_descargar.py,
cn_descargar_cn.py, etc.).

geeSWAT v1.2.3 — Geomatica Ambiental
"""
import csv
import math
import re
import statistics
from datetime import date, timedelta

# ─────────────────────────────────────────────────────────────────────────────
# Constantes
# ─────────────────────────────────────────────────────────────────────────────

# Tokens que representan "sin dato" en distintos exportes (SENAMHI, ANA, etc.)
TOKENS_VACIO = {
    '', 'na', 'n/a', '#n/d', 'nan', 's/d', 'null', 'none',
    '-999', '-9999', '-99', 'sd', '#value!', '#error',
}

METODOS_INTERPOLACION = ['loglineal', 'lineal', 'spline']


class RegistroCaudal:
    """Un día de la serie: fecha, valor original (texto), valor numérico."""
    __slots__ = ('fecha', 'crudo', 'valor', 'origen')

    def __init__(self, fecha, crudo, valor, origen='medido'):
        self.fecha = fecha      # datetime.date
        self.crudo = crudo      # str original tal como venía en el CSV (o None si se insertó)
        self.valor = valor      # float o None
        self.origen = origen    # 'medido' | 'faltante' | 'rellenado' | 'insertado'


# ─────────────────────────────────────────────────────────────────────────────
# Lectura y parseo
# ─────────────────────────────────────────────────────────────────────────────

def _parsear_valor(crudo):
    """Convierte texto crudo a float, o None si es un token de vacío / no numérico."""
    if crudo is None:
        return None
    txt = crudo.strip()
    if txt.lower() in TOKENS_VACIO:
        return None
    txt_norm = txt.replace(',', '.')
    try:
        return float(txt_norm)
    except ValueError:
        return None


def _parsear_valor_celda(valor):
    """Como _parsear_valor pero para celdas de Excel: pueden llegar ya como
    int/float (openpyxl las entrega tipadas), como texto, o None si la celda
    está vacía."""
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    if isinstance(valor, str):
        return _parsear_valor(valor)
    return None


def leer_csv(path, col_fecha='Date', col_flow='Flow', formato_fecha='%m/%d/%Y'):
    """
    Lee el CSV de entrada. Devuelve (registros, errores_parseo)
    registros: lista de RegistroCaudal ordenada por fecha
    errores_parseo: lista de (num_fila, texto) para fechas no interpretables
    """
    registros = []
    errores = []
    with open(path, 'r', newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        if col_fecha not in reader.fieldnames or col_flow not in reader.fieldnames:
            raise ValueError(
                f"El CSV debe tener columnas '{col_fecha}' y '{col_flow}'. "
                f"Columnas encontradas: {reader.fieldnames}"
            )
        for i, row in enumerate(reader, start=2):  # fila 1 = encabezado
            crudo_fecha = (row.get(col_fecha) or '').strip()
            crudo_flow = row.get(col_flow)
            try:
                from datetime import datetime as _dt
                f_obj = _dt.strptime(crudo_fecha, formato_fecha).date()
            except ValueError:
                errores.append((i, crudo_fecha))
                continue
            valor = _parsear_valor(crudo_flow)
            origen = 'medido' if valor is not None else 'faltante'
            registros.append(RegistroCaudal(f_obj, crudo_flow, valor, origen))

    registros.sort(key=lambda r: r.fecha)
    return registros, errores


def leer_excel_ancho(path, hoja=None, mes_inicio_anio_hidro=10):
    """
    Lee un Excel en formato ANCHO típico de SENAMHI/ANA: primera columna con
    la plantilla día/mes del año hidrológico (p. ej. 01-oct .. 30-sep), y una
    columna por año hidrológico con encabezado tipo '2009-2010', '2009/2010',
    'AH 2009-2010', etc.

    Corrige respecto al patrón usual de estos scripts:
      - Extrae el año de inicio con regex (4 dígitos), no con split('-')[0]:
        tolera '2009/2010', 'Año 2009-2010', espacios, etc.
      - Si la plantilla incluye 29-feb y el año calculado no es bisiesto,
        NO truena: descarta esa celda puntual y lo reporta (en vez de
        levantar ValueError y detener todo el proceso).
      - Convierte cada celda con _parsear_valor_celda (coerción numérica
        real), así que un código de texto tipo 'S/D' se trata como faltante
        en vez de romper la interpolación aguas abajo.

    Requiere openpyxl (no pandas, para mantener la convención de
    dependencias mínimas del resto de geeSWAT).

    Devuelve (registros, avisos) donde avisos es una lista de strings con
    filas/columnas descartadas y por qué.
    """
    try:
        import openpyxl
    except ImportError:
        raise ValueError(
            "Falta el módulo 'openpyxl' para leer archivos Excel.\n"
            "Instálalo con: python -m pip install openpyxl"
        )

    avisos = []
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb[hoja] if hoja else wb.active

    filas = list(ws.iter_rows(values_only=True))
    if not filas:
        raise ValueError("El archivo Excel está vacío.")

    encabezado = filas[0]
    filas_datos = filas[1:]

    # columna 0 = plantilla día/mes; columnas 1..n = años hidrológicos
    columnas_anio = []
    for idx_col in range(1, len(encabezado)):
        titulo = encabezado[idx_col]
        if titulo is None:
            continue
        m = re.search(r'(\d{4})', str(titulo))
        if not m:
            avisos.append(f"Columna '{titulo}' ignorada: no se encontró un año de 4 dígitos en el encabezado.")
            continue
        columnas_anio.append((idx_col, int(m.group(1)), str(titulo)))

    if not columnas_anio:
        raise ValueError(
            "No se detectó ninguna columna de año hidrológico. "
            "Se esperaba un encabezado con 4 dígitos de año (p. ej. '2009-2010')."
        )

    registros = []
    for idx_col, anio_inicio, titulo in columnas_anio:
        for num_fila, fila in enumerate(filas_datos, start=2):
            plantilla = fila[0] if len(fila) > 0 else None
            if plantilla is None:
                continue
            # admite tanto datetime/date de Excel como texto 'dd/mm'
            if hasattr(plantilla, 'day') and hasattr(plantilla, 'month'):
                dia, mes = plantilla.day, plantilla.month
            else:
                avisos.append(f"Fila {num_fila}: plantilla de fecha '{plantilla}' no reconocida, se omite.")
                continue

            anio = anio_inicio if mes >= mes_inicio_anio_hidro else anio_inicio + 1

            try:
                fecha_real = date(anio, mes, dia)
            except ValueError:
                # p. ej. 29-feb en año no bisiesto: se omite el punto puntual,
                # no se detiene el proceso completo
                avisos.append(
                    f"Columna '{titulo}', fila {num_fila}: fecha {dia}/{mes}/{anio} "
                    f"inválida (año no bisiesto u otro) — celda omitida."
                )
                continue

            crudo = fila[idx_col] if idx_col < len(fila) else None
            valor = _parsear_valor_celda(crudo)
            origen = 'medido' if valor is not None else 'faltante'
            registros.append(RegistroCaudal(fecha_real, crudo, valor, origen))

    registros.sort(key=lambda r: r.fecha)
    return registros, avisos


def deduplicar(registros):
    """Elimina fechas duplicadas conservando el primer registro con valor
    numérico (si existe alguno entre los duplicados). Devuelve
    (registros_unicos, lista_de_duplicados_descartados)."""
    vistos = {}
    duplicados = []
    for r in registros:
        if r.fecha not in vistos:
            vistos[r.fecha] = r
        else:
            existente = vistos[r.fecha]
            duplicados.append(r)
            # si el existente no tiene valor y el nuevo sí, nos quedamos con el nuevo
            if existente.valor is None and r.valor is not None:
                vistos[r.fecha] = r
    unicos = sorted(vistos.values(), key=lambda r: r.fecha)
    return unicos, duplicados


def reindexar_diario(registros):
    """Asegura que exista un registro por cada día del calendario entre el
    primer y último dato. Inserta registros 'insertado' (valor None) donde
    falte la fecha completa (no solo el valor). Devuelve
    (registros_completos, fechas_insertadas)."""
    if not registros:
        return registros, []
    presentes = {r.fecha: r for r in registros}
    inicio, fin = registros[0].fecha, registros[-1].fecha
    completos = []
    insertadas = []
    d = inicio
    while d <= fin:
        if d in presentes:
            completos.append(presentes[d])
        else:
            completos.append(RegistroCaudal(d, None, None, origen='insertado'))
            insertadas.append(d)
        d += timedelta(days=1)
    return completos, insertadas


# ─────────────────────────────────────────────────────────────────────────────
# Diagnóstico de calidad (solo reporta, no modifica valores)
# ─────────────────────────────────────────────────────────────────────────────

def detectar_episodios_faltantes(registros):
    """Agrupa valores faltantes (None) en episodios consecutivos.
    Devuelve lista de dicts: {inicio, fin, dias}."""
    episodios = []
    i = 0
    n = len(registros)
    while i < n:
        if registros[i].valor is None:
            j = i
            while j < n and registros[j].valor is None:
                j += 1
            episodios.append({
                'inicio': registros[i].fecha,
                'fin': registros[j - 1].fecha,
                'dias': j - i,
            })
            i = j
        else:
            i += 1
    return episodios


def detectar_negativos_cero(registros):
    return [r for r in registros if r.valor is not None and r.valor <= 0]


def detectar_rachas_planas(registros, min_dias=7):
    """Detecta corridas de valores numéricamente idénticos de longitud >= min_dias.
    Solo considera valores medidos (no rellenados)."""
    rachas = []
    i = 0
    n = len(registros)
    while i < n:
        if registros[i].valor is None:
            i += 1
            continue
        j = i + 1
        while j < n and registros[j].valor is not None and registros[j].valor == registros[i].valor:
            j += 1
        largo = j - i
        if largo >= min_dias:
            rachas.append({
                'inicio': registros[i].fecha,
                'fin': registros[j - 1].fecha,
                'dias': largo,
                'valor': registros[i].valor,
            })
        i = j


    return rachas


def detectar_outliers_log_mensual(registros, z_umbral=4.0):
    """Outliers vía z-score de log(Flow), agrupado por mes calendario
    (caudal se distribuye aprox. lognormal). Requiere valor > 0."""
    por_mes = {}
    for r in registros:
        if r.valor is not None and r.valor > 0:
            por_mes.setdefault(r.fecha.month, []).append(r)

    outliers = []
    for mes, regs in por_mes.items():
        logs = [math.log(r.valor) for r in regs]
        if len(logs) < 5:
            continue
        mu = statistics.mean(logs)
        sigma = statistics.pstdev(logs)
        if sigma == 0:
            continue
        for r, lv in zip(regs, logs):
            z = (lv - mu) / sigma
            if abs(z) > z_umbral:
                outliers.append({'fecha': r.fecha, 'valor': r.valor, 'z': z, 'mes': mes})
    outliers.sort(key=lambda o: o['fecha'])
    return outliers


def detectar_saltos_bruscos(registros, k_sigma=5.0):
    """Detecta cambios día a día |Flow[t] - Flow[t-1]| anormalmente grandes
    respecto a la variabilidad TÍPICA DE ESE MES (media + k*sigma del
    incremento absoluto, agrupado por mes calendario). Complementa al
    z-score mensual: el z-score mensual detecta 'nivel inusual para la
    época', esto detecta 'salto inusualmente rápido para la época' —
    ambos estratificados por mes porque el caudal es fuertemente estacional
    (un umbral global de variación día a día queda dominado por los saltos
    grandes pero legítimos de la temporada de avenidas, igual que le pasaba
    al IQR global para el nivel).
    Solo aplica sobre pares de valores medidos consecutivos (no compara
    contra valores ya rellenados, para no señalar como 'salto' algo que la
    propia interpolación introdujo)."""
    pares = [(r_ant, r) for r_ant, r in zip(registros, registros[1:])
             if r_ant.valor is not None and r.valor is not None
             and r_ant.origen == 'medido' and r.origen == 'medido']
    if len(pares) < 5:
        return []

    por_mes = {}
    for r_ant, r in pares:
        cambio = abs(r.valor - r_ant.valor)
        por_mes.setdefault(r.fecha.month, []).append((r_ant, r, cambio))

    saltos = []
    for mes, items in por_mes.items():
        cambios = [c for _, _, c in items]
        if len(cambios) < 5:
            continue
        media = statistics.mean(cambios)
        sigma = statistics.pstdev(cambios)
        if sigma == 0:
            continue
        limite = media + k_sigma * sigma
        for r_ant, r, c in items:
            if c > limite:
                saltos.append({
                    'fecha': r.fecha, 'fecha_anterior': r_ant.fecha,
                    'valor_anterior': r_ant.valor, 'valor': r.valor,
                    'cambio': c, 'mes': mes,
                })
    saltos.sort(key=lambda s: s['fecha'])
    return saltos


def generar_diagnostico(registros, min_dias_racha=7, z_umbral=4.0, k_sigma_salto=5.0):
    """Corre todas las verificaciones y devuelve un dict consolidado."""
    return {
        'n_total': len(registros),
        'episodios_faltantes': detectar_episodios_faltantes(registros),
        'negativos_cero': detectar_negativos_cero(registros),
        'rachas_planas': detectar_rachas_planas(registros, min_dias_racha),
        'outliers': detectar_outliers_log_mensual(registros, z_umbral),
        'saltos_bruscos': detectar_saltos_bruscos(registros, k_sigma_salto),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Relleno de vacíos
# ─────────────────────────────────────────────────────────────────────────────

def _interp_lineal(x0, y0, x1, y1, x):
    if x1 == x0:
        return y0
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def _interp_loglineal(x0, y0, x1, y1, x):
    """Interpola en log(y); si algún extremo no es positivo, cae a lineal."""
    if y0 <= 0 or y1 <= 0:
        return _interp_lineal(x0, y0, x1, y1, x)
    ly0, ly1 = math.log(y0), math.log(y1)
    ly = _interp_lineal(x0, ly0, x1, ly1, x)
    return math.exp(ly)


def _spline_natural_coefs(xs, ys):
    """Coeficientes de spline cúbico natural (Thomas algorithm), puro Python.
    xs deben ser estrictamente crecientes."""
    n = len(xs)
    h = [xs[i + 1] - xs[i] for i in range(n - 1)]
    alpha = [0.0] * n
    for i in range(1, n - 1):
        alpha[i] = (3.0 / h[i]) * (ys[i + 1] - ys[i]) - (3.0 / h[i - 1]) * (ys[i] - ys[i - 1])

    l = [1.0] * n
    mu = [0.0] * n
    z = [0.0] * n
    for i in range(1, n - 1):
        l[i] = 2.0 * (xs[i + 1] - xs[i - 1]) - h[i - 1] * mu[i - 1]
        mu[i] = h[i] / l[i]
        z[i] = (alpha[i] - h[i - 1] * z[i - 1]) / l[i]

    c = [0.0] * n
    b = [0.0] * n
    d = [0.0] * n
    for j in range(n - 2, -1, -1):
        c[j] = z[j] - mu[j] * c[j + 1]
        b[j] = (ys[j + 1] - ys[j]) / h[j] - h[j] * (c[j + 1] + 2.0 * c[j]) / 3.0
        d[j] = (c[j + 1] - c[j]) / (3.0 * h[j])
    return b, c, d  # a = ys


def _spline_eval(xs, ys, b, c, d, x):
    n = len(xs)
    # localizar intervalo
    i = 0
    for k in range(n - 1):
        if xs[k] <= x <= xs[k + 1]:
            i = k
            break
    else:
        i = n - 2 if x > xs[-1] else 0
    dx = x - xs[i]
    return ys[i] + b[i] * dx + c[i] * dx ** 2 + d[i] * dx ** 3


def rellenar_vacios(registros, metodo='loglineal', umbral_advertencia_dias=10):
    """
    Rellena todos los registros con valor=None interpolando entre el último
    y el próximo valor MEDIDO disponibles (no usa valores ya rellenados como
    ancla, para no propagar error). Devuelve (registros_out, episodios_log)
    donde episodios_log incluye si se emitió advertencia por gap largo.

    metodo: 'loglineal' | 'lineal' | 'spline'
    Para 'spline' se ajusta un spline natural sobre TODOS los puntos medidos
    de la serie y se evalúa en las fechas faltantes (más estable que un
    spline local de 2 puntos).
    """
    if metodo not in METODOS_INTERPOLACION:
        raise ValueError(f"Método de interpolación no soportado: {metodo}")

    out = list(registros)
    medidos_idx = [i for i, r in enumerate(out) if r.valor is not None]
    if not medidos_idx:
        raise ValueError("La serie no tiene ningún valor medido; no se puede interpolar.")

    fecha0 = out[medidos_idx[0]].fecha
    episodios = detectar_episodios_faltantes(out)

    VENTANA_SPLINE = 12  # puntos medidos a cada lado del hueco (ventana local, no la serie completa)
    for ep in episodios:
        i_inicio = next(i for i, r in enumerate(out) if r.fecha == ep['inicio'])
        i_fin = next(i for i, r in enumerate(out) if r.fecha == ep['fin'])

        # buscar ancla previa y siguiente (valores medidos, no rellenados)
        i_prev = i_inicio - 1
        i_sig = i_fin + 1
        tiene_prev = i_prev >= 0 and out[i_prev].valor is not None
        tiene_sig = i_sig < len(out) and out[i_sig].valor is not None

        ep['advertencia'] = ep['dias'] >= umbral_advertencia_dias
        ep['metodo'] = metodo
        ep['rellenado'] = False

        if not tiene_prev and not tiene_sig:
            ep['motivo_no_relleno'] = 'sin datos medidos a ningún lado del gap'
            continue
        if not tiene_prev:
            # extrapolación al inicio de la serie: usa el valor siguiente (constante)
            for k in range(i_inicio, i_fin + 1):
                out[k] = RegistroCaudal(out[k].fecha, out[k].crudo, out[i_sig].valor, origen='rellenado')
            ep['rellenado'] = True
            ep['motivo_no_relleno'] = None
            continue
        if not tiene_sig:
            for k in range(i_inicio, i_fin + 1):
                out[k] = RegistroCaudal(out[k].fecha, out[k].crudo, out[i_prev].valor, origen='rellenado')
            ep['rellenado'] = True
            ep['motivo_no_relleno'] = None
            continue

        x0, y0 = (out[i_prev].fecha - fecha0).days, out[i_prev].valor
        x1, y1 = (out[i_sig].fecha - fecha0).days, out[i_sig].valor

        spline_local = None
        spline_valido = False
        if metodo == 'spline':
            medidos_antes = [j for j in medidos_idx if j < i_inicio][-VENTANA_SPLINE:]
            medidos_despues = [j for j in medidos_idx if j > i_fin][:VENTANA_SPLINE]
            ventana_idx = medidos_antes + medidos_despues
            if len(ventana_idx) >= 3:
                xw = [(out[j].fecha - fecha0).days for j in ventana_idx]
                yw = [out[j].valor for j in ventana_idx]
                b, c, d = _spline_natural_coefs(xw, yw)
                spline_local = (xw, yw, b, c, d)
                # validar TODO el episodio antes de aceptar el spline: si oscila
                # a negativo en cualquier punto del tramo, se descarta el episodio
                # completo (no se mezclan métodos dentro de un mismo hueco)
                valores_prueba = [
                    _spline_eval(xw, yw, b, c, d, (out[k].fecha - fecha0).days)
                    for k in range(i_inicio, i_fin + 1)
                ]
                spline_valido = all(v >= 0 for v in valores_prueba)

        ep['metodo_efectivo'] = metodo if (metodo != 'spline' or spline_valido) else 'loglineal (respaldo: spline inestable en este tramo)'

        for k in range(i_inicio, i_fin + 1):
            x = (out[k].fecha - fecha0).days
            if metodo == 'lineal':
                yk = _interp_lineal(x0, y0, x1, y1, x)
            elif metodo == 'loglineal':
                yk = _interp_loglineal(x0, y0, x1, y1, x)
            else:  # spline
                if spline_local is not None and spline_valido:
                    xw, yw, b, c, d = spline_local
                    yk = _spline_eval(xw, yw, b, c, d, x)
                else:
                    yk = _interp_loglineal(x0, y0, x1, y1, x)
            out[k] = RegistroCaudal(out[k].fecha, out[k].crudo, round(yk, 3), origen='rellenado')
        ep['rellenado'] = True
        ep['motivo_no_relleno'] = None

    return out, episodios


# ─────────────────────────────────────────────────────────────────────────────
# Escritura
# ─────────────────────────────────────────────────────────────────────────────

def escribir_csv(registros, path, formato_fecha='%m/%d/%Y', col_fecha='Date', col_flow='Flow'):
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow([col_fecha, col_flow])
        for r in registros:
            if r.valor is None:
                valor_str = ''
            else:
                valor_str = f"{r.valor:.2f}"
            writer.writerow([r.fecha.strftime(formato_fecha), valor_str])


def escribir_reporte_qc(diagnostico, episodios_relleno, path):
    lineas = []
    lineas.append("REPORTE DE CONTROL DE CALIDAD — CAUDAL OBSERVADO")
    lineas.append("=" * 60)
    lineas.append(f"Registros totales: {diagnostico['n_total']}")
    lineas.append("")
    lineas.append(f"Episodios de valores faltantes: {len(diagnostico['episodios_faltantes'])}")
    for ep in episodios_relleno:
        estado = "RELLENADO" if ep.get('rellenado') else "NO RELLENADO"
        adv = "  [ADVERTENCIA: gap largo]" if ep.get('advertencia') else ""
        lineas.append(f"  {ep['inicio']} -> {ep['fin']}  ({ep['dias']} dias)  "
                       f"metodo={ep.get('metodo_efectivo', ep.get('metodo','-'))}  {estado}{adv}")
        if ep.get('motivo_no_relleno'):
            lineas.append(f"      motivo: {ep['motivo_no_relleno']}")
    lineas.append("")
    lineas.append(f"Valores negativos o cero (no corregidos automaticamente): "
                   f"{len(diagnostico['negativos_cero'])}")
    for r in diagnostico['negativos_cero']:
        lineas.append(f"  {r.fecha}  valor={r.valor}")
    lineas.append("")
    lineas.append(f"Rachas de valor identico >= umbral (posible dato pegado / estiaje real, "
                   f"NO corregidas automaticamente): {len(diagnostico['rachas_planas'])}")
    for r in diagnostico['rachas_planas']:
        lineas.append(f"  {r['inicio']} -> {r['fin']}  ({r['dias']} dias)  valor={r['valor']}")
    lineas.append("")
    lineas.append(f"Outliers estadisticos (z-score log-mensual, NO corregidos automaticamente): "
                   f"{len(diagnostico['outliers'])}")
    for o in diagnostico['outliers']:
        lineas.append(f"  {o['fecha']}  valor={o['valor']}  z={o['z']:.2f}")
    lineas.append("")
    lineas.append(f"Saltos bruscos dia a dia (variacion >> tipica de la serie, "
                   f"NO corregidos automaticamente): {len(diagnostico['saltos_bruscos'])}")
    for s in diagnostico['saltos_bruscos']:
        lineas.append(f"  {s['fecha_anterior']} -> {s['fecha']}  "
                       f"{s['valor_anterior']} -> {s['valor']}  (cambio={s['cambio']:.2f})")

    texto = "\n".join(lineas)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(texto)
    return texto
