# -*- coding: utf-8 -*-
"""
hidrologia_calculo.py — geeSWAT
Motor de cálculo morfométrico e interpretación hidrológica automática.

Este módulo NO depende de QGIS: recibe valores numéricos ya extraídos
(área, perímetro, longitudes, elevaciones, etc.) y devuelve índices +
texto técnico interpretativo. La extracción desde capas/DB de QSWAT vive
en hidrologia_extraccion.py, que sí depende de la API de QGIS.

Se separan cálculo/interpretación de la extracción a propósito:
 - permite testear el motor de reglas sin abrir QGIS
 - permite reutilizar el motor si algún día cambia el origen de datos
   (project.sqlite de QSWAT clásico, QSWAT+, o TxtInOut como respaldo)

Todas las fórmulas usan unidades métricas: área en km², longitudes en km,
elevación en m. Las funciones de más abajo documentan la fuente de cada
umbral; donde la bibliografía difiere entre autores, se indica el rango
usado y su origen para que puedas ajustarlo (ver INTERPRETATION_RULES).
"""

import math
import datetime


# ═══════════════════════════════════════════════════════════════════════════
# ÍNDICES MORFOMÉTRICOS
# ═══════════════════════════════════════════════════════════════════════════

def indice_compacidad_gravelius(area_km2, perimetro_km):
    """Kc = 0.28 * P / sqrt(A)  (Gravelius, 1914)."""
    if area_km2 <= 0:
        return None
    return round(0.28 * perimetro_km / math.sqrt(area_km2), 3)


def factor_forma_horton(area_km2, longitud_axial_km):
    """Kf = A / L^2  (Horton, 1932)."""
    if longitud_axial_km <= 0:
        return None
    return round(area_km2 / (longitud_axial_km ** 2), 3)


def razon_elongacion_schumm(area_km2, longitud_axial_km):
    """Re = 1.128 * sqrt(A) / L  (Schumm, 1956)."""
    if longitud_axial_km <= 0:
        return None
    return round(1.128 * math.sqrt(area_km2) / longitud_axial_km, 3)


def densidad_drenaje(long_total_cauces_km, area_km2):
    """Dd = Σ L_cauces / A  [km/km²] (Horton, 1932)."""
    if area_km2 <= 0:
        return None
    return round(long_total_cauces_km / area_km2, 3)


def ancho_medio(area_km2, longitud_axial_km):
    """Ancho medio Am = A / L."""
    if not longitud_axial_km:
        return None
    return round(area_km2 / longitud_axial_km, 3)


def circularidad_miller(area_km2, perimetro_km):
    """Razón de circularidad Rc = 4*pi*A / P^2  (Miller, 1953). 1 = círculo perfecto."""
    if perimetro_km <= 0:
        return None
    return round(4 * math.pi * area_km2 / (perimetro_km ** 2), 3)


def indice_rugosidad(dd_km_km2, relieve_total_m):
    """Número de rugosidad de Strahler Rn = Dd * H  (H en km, Dd en km/km2 -> adimensional)."""
    if dd_km_km2 is None or relieve_total_m is None:
        return None
    return round(dd_km_km2 * (relieve_total_m / 1000.0), 3)


def indice_melton(relieve_total_m, area_km2):
    """Número de rugosidad de Melton MRN = (Hmax-Hmin) / sqrt(A)  (Melton, 1965;
    H en m, A en km2 — convención de la literatura de peligros por flujos de detritos)."""
    if relieve_total_m is None or area_km2 is None or area_km2 <= 0:
        return None
    return round(relieve_total_m / math.sqrt(area_km2), 3)



    """
    Criterio de Alvord (aprox.) para pendiente media de cuenca a partir
    de curvas de nivel no siempre está disponible; como proxy operativo
    cuando solo hay relieve total y densidad de drenaje, se reporta el
    relieve y la densidad por separado en vez de forzar un valor de
    pendiente poco confiable. Ver hidrologia_extraccion.py para el cálculo
    real de pendiente media por zonal stats sobre el DEM (preferido).
    """
    raise NotImplementedError(
        "Usar pendiente media real desde el DEM (zonal stats). "
        "Esta función queda como placeholder documentado.")


# ═══════════════════════════════════════════════════════════════════════════
# TIEMPO DE CONCENTRACIÓN — múltiples métodos
# ═══════════════════════════════════════════════════════════════════════════

def tc_kirpich(long_cauce_m, pendiente_mm):
    """Kirpich (1940). Tc en minutos. L en metros, S en m/m."""
    if long_cauce_m <= 0 or pendiente_mm <= 0:
        return None
    return round(0.0195 * (long_cauce_m ** 0.77) * (pendiente_mm ** -0.385), 1)


def tc_temez(long_cauce_km, pendiente_mm):
    """Témez (1978, uso extendido en Perú/España). Tc en horas."""
    if long_cauce_km <= 0 or pendiente_mm <= 0:
        return None
    pendiente_pct = pendiente_mm * 100
    return round(0.3 * ((long_cauce_km / (pendiente_pct ** 0.25)) ** 0.76), 2)


def tc_california_culverts(long_cauce_km, desnivel_m):
    """California Culverts Practice. Tc en minutos. Requiere desnivel H (m)."""
    if long_cauce_km <= 0 or desnivel_m <= 0:
        return None
    return round(60 * (0.87 * (long_cauce_km ** 3) / desnivel_m) ** 0.385, 1)


def tc_scs_lag(long_cauce_m, cn, pendiente_pct):
    """
    SCS Lag method. Requiere Curva Número (CN) — usar el raster generado
    en la pestaña 'Curva Número' de geeSWAT si está disponible.
    Tc en horas.
    """
    if long_cauce_m <= 0 or not (1 < cn < 100) or pendiente_pct <= 0:
        return None
    L_ft = long_cauce_m * 3.28084
    lag_hr = (L_ft ** 0.8) * (((1000.0 / cn) - 9) ** 0.7) / (1900 * (pendiente_pct ** 0.5))
    return round(lag_hr / 0.6, 2)


def resumen_tc(long_cauce_km, pendiente_mm, desnivel_m, cn=None, pendiente_pct=None):
    """Corre todos los métodos posibles con los datos disponibles y devuelve
    un diccionario {metodo: minutos} más un rango [min, max]."""
    resultados = {}
    v = tc_kirpich(long_cauce_km * 1000, pendiente_mm)
    if v is not None:
        resultados['Kirpich'] = v
    v = tc_temez(long_cauce_km, pendiente_mm)
    if v is not None:
        resultados['Témez'] = round(v * 60, 1)
    v = tc_california_culverts(long_cauce_km, desnivel_m)
    if v is not None:
        resultados['California Culverts'] = v
    if cn is not None and pendiente_pct is not None:
        v = tc_scs_lag(long_cauce_km * 1000, cn, pendiente_pct)
        if v is not None:
            resultados['SCS Lag'] = round(v * 60, 1)
    if resultados:
        vals = list(resultados.values())
        resultados['_rango_min'] = round(min(vals), 1)
        resultados['_rango_max'] = round(max(vals), 1)
    return resultados


# ═══════════════════════════════════════════════════════════════════════════
# RED DE DRENAJE POR ORDEN DE STRAHLER
# ═══════════════════════════════════════════════════════════════════════════

def frecuencia_corrientes(n_tramos_total, area_km2):
    """Fs = N / A  (Horton, 1932)."""
    if area_km2 <= 0:
        return None
    return round(n_tramos_total / area_km2, 3)


def razon_bifurcacion_media(conteo_por_orden):
    """
    conteo_por_orden: dict {orden: n_tramos}, ordenado de menor a mayor orden.
    Rb_i = N_u / N_(u+1); se promedia entre todos los pares consecutivos
    (Strahler, 1957 recomienda el promedio en vez de un solo par).
    """
    ordenes = sorted(conteo_por_orden.keys())
    razones = []
    for a, b in zip(ordenes, ordenes[1:]):
        nb = conteo_por_orden.get(b, 0)
        if nb > 0:
            razones.append(conteo_por_orden[a] / nb)
    if not razones:
        return None
    return round(sum(razones) / len(razones), 3)


def textura_drenaje(n_tramos_total, perimetro_km):
    """Dt = N / P  (Horton, 1945)."""
    if perimetro_km <= 0:
        return None
    return round(n_tramos_total / perimetro_km, 3)


def sinuosidad(long_real_km, long_linea_recta_km):
    """Sin = longitud real del cauce / distancia recta entre sus extremos."""
    if not long_linea_recta_km:
        return None
    return round(long_real_km / long_linea_recta_km, 3)




def curva_hipsometrica(pares_area_elevacion):
    """
    pares_area_elevacion: lista de tuplas (elevacion_m, area_acumulada_m2)
    ordenada de mayor a menor elevación, tal como se obtiene de un
    histograma del DEM recortado a la cuenca.

    Devuelve:
      - puntos normalizados (x=% área acumulada, y=% elevación relativa)
      - HI: integral hipsométrica (Strahler, 1952) ≈ (Emedia - Emin)/(Emax-Emin)
      - clasificación geomorfológica
    """
    if not pares_area_elevacion or len(pares_area_elevacion) < 3:
        return None

    elevs = [p[0] for p in pares_area_elevacion]
    e_max, e_min = max(elevs), min(elevs)
    if e_max == e_min:
        return None

    area_total = pares_area_elevacion[-1][1]
    if area_total <= 0:
        return None

    puntos = []
    for elev, area_acum in pares_area_elevacion:
        x = 100.0 * area_acum / area_total
        y = 100.0 * (elev - e_min) / (e_max - e_min)
        puntos.append((round(x, 2), round(y, 2)))

    e_media = sum(e for e, _ in pares_area_elevacion) / len(pares_area_elevacion)
    hi = (e_media - e_min) / (e_max - e_min)

    if hi > 0.60:
        etapa = 'juvenil'
    elif hi >= 0.35:
        etapa = 'madura (equilibrio)'
    else:
        etapa = 'senil'

    return {
        'puntos': puntos,
        'HI': round(hi, 3),
        'etapa': etapa,
        'e_min': e_min,
        'e_max': e_max,
        'e_media': round(e_media, 1),
        'n_muestras': len(pares_area_elevacion),
    }


# ═══════════════════════════════════════════════════════════════════════════
# MOTOR DE INTERPRETACIÓN — reglas documentadas y editables
# ═══════════════════════════════════════════════════════════════════════════
# Cada regla es (umbral_superior, etiqueta, texto, fuente). Se recorren en
# orden ascendente y se toma la primera cuyo umbral no se supere.
# Ajusta estos rangos si tu criterio regional difiere.

INTERPRETATION_RULES = {
    'Kc': [
        (1.25, 'Clase I — casi redonda a oval-redonda',
         'La cuenca tiende a la forma circular. Los tiempos de concentración '
         'de las distintas subcuencas tributarias tienden a coincidir, lo '
         'que favorece picos de crecida más altos y concentrados en el tiempo.',
         'Gravelius (1914); clasificación de clases usada en Villón Béjar, '
         '"Hidrología" (Perú/Centroamérica)'),
        (1.50, 'Clase II — oval-redonda a oval-oblonga',
         'Forma intermedia. Respuesta hidrológica moderadamente concentrada, '
         'sin la severidad de una cuenca circular ni la atenuación de una alargada.',
         'Gravelius (1914); Villón Béjar'),
        (float('inf'), 'Clase III — oval-oblonga a rectangular-alargada',
         'Cuenca alargada. El hidrograma de salida tiende a ser más atenuado '
         'y prolongado, con picos de crecida menores para una misma tormenta, '
         'porque el agua de las partes altas tarda más en converger con la de '
         'las partes bajas.',
         'Gravelius (1914); Villón Béjar'),
    ],
    'Dd': [
        (0.5, 'Baja',
         'Densidad de drenaje baja: pocos cauces por unidad de área. Suele '
         'asociarse a sustratos muy permeables (alta infiltración) o a suelos '
         'resistentes a la erosión, con respuesta hidrológica lenta y menor '
         'escorrentía directa relativa. No confundir con "alta infiltración de '
         'suelo": Dd refleja la red de cauces, la infiltración real depende del '
         'tipo/uso de suelo (ver HRUs).',
         'Horton (1932); rangos de uso común en hidrología aplicada andina'),
        (1.5, 'Moderada',
         'Densidad de drenaje moderada, típica de cuencas con litología mixta '
         'y cobertura vegetal parcial.',
         'Horton (1932)'),
        (float('inf'), 'Alta',
         'Densidad de drenaje alta: red de cauces muy ramificada. Asociada a '
         'sustratos poco permeables o suelos erosionables, con respuesta '
         'hidrológica rápida (picos de crecida más súbitos) y mayor producción '
         'de sedimentos si además hay pendientes fuertes.',
         'Horton (1932)'),
    ],
    'HI': [
        (0.35, 'Senil',
         'Curva hipsométrica cóncava. Cuenca en etapa geomorfológica avanzada: '
         'relieve suavizado, predominio de procesos de depositación sobre '
         'erosión, baja energía del relieve.',
         'Strahler (1952)'),
        (0.60, 'Madura (equilibrio)',
         'Curva en forma de "S". Balance entre procesos erosivos y de '
         'depositación; la cuenca está en una etapa intermedia de evolución.',
         'Strahler (1952)'),
        (float('inf'), 'Juvenil',
         'Curva convexa, integral hipsométrica alta. Cuenca en etapa temprana: '
         'predominan los procesos erosivos activos sobre los de depositación, '
         'con mayor potencial de producción de sedimentos.',
         'Strahler (1952)'),
    ],
    'Melton': [
        (0.30, 'Fluvial — bajo peligro de flujo de detritos',
         'Relieve moderado respecto al área de la cuenca. Predominan procesos '
         'fluviales normales; baja propensión a flujos de detritos/huaicos.',
         'Melton (1965); umbrales de Marchi & Fontana (2005)'),
        (0.60, 'Transicional',
         'Zona de transición entre respuesta fluvial y torrencial. Bajo '
         'lluvias intensas puede haber generación de flujos hiperconcentrados.',
         'Marchi & Fontana (2005)'),
        (float('inf'), 'Torrencial — alto peligro de flujo de detritos',
         'Relieve muy alto respecto al área: cuenca típica de generación de '
         'huaicos/flujos de detritos (debris flows) en eventos de lluvia '
         'intensa. Relevante para ordenamiento territorial y gestión de '
         'riesgos en cabeceras de cuenca andina.',
         'Melton (1965); Marchi & Fontana (2005)'),
    ],
    'Rb': [
        (3.0, 'Baja — posible control estructural/topografía plana',
         'Razón de bifurcación menor a lo típico de redes naturales; puede '
         'indicar control estructural/litológico fuerte o relieve muy plano.',
         'Strahler (1957); rango natural típico 3-5'),
        (5.0, 'Típica de red de drenaje natural',
         'Dentro del rango típico de cuencas con litología relativamente '
         'homogénea y sin fuerte control estructural.',
         'Strahler (1957)'),
        (float('inf'), 'Alta — fuerte control estructural/litológico',
         'Razón de bifurcación por encima de lo típico; sugiere fuerte '
         'control geológico (fallas, plegamiento) sobre el trazado de la red.',
         'Strahler (1957)'),
    ],
    'Dt': [
        (2.0, 'Gruesa',
         'Textura de drenaje gruesa: pocos cauces por unidad de perímetro. '
         'Asociada a sustratos permeables o relieve suave.',
         'Horton (1945)'),
        (4.0, 'Media',
         'Textura de drenaje media.',
         'Horton (1945)'),
        (float('inf'), 'Fina',
         'Textura de drenaje fina: red muy ramificada respecto al perímetro. '
         'Asociada a sustratos poco permeables o alta erosionabilidad.',
         'Horton (1945)'),
    ],
}


def _clasificar(valor, reglas):
    for umbral, etiqueta, texto, fuente in reglas:
        if valor <= umbral:
            return {'valor': valor, 'clase': etiqueta, 'texto': texto, 'fuente': fuente}
    return None


def interpretar_forma(kc, kf=None, re=None):
    r = _clasificar(kc, INTERPRETATION_RULES['Kc'])
    if r is None:
        return None
    extra = []
    if kf is not None:
        extra.append(f'Factor de forma (Horton) Kf = {kf}.')
    if re is not None:
        extra.append(f'Razón de elongación (Schumm) Re = {re}.')
    r['detalle_numerico'] = ' '.join(extra)
    return r


def interpretar_densidad_drenaje(dd):
    return _clasificar(dd, INTERPRETATION_RULES['Dd'])


def interpretar_hipsometria(resultado_curva, min_muestras_confiables=15):
    if resultado_curva is None:
        return None
    r = _clasificar(resultado_curva['HI'], INTERPRETATION_RULES['HI'])
    if r is None:
        return None
    r['n_muestras'] = resultado_curva['n_muestras']
    r['confiable'] = resultado_curva['n_muestras'] >= min_muestras_confiables
    if not r['confiable']:
        r['advertencia'] = (
            f"Solo se usaron {resultado_curva['n_muestras']} clases de elevación "
            f"para construir la curva. Con tan pocas muestras el valor de HI "
            f"({resultado_curva['HI']}) es poco confiable — la clasificación "
            f"'{r['clase']}' debe tomarse como orientativa, no concluyente. "
            f"Se recomienda recortar el DEM completo a la cuenca (no solo al "
            f"contorno de subcuencas) para tener más clases de elevación."
        )
    return r


def interpretar_relieve_pendiente(pendiente_media_pct):
    """Clasificación de pendiente para potencial erosivo, escala referencial
    (tabla de clases de pendiente para fines de erosión, uso extendido en
    estudios de ordenamiento territorial en Perú — Reglamento de Clasificación
    de Tierras / FAO)."""
    if pendiente_media_pct is None:
        return None
    tabla = [
        (4, 'Plana a suave', 'Escorrentía lenta, bajo riesgo erosivo por sí sola.'),
        (8, 'Suave a moderada', 'Riesgo erosivo bajo a moderado, sensible a pérdida de cobertura.'),
        (15, 'Moderada a fuerte', 'Riesgo erosivo moderado; requiere prácticas de conservación en suelos desnudos o cultivados.'),
        (25, 'Fuerte', 'Riesgo erosivo alto; el transporte de sedimentos hacia los cauces puede ser significativo en eventos intensos.'),
        (50, 'Muy fuerte a escarpada', 'Riesgo erosivo muy alto, típico de zonas de cabecera de cuenca andina; alta producción de sedimentos.'),
        (float('inf'), 'Extremadamente escarpada', 'Zonas de muy alta energía de relieve; erosión y movimientos en masa son procesos dominantes.'),
    ]
    for umbral, clase, texto in tabla:
        if pendiente_media_pct <= umbral:
            return {'valor': pendiente_media_pct, 'clase': clase, 'texto': texto,
                    'fuente': 'Clases de pendiente de uso común en estudios de ordenamiento territorial (FAO / normativa peruana)'}
    return None


def interpretar_melton(mrn):
    if mrn is None:
        return None
    return _clasificar(mrn, INTERPRETATION_RULES['Melton'])


def interpretar_bifurcacion(rb):
    if rb is None:
        return None
    return _clasificar(rb, INTERPRETATION_RULES['Rb'])


def interpretar_textura(dt):
    if dt is None:
        return None
    return _clasificar(dt, INTERPRETATION_RULES['Dt'])


def _fila(nombre, formula, valor, unidad, interpretacion='', fuente=''):
    val_str = '—' if valor is None else str(valor)
    return (f'<tr><td class="p">{nombre}</td><td class="f">{formula}</td>'
            f'<td class="v">{val_str}</td><td class="u">{unidad}</td>'
            f'<td class="i">{interpretacion}'
            + (f'<div class="fuente">Fuente: {fuente}</div>' if fuente else '')
            + '</td></tr>')


def _svg_curva_hipsometrica(puntos, ancho=520, alto=340):
    if not puntos:
        return ''
    pad = 45
    w, h = ancho - 2 * pad, alto - 2 * pad
    pts_svg = []
    for x, y in puntos:
        px = pad + (x / 100.0) * w
        py = pad + (1 - y / 100.0) * h
        pts_svg.append(f'{px:.1f},{py:.1f}')
    polyline = ' '.join(pts_svg)
    ejes = (
        f'<line x1="{pad}" y1="{pad}" x2="{pad}" y2="{pad+h}" stroke="#607D8B" stroke-width="1.5"/>'
        f'<line x1="{pad}" y1="{pad+h}" x2="{pad+w}" y2="{pad+h}" stroke="#607D8B" stroke-width="1.5"/>'
    )
    etiquetas = (
        f'<text x="{pad+w/2}" y="{alto-8}" text-anchor="middle" font-size="12" fill="#37474F">'
        f'% Área acumulada (relativa a la altitud)</text>'
        f'<text x="14" y="{pad+h/2}" text-anchor="middle" font-size="12" fill="#37474F" '
        f'transform="rotate(-90 14 {pad+h/2})">% Elevación relativa</text>'
    )
    marcas = ''
    for pct in (0, 25, 50, 75, 100):
        gx = pad + (pct / 100.0) * w
        gy = pad + (1 - pct / 100.0) * h
        marcas += (f'<line x1="{pad}" y1="{gy:.1f}" x2="{pad+w}" y2="{gy:.1f}" '
                   f'stroke="#ECEFF1" stroke-width="1"/>')
        marcas += (f'<text x="{pad-8}" y="{gy+4:.1f}" text-anchor="end" font-size="10" '
                   f'fill="#78909C">{pct}</text>')
        marcas += (f'<text x="{gx:.1f}" y="{pad+h+16}" text-anchor="middle" font-size="10" '
                   f'fill="#78909C">{pct}</text>')
    return (f'<svg viewBox="0 0 {ancho} {alto}" xmlns="http://www.w3.org/2000/svg">'
            f'{marcas}{ejes}'
            f'<polyline points="{polyline}" fill="none" stroke="#0D47A1" stroke-width="2.5"/>'
            f'{etiquetas}</svg>')


def construir_informe_html(datos, faltantes, logo_base64=None):
    """Genera el informe completo (Grupos 3.1-3.4 + curva hipsométrica) como
    HTML autocontenido, con la fórmula de cada parámetro junto a su valor."""
    nombre = datos.get('nombre', 'sin nombre')
    filas_31, filas_32, filas_33, filas_34 = [], [], [], []

    # 3.1 Geometría
    filas_31.append(_fila('Área', 'A (medida sobre el polígono)',
                           datos.get('area_km2'), 'km²'))
    filas_31.append(_fila('Perímetro', 'P (medido sobre el contorno)',
                           datos.get('perimetro_km'), 'km'))
    filas_31.append(_fila('Longitud', 'L = eje mayor del rectángulo mínimo envolvente',
                           datos.get('longitud_axial_km'), 'km'))
    filas_31.append(_fila('Ancho medio', 'Am = A / L',
                           datos.get('ancho_medio_km'), 'km'))

    # 3.2 Forma
    f = datos.get('forma')
    if f:
        filas_32.append(_fila('Índice de compacidad (Gravelius)',
                               'Kc = 0.28 · P / √A', f['valor'], '—',
                               f"{f['clase']}: {f['texto']}", f['fuente']))
    if datos.get('kf') is not None:
        filas_32.append(_fila('Factor de forma (Horton)', 'Kf = A / L²',
                               datos['kf'], '—',
                               'Kf alto (→1) implica cuenca ancha, con mayor probabilidad de que '
                               'las crecidas de distintos tributarios coincidan en el tiempo; '
                               'Kf bajo implica cuenca alargada, hidrograma más atenuado.',
                               'Horton (1932)'))
    if datos.get('re') is not None:
        filas_32.append(_fila('Razón de elongación (Schumm)', 'Re = 1.128 · √A / L',
                               datos['re'], '—',
                               'Re cercano a 1: cuenca circular y de bajo relieve. Re bajo: '
                               'cuenca alargada y de alto relieve.', 'Schumm (1956)'))
    if datos.get('circularidad') is not None:
        filas_32.append(_fila('Circularidad (Miller)', 'Rc = 4π·A / P²',
                               datos['circularidad'], '—',
                               'Rc = 1 sería un círculo perfecto; valores bajos indican forma '
                               'alargada/irregular.', 'Miller (1953)'))

    # 3.3 Relieve
    filas_33.append(_fila('Elevación mínima', 'Emin (del DEM recortado a la cuenca)',
                           datos.get('e_min'), 'm'))
    filas_33.append(_fila('Elevación máxima', 'Emax (del DEM recortado a la cuenca)',
                           datos.get('e_max'), 'm'))
    filas_33.append(_fila('Elevación media', 'Emedia (del DEM recortado a la cuenca)',
                           datos.get('e_media'), 'm'))
    p = datos.get('relieve_interp')
    filas_33.append(_fila('Pendiente media', 'promedio zonal de %pendiente del DEM',
                           p['valor'] if p else None, '%',
                           f"{p['clase']}: {p['texto']}" if p else '',
                           p['fuente'] if p else ''))
    filas_33.append(_fila('Relieve total', 'H = Emax − Emin',
                           datos.get('relieve_total_m'), 'm'))
    filas_33.append(_fila('Índice de rugosidad (Strahler)', 'Rn = Dd · H(km)',
                           datos.get('rugosidad'), '—',
                           'Combina densidad de drenaje y relieve: valores altos indican '
                           'terreno escarpado y muy disectado por cauces.', 'Strahler (1958)'))
    m = datos.get('melton_interp')
    filas_33.append(_fila('Índice de Melton', 'MRN = H / √A (H en m, A en km²)',
                           m['valor'] if m else None, '—',
                           f"{m['clase']}: {m['texto']}" if m else '',
                           m['fuente'] if m else ''))

    # 3.4 Red de drenaje
    red = datos.get('_red')
    filas_34.append(_fila('Longitud total de cauces', 'ΣL (suma de todos los tramos)',
                           red['long_total_km'] if red else None, 'km'))
    orden = datos.get('_orden')
    if orden:
        lp = ', '.join(f"orden {o}: {l:.2f} km" for o, l in sorted(orden['long_por_orden'].items()))
        filas_34.append(_fila('Longitud por orden', 'ΣL por cada orden de Strahler',
                               lp, '—'))
    else:
        filas_34.append(_fila('Longitud por orden', 'requiere campo de orden de Strahler',
                               None, '—', 'No calculado: no se indicó campo de orden de Strahler.'))
    dd = datos.get('dd_interp')
    filas_34.append(_fila('Densidad de drenaje', 'Dd = ΣL / A',
                           dd['valor'] if dd else None, 'km/km²',
                           f"{dd['clase']}: {dd['texto']}" if dd else '',
                           dd['fuente'] if dd else ''))
    filas_34.append(_fila('Frecuencia de corrientes', 'Fs = N / A',
                           datos.get('frecuencia'), 'tramos/km²'))
    if orden:
        rb_i = orden.get('rb_interp')
        filas_34.append(_fila('Razón de bifurcación', 'Rb = promedio(Nu / N(u+1))',
                               rb_i['valor'] if rb_i else None, '—',
                               f"{rb_i['clase']}: {rb_i['texto']}" if rb_i else '',
                               rb_i['fuente'] if rb_i else ''))
        dt_i = orden.get('dt_interp')
        filas_34.append(_fila('Textura de drenaje', 'Dt = N / P',
                               dt_i['valor'] if dt_i else None, '—',
                               f"{dt_i['clase']}: {dt_i['texto']}" if dt_i else '',
                               dt_i['fuente'] if dt_i else ''))
        filas_34.append(_fila('Orden máximo', 'máximo valor del campo de orden de Strahler',
                               orden.get('orden_max'), '—'))
        filas_34.append(_fila('Sinuosidad', 'Sin = long. real cauce principal / distancia recta extremos',
                               orden.get('sinuosidad'), '—',
                               'Sin ≈ 1: cauce recto. Sin > 1.5: cauce muy sinuoso '
                               '(mayor tiempo de tránsito, mayor interacción con la llanura).'))
    else:
        for etiqueta in ('Razón de bifurcación', 'Textura de drenaje', 'Orden máximo', 'Sinuosidad'):
            filas_34.append(_fila(etiqueta, 'requiere campo de orden de Strahler', None, '—',
                                   'No calculado: no se indicó campo de orden de Strahler en la '
                                   'capa de red de drenaje.'))

    # Tiempo de concentración
    tc_html = ''
    if datos.get('tc'):
        tc = datos['tc']
        filas_tc = ''.join(f'<li>{met}: <b>{v} min</b></li>' for met, v in tc.items() if not met.startswith('_'))
        rango = ''
        if '_rango_min' in tc:
            rango = (f"<p>Rango entre métodos: <b>{tc['_rango_min']} – {tc['_rango_max']} min</b>. "
                      f"Cada método fue calibrado con cuencas distintas a la tuya; el rango, no un "
                      f"único valor, es el resultado defendible.</p>")
        tc_html = f'<ul>{filas_tc}</ul>{rango}'

    # Curva hipsométrica
    curva = datos.get('_curva')
    hi = datos.get('hipso_interp')
    svg = _svg_curva_hipsometrica(curva['puntos']) if curva else ''
    hip_texto = ''
    if hi:
        hip_texto = (f"<p><b>Integral hipsométrica HI = {hi['valor']}</b> → etapa "
                     f"<b>{hi['clase']}</b><br>{hi['texto']}<br>"
                     f"<span class='fuente'>Fuente: {hi['fuente']}</span></p>")
        if not hi.get('confiable', True):
            hip_texto += f"<p class='advertencia'>⚠ {hi['advertencia']}</p>"

    faltantes_html = ''
    if faltantes:
        faltantes_html = ('<div class="limitaciones"><h2>Limitaciones de este análisis</h2><ul>'
                           + ''.join(f'<li>{x}</li>' for x in faltantes) + '</ul></div>')

    fecha_str = datetime.datetime.now().strftime('%d/%m/%Y %H:%M')
    logo_html = (f'<img src="data:image/jpeg;base64,{logo_base64}" alt="Geomatica Ambiental" '
                 f'width="138" height="46" style="background-color:#FFFFFF;padding:5px 8px;'
                 f'border-radius:5px;">' if logo_base64 else '')

    html = f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8">
<title>Informe Hidrológico — {nombre} — geeSWAT</title>
<style>
  body {{ font-family: 'Segoe UI', Arial, sans-serif; margin: 0; background:#F5F7FA; color:#263238; }}
  main {{ max-width: 1000px; margin: 0 auto; padding: 24px 32px 40px; }}
  section {{ background:white; border-radius:10px; padding:20px 24px; margin-bottom:22px;
             box-shadow:0 1px 3px rgba(0,0,0,.08); }}
  h2 {{ color:#0D47A1; border-bottom:2px solid #E3F2FD; padding-bottom:8px; font-size:17px; }}
  table.datos {{ width:100%; border-collapse:collapse; font-size:13px; margin-top:10px;}}
  table.datos th {{ text-align:left; background:#ECEFF1; padding:8px 10px; font-size:11px;
        text-transform:uppercase; letter-spacing:.03em; color:#455A64; }}
  table.datos td {{ padding:9px 10px; border-bottom:1px solid #ECEFF1; vertical-align:top; }}
  td.p {{ font-weight:600; width:19%; }}
  td.f {{ font-family: Consolas, monospace; color:#37474F; width:20%; font-size:12px; }}
  td.v {{ font-weight:600; color:#0D47A1; width:9%; }}
  td.u {{ color:#78909C; width:8%; }}
  .fuente {{ color:#90A4AE; font-size:11px; margin-top:3px; }}
  .advertencia {{ background:#FFF8E1; border-left:4px solid #F9A825; padding:10px 14px; border-radius:4px; }}
  .limitaciones {{ background:#FFF3E0; }}
  .limitaciones h2 {{ color:#E65100; border-color:#FFE0B2; }}
  svg {{ width:100%; max-width:560px; display:block; margin:10px auto; }}
</style></head>
<body>
<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;background-color:#0D47A1;">
<tr>
  <td bgcolor="#0D47A1" style="background-color:#0D47A1;padding:18px 32px;">
    <h1 style="margin:0;font-size:21px;color:#FFFFFF;">Informe de Interpretación Hidrológica</h1>
    <p style="margin:6px 0 0;font-size:13px;color:#FFFFFF;">Cuenca / subcuenca:
      <b style="color:#FFFFFF;">{nombre}</b> — generado con geeSWAT · {fecha_str}</p>
  </td>
  <td bgcolor="#0D47A1" style="background-color:#0D47A1;padding:18px 32px;text-align:right;width:170px;">
    {logo_html}
  </td>
</tr>
</table>
<main>
<section><h2>3.1 Geometría</h2><table class="datos">
<tr><th>Parámetro</th><th>Fórmula</th><th>Valor</th><th>Unidad</th><th>Interpretación</th></tr>
{''.join(filas_31)}
</table></section>

<section><h2>3.2 Forma</h2><table class="datos">
<tr><th>Parámetro</th><th>Fórmula</th><th>Valor</th><th>Unidad</th><th>Interpretación</th></tr>
{''.join(filas_32)}
</table></section>

<section><h2>3.3 Relieve</h2><table class="datos">
<tr><th>Parámetro</th><th>Fórmula</th><th>Valor</th><th>Unidad</th><th>Interpretación</th></tr>
{''.join(filas_33)}
</table></section>

<section><h2>3.4 Red de drenaje</h2><table class="datos">
<tr><th>Parámetro</th><th>Fórmula</th><th>Valor</th><th>Unidad</th><th>Interpretación</th></tr>
{''.join(filas_34)}
</table></section>

<section><h2>Tiempo de concentración</h2>{tc_html or '<p>No calculado.</p>'}</section>

<section><h2>Curva hipsométrica</h2>{svg}{hip_texto}</section>

{faltantes_html}
</main>
<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;background-color:#0A2F6E;margin-top:8px;">
<tr>
  <td bgcolor="#0A2F6E" style="background-color:#0A2F6E;padding:18px 32px;font-weight:bold;color:#FFFFFF;font-size:14px;">
    Geomatica Ambiental
  </td>
  <td bgcolor="#0A2F6E" style="background-color:#0A2F6E;padding:18px 32px;text-align:right;font-size:12px;line-height:1.7;color:#FFFFFF;">
    <a href="https://www.geomatica.pe/" target="_blank" style="color:#90CAF9;">www.geomatica.pe</a><br>
    <span style="color:#FFFFFF;">nino@geomatica.pe &nbsp;·&nbsp; +51 995 664 488</span>
  </td>
</tr>
</table>
</body></html>"""
    return html


def construir_tabla_csv_multicuencas(lista_datos):
    """CSV ancho: una fila por subcuenca, columnas = parámetros. Formato
    pensado para comparar subcuencas lado a lado en Excel."""
    import csv
    import io

    columnas = [
        'Subcuenca', 'Área_km2', 'Perímetro_km', 'Longitud_km', 'Ancho_medio_km',
        'Kc_Gravelius', 'Clase_forma', 'Kf_Horton', 'Re_Schumm', 'Circularidad_Miller',
        'Emin_m', 'Emax_m', 'Emedia_m', 'Pendiente_media_pct', 'Relieve_total_m',
        'Rugosidad_Strahler', 'Melton', 'Clase_Melton',
        'Long_total_red_km', 'Densidad_drenaje', 'Clase_Dd', 'Frecuencia_corrientes',
        'Orden_max', 'Razon_bifurcacion', 'Textura_drenaje', 'Sinuosidad',
        'TC_min_min', 'TC_max_min', 'HI_hipsometrico', 'Etapa_hipsometrica',
    ]
    filas = [columnas]
    for datos in lista_datos:
        f = datos.get('forma') or {}
        p = datos.get('relieve_interp') or {}
        m = datos.get('melton_interp') or {}
        dd = datos.get('dd_interp') or {}
        orden = datos.get('_orden') or {}
        rb_i = orden.get('rb_interp') or {}
        dt_i = orden.get('dt_interp') or {}
        tc = datos.get('tc') or {}
        hi = datos.get('hipso_interp') or {}
        filas.append([
            datos.get('nombre'), datos.get('area_km2'), datos.get('perimetro_km'),
            datos.get('longitud_axial_km'), datos.get('ancho_medio_km'),
            f.get('valor'), f.get('clase'), datos.get('kf'), datos.get('re'),
            datos.get('circularidad'),
            datos.get('e_min'), datos.get('e_max'), datos.get('e_media'),
            p.get('valor'), datos.get('relieve_total_m'),
            datos.get('rugosidad'), m.get('valor'), m.get('clase'),
            (datos.get('_red') or {}).get('long_total_km'), dd.get('valor'), dd.get('clase'),
            datos.get('frecuencia'),
            orden.get('orden_max'), rb_i.get('valor'), dt_i.get('valor'), orden.get('sinuosidad'),
            tc.get('_rango_min'), tc.get('_rango_max'),
            hi.get('valor'), hi.get('clase'),
        ])
    buf = io.StringIO()
    csv.writer(buf).writerows(filas)
    return buf.getvalue()


def construir_resumen_multicuencas_html(lista_datos, lista_faltantes, logo_base64=None):
    """Tabla comparativa HTML — una fila por subcuenca — con los índices
    principales de cada grupo. Para el detalle completo (fórmulas incluidas)
    de cada subcuenca, ver su informe individual en la subcarpeta respectiva."""
    fecha_str = datetime.datetime.now().strftime('%d/%m/%Y %H:%M')
    logo_html = (f'<img src="data:image/jpeg;base64,{logo_base64}" alt="Geomatica Ambiental" '
                 f'width="138" height="46" style="background-color:#FFFFFF;padding:5px 8px;'
                 f'border-radius:5px;">' if logo_base64 else '')

    filas = []
    for datos in lista_datos:
        f = datos.get('forma') or {}
        p = datos.get('relieve_interp') or {}
        m = datos.get('melton_interp') or {}
        dd = datos.get('dd_interp') or {}
        tc = datos.get('tc') or {}
        hi = datos.get('hipso_interp') or {}
        filas.append(
            f'<tr>'
            f'<td class="nom">{datos.get("nombre")}</td>'
            f'<td>{datos.get("area_km2", "—")}</td>'
            f'<td>{f.get("valor", "—")}<div class="clase">{f.get("clase","")}</div></td>'
            f'<td>{dd.get("valor", "—")}<div class="clase">{dd.get("clase","")}</div></td>'
            f'<td>{p.get("valor", "—")}<div class="clase">{p.get("clase","")}</div></td>'
            f'<td>{m.get("valor", "—")}<div class="clase">{m.get("clase","")}</div></td>'
            f'<td>{tc.get("_rango_min","—")}–{tc.get("_rango_max","—")}</td>'
            f'<td>{hi.get("valor", "—")}<div class="clase">{hi.get("clase","")}</div></td>'
            f'</tr>'
        )

    faltantes_html = ''
    if any(lista_faltantes):
        items = ''
        for datos, falt in zip(lista_datos, lista_faltantes):
            if falt:
                items += f'<li><b>{datos.get("nombre")}</b>: {"; ".join(falt)}</li>'
        if items:
            faltantes_html = (f'<div class="limitaciones"><h2>Limitaciones por subcuenca</h2>'
                               f'<ul>{items}</ul></div>')

    html = f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8">
<title>Resumen morfométrico de subcuencas — geeSWAT</title>
<style>
  body {{ font-family: 'Segoe UI', Arial, sans-serif; margin: 0; background:#F5F7FA; color:#263238; }}
  main {{ max-width: 1150px; margin: 0 auto; padding: 24px 32px 40px; }}
  section {{ background:white; border-radius:10px; padding:20px 24px; margin-bottom:22px;
             box-shadow:0 1px 3px rgba(0,0,0,.08); }}
  h2 {{ color:#0D47A1; border-bottom:2px solid #E3F2FD; padding-bottom:8px; font-size:17px; }}
  table.resumen {{ width:100%; border-collapse:collapse; font-size:12.5px; margin-top:10px;}}
  table.resumen th {{ text-align:left; background:#ECEFF1; padding:8px 9px; font-size:10.5px;
        text-transform:uppercase; letter-spacing:.02em; color:#455A64; }}
  table.resumen td {{ padding:8px 9px; border-bottom:1px solid #ECEFF1; vertical-align:top; }}
  table.resumen td.nom {{ font-weight:600; color:#0D47A1; }}
  table.resumen .clase {{ color:#78909C; font-size:10.5px; margin-top:2px; }}
  .limitaciones {{ background:#FFF3E0; }}
  .limitaciones h2 {{ color:#E65100; border-color:#FFE0B2; }}
  .nota {{ color:#78909C; font-size:11px; margin-top:8px; }}
</style></head>
<body>
<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;background-color:#0D47A1;">
<tr>
  <td bgcolor="#0D47A1" style="background-color:#0D47A1;padding:18px 32px;">
    <h1 style="margin:0;font-size:21px;color:#FFFFFF;">Resumen Morfométrico — Todas las Subcuencas</h1>
    <p style="margin:6px 0 0;font-size:13px;color:#FFFFFF;">{len(lista_datos)} subcuencas analizadas —
      generado con geeSWAT · {fecha_str}</p>
  </td>
  <td bgcolor="#0D47A1" style="background-color:#0D47A1;padding:18px 32px;text-align:right;width:170px;">
    {logo_html}
  </td>
</tr>
</table>
<main>
<section>
<h2>Comparación entre subcuencas</h2>
<table class="resumen">
<tr><th>Subcuenca</th><th>Área (km²)</th><th>Forma (Kc)</th><th>Densidad drenaje</th>
    <th>Pendiente media</th><th>Melton</th><th>TC (min)</th><th>HI hipsométrico</th></tr>
{''.join(filas)}
</table>
<p class="nota">El detalle completo de cada subcuenca (todas las fórmulas, grupos 3.1-3.4 y curva
hipsométrica) está en su informe individual, guardado en la subcarpeta con su nombre.</p>
</section>
{faltantes_html}
</main>
<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;background-color:#0A2F6E;margin-top:8px;">
<tr>
  <td bgcolor="#0A2F6E" style="background-color:#0A2F6E;padding:18px 32px;font-weight:bold;color:#FFFFFF;font-size:14px;">
    Geomatica Ambiental
  </td>
  <td bgcolor="#0A2F6E" style="background-color:#0A2F6E;padding:18px 32px;text-align:right;font-size:12px;line-height:1.7;color:#FFFFFF;">
    <a href="https://www.geomatica.pe/" target="_blank" style="color:#90CAF9;">www.geomatica.pe</a><br>
    <span style="color:#FFFFFF;">nino@geomatica.pe &nbsp;·&nbsp; +51 995 664 488</span>
  </td>
</tr>
</table>
</body></html>"""
    return html


def construir_tabla_csv(datos):
    """Tabla plana Grupo,Parámetro,Valor,Unidad,Interpretación — para guardar
    junto al HTML en la carpeta de salida (todo el detalle numérico en un
    formato que se puede abrir en Excel/pandas)."""
    import csv
    import io

    filas = [('Grupo', 'Parámetro', 'Valor', 'Unidad', 'Interpretación')]

    filas.append(('3.1 Geometría', 'Área', datos.get('area_km2'), 'km2', ''))
    filas.append(('3.1 Geometría', 'Perímetro', datos.get('perimetro_km'), 'km', ''))
    filas.append(('3.1 Geometría', 'Longitud', datos.get('longitud_axial_km'), 'km', ''))
    filas.append(('3.1 Geometría', 'Ancho medio', datos.get('ancho_medio_km'), 'km', ''))

    f = datos.get('forma')
    if f:
        filas.append(('3.2 Forma', 'Índice de compacidad (Gravelius)', f['valor'], '-', f['clase']))
    if datos.get('kf') is not None:
        filas.append(('3.2 Forma', 'Factor de forma (Horton)', datos['kf'], '-', ''))
    if datos.get('re') is not None:
        filas.append(('3.2 Forma', 'Razón de elongación (Schumm)', datos['re'], '-', ''))
    if datos.get('circularidad') is not None:
        filas.append(('3.2 Forma', 'Circularidad (Miller)', datos['circularidad'], '-', ''))

    filas.append(('3.3 Relieve', 'Elevación mínima', datos.get('e_min'), 'm', ''))
    filas.append(('3.3 Relieve', 'Elevación máxima', datos.get('e_max'), 'm', ''))
    filas.append(('3.3 Relieve', 'Elevación media', datos.get('e_media'), 'm', ''))
    p = datos.get('relieve_interp')
    filas.append(('3.3 Relieve', 'Pendiente media', p['valor'] if p else None, '%', p['clase'] if p else ''))
    filas.append(('3.3 Relieve', 'Relieve total', datos.get('relieve_total_m'), 'm', ''))
    filas.append(('3.3 Relieve', 'Índice de rugosidad', datos.get('rugosidad'), '-', ''))
    m = datos.get('melton_interp')
    filas.append(('3.3 Relieve', 'Índice de Melton', m['valor'] if m else None, '-', m['clase'] if m else ''))

    red = datos.get('_red')
    filas.append(('3.4 Red de drenaje', 'Longitud total', red['long_total_km'] if red else None, 'km', ''))
    dd = datos.get('dd_interp')
    filas.append(('3.4 Red de drenaje', 'Densidad de drenaje', dd['valor'] if dd else None, 'km/km2', dd['clase'] if dd else ''))
    filas.append(('3.4 Red de drenaje', 'Frecuencia de corrientes', datos.get('frecuencia'), 'tramos/km2', ''))
    orden = datos.get('_orden')
    if orden:
        for o, l in sorted(orden['long_por_orden'].items()):
            filas.append(('3.4 Red de drenaje', f'Longitud orden {o}', round(l, 3), 'km', ''))
        rb_i = orden.get('rb_interp')
        filas.append(('3.4 Red de drenaje', 'Razón de bifurcación', rb_i['valor'] if rb_i else None, '-', rb_i['clase'] if rb_i else ''))
        dt_i = orden.get('dt_interp')
        filas.append(('3.4 Red de drenaje', 'Textura de drenaje', dt_i['valor'] if dt_i else None, '-', dt_i['clase'] if dt_i else ''))
        filas.append(('3.4 Red de drenaje', 'Orden máximo', orden.get('orden_max'), '-', ''))
        filas.append(('3.4 Red de drenaje', 'Sinuosidad', orden.get('sinuosidad'), '-', ''))

    if datos.get('tc'):
        for met, v in datos['tc'].items():
            if not met.startswith('_'):
                filas.append(('Tiempo de concentración', met, v, 'min', ''))

    hi = datos.get('hipso_interp')
    if hi:
        filas.append(('Curva hipsométrica', 'Integral hipsométrica (HI)', hi['valor'], '-', hi['clase']))

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerows(filas)
    return buf.getvalue()


def construir_informe(datos, faltantes):
    """
    datos: dict con las claves que se lograron calcular (ver hidrologia_extraccion.py)
    faltantes: lista de strings describiendo qué no se pudo calcular y por qué
    Devuelve el informe como texto Markdown.
    """
    L = []
    L.append('# Informe de Interpretación Hidrológica — geeSWAT\n')
    L.append(f"Cuenca/subcuenca analizada: **{datos.get('nombre', 'sin nombre')}**\n")
    L.append(f"Área: **{datos.get('area_km2', '—')} km²**  |  "
             f"Perímetro: **{datos.get('perimetro_km', '—')} km**\n")

    # Forma
    if datos.get('forma'):
        f = datos['forma']
        L.append('## 1. Forma de la cuenca\n')
        L.append(f"Índice de compacidad de Gravelius: **Kc = {f['valor']}** → **{f['clase']}**\n")
        if f.get('detalle_numerico'):
            L.append(f"{f['detalle_numerico']}\n")
        L.append(f"{f['texto']}\n")
        L.append(f"*Fuente: {f['fuente']}*\n")

    # Densidad de drenaje
    if datos.get('dd_interp'):
        d = datos['dd_interp']
        L.append('## 2. Densidad de drenaje\n')
        L.append(f"Dd = **{d['valor']} km/km²** → **{d['clase']}**\n")
        L.append(f"{d['texto']}\n")
        L.append(f"*Fuente: {d['fuente']}*\n")

    # Relieve / pendiente
    if datos.get('relieve_interp'):
        p = datos['relieve_interp']
        L.append('## 3. Relieve y pendiente\n')
        L.append(f"Pendiente media: **{p['valor']}%** → **{p['clase']}**\n")
        L.append(f"{p['texto']}\n")
        if datos.get('relieve_total_m') is not None:
            L.append(f"Relieve total (Emax − Emin): {datos['relieve_total_m']} m.\n")
        L.append(f"*Fuente: {p['fuente']}*\n")

    # Tiempo de concentración
    if datos.get('tc'):
        tc = datos['tc']
        L.append('## 4. Tiempo de concentración\n')
        for metodo, val in tc.items():
            if metodo.startswith('_'):
                continue
            L.append(f"- {metodo}: **{val} min**")
        L.append('')
        if '_rango_min' in tc:
            L.append(f"Rango entre métodos: **{tc['_rango_min']} – {tc['_rango_max']} min**. "
                      f"La dispersión entre métodos es esperable: cada uno fue calibrado con "
                      f"datos de cuencas distintas a la tuya; el rango, no un único valor, es "
                      f"el resultado defendible.\n")

    # Hipsometría
    if datos.get('hipso_interp'):
        h = datos['hipso_interp']
        L.append('## 5. Curva hipsométrica\n')
        L.append(f"Integral hipsométrica: **HI = {h['valor']}** → etapa **{h['clase']}**\n")
        L.append(f"{h['texto']}\n")
        if not h.get('confiable', True):
            L.append(f"> ⚠ {h['advertencia']}\n")
        L.append(f"*Fuente: {h['fuente']}*\n")

    if faltantes:
        L.append('## Limitaciones de este análisis\n')
        for f in faltantes:
            L.append(f"- {f}")
        L.append('')

    return '\n'.join(L)
