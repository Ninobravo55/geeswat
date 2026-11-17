# -*- coding: utf-8 -*-
"""
hidrologia_extraccion.py — geeSWAT
Extrae los insumos numéricos que necesita hidrologia_calculo.py a partir de:
  1) Capas cargadas en QGIS (polígono de cuenca/subcuencas, red de drenaje, DEM)
  2) Opcionalmente, la carpeta TxtInOut de un modelo SWAT ya corrido con QSWAT,
     de donde se leen CH_L1 (longitud de cauce tributario) y CH_S1 (pendiente
     de cauce) por subcuenca — estos son los valores que el propio SWAT usa
     para enrutamiento, así que son la fuente más confiable disponible para
     tiempo de concentración cuando no hay topología completa de red.

Todo lo que depende de qgis.core/osgeo vive aquí, separado del motor de
cálculo puro (hidrologia_calculo.py) para poder testear las fórmulas sin
levantar QGIS.
"""

import os
import glob
import math

from qgis.core import (
    QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject,
    QgsDistanceArea, QgsWkbTypes, QgsRectangle, QgsVectorLayer, QgsFeature,
    QgsPointXY
)

try:
    from osgeo import gdal
    import numpy as np
    GDAL_OK = True
except ImportError:
    GDAL_OK = False


# ═══════════════════════════════════════════════════════════════════════════
# GEOMETRÍA DE LA CUENCA (polígono)
# ═══════════════════════════════════════════════════════════════════════════

def extraer_geometria_cuenca(layer):
    """
    Disuelve todas las features del layer de cuenca/subcuencas en una sola
    geometría y calcula área, perímetro y longitud axial (eje mayor del
    rectángulo mínimo envolvente, usado como proxy de longitud de cuenca).
    Devuelve valores en km / km2, calculados sobre una CRS proyectada
    (metros) — si el layer está en geográficas, se avisa en el log.
    """
    if layer is None:
        return None

    geoms = [f.geometry() for f in layer.getFeatures() if not f.geometry().isNull()]
    if not geoms:
        return None

    geom_total = geoms[0]
    for g in geoms[1:]:
        geom_total = geom_total.combine(g)
    geom_total = geom_total.makeValid()

    da = QgsDistanceArea()
    da.setSourceCrs(layer.crs(), QgsProject.instance().transformContext())
    da.setEllipsoid(QgsProject.instance().ellipsoid() or 'WGS84')

    area_m2 = da.measureArea(geom_total)
    perim_m = da.measurePerimeter(geom_total)

    try:
        obb = geom_total.orientedMinimumBoundingBox()
        # QGIS devuelve (QgsGeometry, area, angle, width, height)
        _, _, _, width, height = obb
        longitud_axial_m = max(width, height)
    except Exception:
        longitud_axial_m = None

    # Si la capa está en geográficas, orientedMinimumBoundingBox da grados,
    # no metros: en ese caso recalculamos la longitud axial con QgsDistanceArea
    # sobre los dos vértices más separados del contorno (más lento pero correcto).
    is_geografica = layer.crs().isGeographic()
    if is_geografica or longitud_axial_m is None:
        longitud_axial_m = _longitud_maxima_contorno(geom_total, da)

    return {
        'area_km2': round(area_m2 / 1e6, 3),
        'perimetro_km': round(perim_m / 1000, 3),
        'longitud_axial_km': round(longitud_axial_m / 1000, 3) if longitud_axial_m else None,
        'crs_geografica': is_geografica,
    }


def _longitud_maxima_contorno(geom, da, muestreo_max=300):
    """Distancia máxima entre pares de vértices del contorno (fallback cuando
    orientedMinimumBoundingBox no es confiable, p.ej. CRS geográfica)."""
    try:
        anillo = geom.convertToType(QgsWkbTypes.LineGeometry, False)
        pts = [v for v in anillo.vertices()]
    except Exception:
        return None
    if len(pts) > muestreo_max:
        paso = len(pts) // muestreo_max
        pts = pts[::paso]
    max_d = 0.0
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            d = da.measureLine(pts[i], pts[j])
            if d > max_d:
                max_d = d
    return max_d if max_d > 0 else None


# ═══════════════════════════════════════════════════════════════════════════
# RED DE DRENAJE (líneas)
# ═══════════════════════════════════════════════════════════════════════════

def extraer_red_drenaje(layer):
    """Longitud total de la red (para densidad de drenaje) y longitud del
    cauce individual más largo (proxy de cauce principal si no hay topología)."""
    if layer is None:
        return None

    da = QgsDistanceArea()
    da.setSourceCrs(layer.crs(), QgsProject.instance().transformContext())
    da.setEllipsoid(QgsProject.instance().ellipsoid() or 'WGS84')

    total_m = 0.0
    max_m = 0.0
    n = 0
    for f in layer.getFeatures():
        g = f.geometry()
        if g.isNull():
            continue
        length_m = da.measureLength(g)
        total_m += length_m
        if length_m > max_m:
            max_m = length_m
        n += 1

    if n == 0:
        return None

    return {
        'long_total_km': round(total_m / 1000, 3),
        'cauce_mas_largo_km': round(max_m / 1000, 3),
        'n_tramos': n,
    }


def analizar_red_por_orden(streams_layer, campo_orden, log=None):
    """
    Agrupa los tramos de streams_layer por el valor de campo_orden (se asume
    orden de Strahler, pero funciona con cualquier campo entero de orden).
    Devuelve conteo y longitud por orden, y la geometría fusionada del cauce
    de mayor orden (para calcular sinuosidad).
    """
    if streams_layer is None or not campo_orden:
        return None

    da = QgsDistanceArea()
    da.setSourceCrs(streams_layer.crs(), QgsProject.instance().transformContext())
    da.setEllipsoid(QgsProject.instance().ellipsoid() or 'WGS84')

    conteo, longitud, geoms_por_orden = {}, {}, {}
    for f in streams_layer.getFeatures():
        try:
            orden = int(f[campo_orden])
        except (TypeError, ValueError):
            continue
        g = f.geometry()
        if g.isNull():
            continue
        length_km = da.measureLength(g) / 1000.0
        conteo[orden] = conteo.get(orden, 0) + 1
        longitud[orden] = longitud.get(orden, 0.0) + length_km
        geoms_por_orden.setdefault(orden, []).append(g)

    if not conteo:
        if log:
            log(f'El campo "{campo_orden}" no tiene valores enteros legibles '
                f'en la capa de red de drenaje; se omite el análisis por orden.')
        return None

    orden_max = max(conteo.keys())
    main_geoms = geoms_por_orden[orden_max]
    main_union = main_geoms[0]
    for g in main_geoms[1:]:
        main_union = main_union.combine(g)
    try:
        merged = main_union.mergeLines() if main_union.isMultipart() else main_union
        vertices = list(merged.vertices())
        if len(vertices) >= 2:
            p1 = QgsPointXY(vertices[0])
            p2 = QgsPointXY(vertices[-1])
            recta_km = da.measureLine(p1, p2) / 1000.0
        else:
            recta_km = None
    except Exception as e:
        if log:
            log(f'No se pudo calcular la geometría del cauce principal para '
                f'sinuosidad: {e}')
        recta_km = None

    return {
        'conteo_por_orden': conteo,
        'long_por_orden': longitud,
        'orden_max': orden_max,
        'n_tramos_total': sum(conteo.values()),
        'main_channel_length_km': longitud[orden_max],
        'main_channel_straight_km': recta_km,
    }




def _verificar_solape(dem_layer, mask_layer, log=None):
    """Comprueba que la extensión del DEM realmente se superpone con la
    cuenca (reproyectando la extensión de la cuenca al CRS del DEM). Un
    recorte GDAL sobre capas sin solape no lanza excepción en QGIS: solo
    no escribe archivo de salida, lo que después se ve como un
    'No such file or directory' difícil de diagnosticar. Se verifica esto
    primero para dar un mensaje directo."""
    dem_extent = dem_layer.extent()
    mask_extent = mask_layer.extent()
    try:
        if dem_layer.crs() != mask_layer.crs():
            transform = QgsCoordinateTransform(
                mask_layer.crs(), dem_layer.crs(), QgsProject.instance())
            mask_extent_en_dem = transform.transformBoundingBox(mask_extent)
        else:
            mask_extent_en_dem = mask_extent
    except Exception as e:
        if log:
            log(f'No se pudo comparar la extensión del DEM con la de la '
                f'cuenca (fallo de reproyección: {e}). Se continúa sin '
                f'verificación previa.')
        return True

    if not dem_extent.intersects(mask_extent_en_dem):
        if log:
            log('ALERTA: el DEM y la cuenca NO se superponen — por eso el '
                'recorte no genera archivo. '
                f"DEM: CRS {dem_layer.crs().authid()}, extensión "
                f"{dem_extent.toString(2)}. "
                f"Cuenca: CRS {mask_layer.crs().authid()}, extensión "
                f"(reproyectada al CRS del DEM) {mask_extent_en_dem.toString(2)}. "
                'Revisa que ambas capas correspondan a la misma zona de '
                'estudio y que el CRS asignado a cada una sea el correcto '
                '(un CRS mal asignado sin reproyectar los datos produce '
                'exactamente este síntoma).')
        return False
    return True


def _mask_layer_valida(mask_layer, log=None):
    """
    Disuelve y repara (makeValid) la geometría de mask_layer en una capa en
    memoria de una sola feature. gdal:cliprasterbymasklayer usa esa geometría
    como cutline, y GDAL exige un polígono simple y válido — los shapefiles
    de subcuencas de QSWAT (varias features, a veces con auto-intersecciones
    por el paso ráster→vector) suelen fallar el cutline sin lanzar excepción
    en Python: el recorte simplemente no escribe archivo. Reparar/disolver
    aquí evita ese fallo silencioso.
    """
    try:
        geoms = [f.geometry() for f in mask_layer.getFeatures() if not f.geometry().isNull()]
        if not geoms:
            return mask_layer
        geom_total = geoms[0]
        for g in geoms[1:]:
            geom_total = geom_total.combine(g)
        geom_total = geom_total.makeValid()
        if geom_total.isEmpty():
            if log:
                log('La geometría disuelta de la cuenca quedó vacía tras '
                    'reparar; se usa la capa original sin reparar.')
            return mask_layer

        mem_layer = QgsVectorLayer(f'Polygon?crs={mask_layer.crs().authid()}',
                                    'mask_valida', 'memory')
        prov = mem_layer.dataProvider()
        feat = QgsFeature()
        feat.setGeometry(geom_total)
        prov.addFeature(feat)
        mem_layer.updateExtents()
        return mem_layer
    except Exception as e:
        if log:
            log(f'No se pudo reparar la geometría de la cuenca para el '
                f'recorte ({e}); se usa la capa original.')
        return mask_layer


def extraer_relieve_dem(dem_layer, mask_layer, n_clases=25, log=None):
    """
    Recorta el DEM al polígono de cuenca y calcula:
      - e_min, e_max, e_media
      - pendiente media (%) vía processing 'native:slope' + estadística zonal
      - pares (elevación, área_acumulada_m2) para la curva hipsométrica

    Requiere que el procesamiento 'processing' de QGIS esté disponible
    (siempre lo está dentro de QGIS) y GDAL/numpy para leer el raster recortado.
    """
    if not GDAL_OK:
        if log:
            log('GDAL/numpy no disponibles en este entorno Python: no se '
                'puede calcular relieve ni curva hipsométrica.')
        return None
    if dem_layer is None or mask_layer is None:
        return None

    if not _verificar_solape(dem_layer, mask_layer, log=log):
        return None

    import processing
    import tempfile

    mask_valida = _mask_layer_valida(mask_layer, log=log)

    tmp_dir = tempfile.mkdtemp(prefix='geeswat_hidro_')
    dem_clip_path = os.path.join(tmp_dir, 'dem_clip.tif')
    slope_path = os.path.join(tmp_dir, 'slope.tif')
    slope_clip_path = os.path.join(tmp_dir, 'slope_clip.tif')

    try:
        r = processing.run('gdal:cliprasterbymasklayer', {
            'INPUT': dem_layer,
            'MASK': mask_valida,
            'CROP_TO_CUTLINE': True,
            'KEEP_RESOLUTION': True,
            'OUTPUT': dem_clip_path,
        })
        dem_clip_path = r.get('OUTPUT', dem_clip_path)
        if not os.path.isfile(dem_clip_path):
            if log:
                log(f'El recorte del DEM no generó archivo aun con la geometría '
                    f'de cuenca reparada. Verifica que el DEM '
                    f'(CRS: {dem_layer.crs().authid()}) realmente cubra la '
                    f'extensión de la cuenca (CRS: {mask_layer.crs().authid()}).')
            return None
    except Exception as e:
        if log:
            log(f'No se pudo recortar el DEM a la cuenca: {e}')
        return None

    # Pendiente sobre el DEM completo (evita artefactos de borde del recorte)
    # y luego se recorta el resultado.
    try:
        r_slope = processing.run('native:slope', {
            'INPUT': dem_layer,
            'Z_FACTOR': 1,
            'OUTPUT': slope_path,
        })
        slope_path = r_slope.get('OUTPUT', slope_path)
        r_clip = processing.run('gdal:cliprasterbymasklayer', {
            'INPUT': slope_path,
            'MASK': mask_valida,
            'CROP_TO_CUTLINE': True,
            'KEEP_RESOLUTION': True,
            'OUTPUT': slope_clip_path,
        })
        slope_clip_path = r_clip.get('OUTPUT', slope_clip_path)
        if os.path.isfile(slope_clip_path):
            pendiente_media_pct = _media_raster(slope_clip_path)
        else:
            pendiente_media_pct = None
            if log:
                log('El recorte de pendiente no generó archivo; pendiente '
                    'media no disponible.')
    except Exception as e:
        if log:
            log(f'No se pudo calcular la pendiente media desde el DEM: {e}')
        pendiente_media_pct = None

    if not os.path.isfile(dem_clip_path):
        if log:
            log('El DEM recortado no existe en disco tras el procesamiento.')
        return None
    ds = gdal.Open(dem_clip_path)
    if ds is None:
        if log:
            log('El DEM recortado no se pudo abrir con GDAL.')
        return None
    banda = ds.GetRasterBand(1)
    nodata = banda.GetNoDataValue()
    arr = banda.ReadAsArray().astype(float)
    gt = ds.GetGeoTransform()
    px_area_m2 = abs(gt[1] * gt[5])

    if nodata is not None:
        validos = arr[arr != nodata]
    else:
        validos = arr.flatten()
    validos = validos[~np.isnan(validos)]

    if validos.size < 5:
        if log:
            log('Muy pocos píxeles válidos en el DEM recortado; revisa que '
                'el DEM cubra realmente el área de la cuenca.')
        return None

    e_min = float(validos.min())
    e_max = float(validos.max())
    e_media = float(validos.mean())

    # Curva hipsométrica: histograma de elevaciones -> área acumulada
    # desde la elevación máxima hacia la mínima (convención estándar).
    hist, bin_edges = np.histogram(validos, bins=n_clases, range=(e_min, e_max))
    pares = []
    area_acum_px = 0
    # Recorremos de mayor a menor elevación acumulando área
    for i in reversed(range(len(hist))):
        area_acum_px += hist[i]
        elev_repr = (bin_edges[i] + bin_edges[i + 1]) / 2.0
        pares.append((round(elev_repr, 1), area_acum_px * px_area_m2))

    return {
        'e_min': round(e_min, 1),
        'e_max': round(e_max, 1),
        'e_media': round(e_media, 1),
        'relieve_total_m': round(e_max - e_min, 1),
        'pendiente_media_pct': round(pendiente_media_pct, 2) if pendiente_media_pct else None,
        'pares_area_elevacion': pares,
        'n_pixeles_validos': int(validos.size),
    }


def media_raster_zonal(raster_layer, mask_layer, log=None):
    """Recorta raster_layer a mask_layer y devuelve la media de los píxeles
    válidos. Se usa para obtener el CN medio de la cuenca (método SCS Lag)."""
    if not GDAL_OK or raster_layer is None or mask_layer is None:
        return None
    if not _verificar_solape(raster_layer, mask_layer, log=log):
        return None
    import processing
    import tempfile
    mask_valida = _mask_layer_valida(mask_layer, log=log)
    tmp_dir = tempfile.mkdtemp(prefix='geeswat_hidro_cn_')
    clip_path = os.path.join(tmp_dir, 'cn_clip.tif')
    try:
        r = processing.run('gdal:cliprasterbymasklayer', {
            'INPUT': raster_layer,
            'MASK': mask_valida,
            'CROP_TO_CUTLINE': True,
            'KEEP_RESOLUTION': True,
            'OUTPUT': clip_path,
        })
        clip_path = r.get('OUTPUT', clip_path)
        if not os.path.isfile(clip_path):
            if log:
                log('El recorte del ráster CN no generó archivo.')
            return None
        return _media_raster(clip_path)
    except Exception as e:
        if log:
            log(f'No se pudo calcular el CN medio de la cuenca: {e}')
        return None


def _media_raster(path):
    ds = gdal.Open(path)
    if ds is None:
        return None
    banda = ds.GetRasterBand(1)
    nodata = banda.GetNoDataValue()
    arr = banda.ReadAsArray().astype(float)
    if nodata is not None:
        arr = arr[arr != nodata]
    arr = arr[~np.isnan(arr)]
    if arr.size == 0:
        return None
    return float(arr.mean())


# ═══════════════════════════════════════════════════════════════════════════
# TxtInOut de SWAT (respaldo / complemento para tiempo de concentración)
# ═══════════════════════════════════════════════════════════════════════════

def layer_de_feature(layer, feature, tipo='Polygon'):
    """Crea una capa en memoria de una sola feature (misma CRS que layer).
    Se usa para poder reutilizar extraer_geometria_cuenca / extraer_relieve_dem
    / media_raster_zonal sobre UNA subcuenca a la vez, en vez de la cuenca
    disuelta completa."""
    mem = QgsVectorLayer(f'{tipo}?crs={layer.crs().authid()}', 'feature_unica', 'memory')
    prov = mem.dataProvider()
    f2 = QgsFeature()
    f2.setGeometry(feature.geometry())
    prov.addFeature(f2)
    mem.updateExtents()
    return mem


def filtrar_red_por_subcuenca(streams_layer, subcuenca_geom, campo_vinculo=None, valor_id=None):
    """
    Devuelve una capa en memoria con los tramos de streams_layer que
    pertenecen a una subcuenca:
      - si campo_vinculo se indica, filtra por streams[campo_vinculo] == valor_id
        (exacto y rápido — usa la topología ya calculada por QSWAT)
      - si no, filtra espacialmente por intersección con la geometría de la
        subcuenca (más lento, aproximado en los bordes)
    """
    mem = QgsVectorLayer(f'LineString?crs={streams_layer.crs().authid()}',
                          'red_subcuenca', 'memory')
    prov = mem.dataProvider()
    prov.addAttributes(streams_layer.fields())
    mem.updateFields()
    nuevas = []
    for f in streams_layer.getFeatures():
        usar = False
        if campo_vinculo:
            try:
                usar = (f[campo_vinculo] == valor_id)
            except KeyError:
                usar = False
        else:
            g = f.geometry()
            usar = (not g.isNull()) and g.intersects(subcuenca_geom)
        if usar:
            nf = QgsFeature(mem.fields())
            nf.setGeometry(f.geometry())
            nf.setAttributes(f.attributes())
            nuevas.append(nf)
    if nuevas:
        prov.addFeatures(nuevas)
    mem.updateExtents()
    return mem



def leer_txtinout_subbasins(carpeta_txtinout, log=None):
    """
    Recorre carpeta_txtinout buscando archivos *.sub (uno por subcuenca en
    QSWAT clásico/ArcSWAT) y extrae SUB_KM, CH_L1 (km), CH_S1 (m/m), ELEV.
    Devuelve una lista de dicts, uno por subcuenca encontrada.
    """
    if not carpeta_txtinout or not os.path.isdir(carpeta_txtinout):
        return []

    resultados = []
    for path in sorted(glob.glob(os.path.join(carpeta_txtinout, '*.sub'))):
        try:
            with open(path, 'rb') as f:
                raw = f.read().decode('latin-1')
        except Exception as e:
            if log:
                log(f'No se pudo leer {os.path.basename(path)}: {e}')
            continue

        datos = {'archivo': os.path.basename(path)}
        for linea in raw.splitlines():
            if '|' not in linea:
                continue
            valor_str, etiqueta = linea.split('|', 1)
            etiqueta = etiqueta.strip()
            valor_str = valor_str.strip()
            campo = None
            if etiqueta.startswith('SUB_KM'):
                campo = 'sub_km2'
            elif etiqueta.startswith('CH_L1'):
                campo = 'ch_l1_km'
            elif etiqueta.startswith('CH_S1'):
                campo = 'ch_s1_mm'
            elif etiqueta.startswith('CH_W1'):
                campo = 'ch_w1_m'
            elif etiqueta.startswith('ELEV'):
                campo = 'elev_m'
            if campo:
                try:
                    datos[campo] = float(valor_str)
                except ValueError:
                    pass

        # Subbasin id embebido en el nombre, ej. 000010000.sub -> subbasin 1
        base = os.path.basename(path).replace('.sub', '')
        try:
            datos['subbasin'] = int(base) // 10000
        except ValueError:
            datos['subbasin'] = None

        if 'sub_km2' in datos:
            resultados.append(datos)

    return resultados
