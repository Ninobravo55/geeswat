# -*- coding: utf-8 -*-
from .export_router_enhanced import upload_associated_files_to_drive
"""
mde_descargar_mde.py — geeSWAT
Descarga MDE desde Google Earth Engine para análisis hidrológico SWAT.

Estrategia:
  - Área pequeña : descarga directa getDownloadURL → GeoTIFF local
  - Área grande  : Tiling (división en cuadrantes) → descarga directa → Mosaico GDAL

Límite real de GEE: 50 331 648 bytes (~48 MB) por request de descarga directa.
Umbral conservador usado por tile: 2 MP para garantizar estabilidad.

geeSWAT v1.2.3 — Geomatica Ambiental
"""
from qgis.PyQt.QtGui import QIcon
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterExtent,
    QgsProcessingParameterEnum,
    QgsProcessingParameterString,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterNumber,
    QgsProject,
    QgsRasterLayer,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
)
import os
import time
import math

# ─────────────────────────────────────────────────────────────────────────────
# Constantes
# ─────────────────────────────────────────────────────────────────────────────
LIMITE_TILE_PX = 2_000_000   # píxeles — límite por tile para descarga directa

# ─────────────────────────────────────────────────────────────────────────────
# Catálogo de DEMs
# ─────────────────────────────────────────────────────────────────────────────
GEE_DEMS = [
    {
        'label'     : 'SRTM GL1 — 30 m global (NASA/USGS)',
        'collection': 'USGS/SRTMGL1_003',
        'band'      : 'elevation',
        'scale'     : 30,
        'tipo'      : 'Image',
        'dtype'     : 'Int16',
        'desc'      : 'SRTM 1 arc-sec. Global 56S-60N. Datum EGM96.',
    },
    {
        'label'     : 'FABDEM GLO-30 — 30 m global (sin edificios ni bosques)',
        'collection': 'projects/sat-io/open-datasets/FABDEM',
        'band'      : 'b1',
        'scale'     : 30,
        'tipo'      : 'ImageCollection',
        'dtype'     : 'Float32',
        'desc'      : (
            'FABDEM (Forest And Buildings removed Copernicus DEM). '
            'Elimina sesgos de altura de edificios y árboles del DEM Copernicus GLO-30. '
            'Resolución ~30 m global. Ideal para modelamiento hidrológico SWAT.'
        ),
    },
    {
        'label'     : 'Copernicus DEM GLO-30 — 30 m global',
        'collection': 'COPERNICUS/DEM/GLO30',
        'band'      : 'DEM',
        'scale'     : 30,
        'tipo'      : 'ImageCollection',
        'dtype'     : 'Float32',
        'desc'      : 'TanDEM-X. Cobertura global. Alta precisión.',
    },
    {
        'label'     : 'NASADEM — 30 m global (NASA)',
        'collection': 'NASA/NASADEM_HGT/001',
        'band'      : 'elevation',
        'scale'     : 30,
        'tipo'      : 'Image',
        'dtype'     : 'Int16',
        'desc'      : 'SRTM reprocesado con ASTER/ICESat. Global.',
    },
    {
        'label'     : 'ALOS AW3D30 — 30 m global (JAXA)',
        'collection': 'JAXA/ALOS/AW3D30/V3_2',
        'band'      : 'DSM',
        'scale'     : 30,
        'tipo'      : 'ImageCollection',
        'dtype'     : 'Float32',
        'desc'      : 'DSM global de ALOS PRISM. JAXA.',
    },
    {
        'label'     : 'MERIT DEM — 90 m global (corregido)',
        'collection': 'MERIT/DEM/v1_0_3',
        'band'      : 'dem',
        'scale'     : 90,
        'tipo'      : 'Image',
        'dtype'     : 'Float32',
        'desc'      : 'SRTM/AW3D30 corregido por vegetación y speckle. Ideal SWAT.',
    },
    {
        'label'     : 'SRTM 90 m (CGIAR-CSI v4)',
        'collection': 'CGIAR/SRTM90_V4',
        'band'      : 'elevation',
        'scale'     : 90,
        'tipo'      : 'Image',
        'dtype'     : 'Int16',
        'desc'      : 'SRTM 90 m con vacíos rellenados. CGIAR-CSI v4.',
    },
    {
        'label'     : 'HydroSHEDS — 90 m void-filled (WWF)',
        'collection': 'WWF/HydroSHEDS/03VFDEM',
        'band'      : 'b1',
        'scale'     : 90,
        'tipo'      : 'Image',
        'dtype'     : 'Int16',
        'desc'      : 'DEM hidrológicamente acondicionado. Excelente para SWAT.',
    },
    {
        'label'     : 'ASTER GDEM v3 — 30 m global (NASA/METI)',
        'collection': 'NASA/ASTER_GED/AG100_003',
        'band'      : 'elevation',
        'scale'     : 30,
        'tipo'      : 'Image',
        'dtype'     : 'Float32',
        'desc'      : 'ASTER GDEM v3. Cobertura 83N-83S.',
    },
    {
        'label'     : '3DEP 1 m (solo EE.UU.) — USGS',
        'collection': 'USGS/3DEP/1m',
        'band'      : 'elevation',
        'scale'     : 1,
        'tipo'      : 'ImageCollection',
        'dtype'     : 'Float32',
        'desc'      : '3D Elevation Program 1 metro. Solo EE.UU.',
    },
    {
        'label'     : 'DEM France 5 m — RGE ALTI (IGN)',
        'collection': 'projects/sat-io/open-datasets/IGN_RGE_Alti_5m',
        'band'      : 'b1',
        'scale'     : 5,
        'tipo'      : 'Image',
        'dtype'     : 'Float32',
        'desc'      : (
            'RGE ALTI® 5m. Elevación del suelo de Francia continental '
            'con resolución 5x5m. Producido por el Institut National '
            'de l\'Information Géographique et Forestière (IGN). Solo Francia.'
        ),
    },
]

DEM_LABELS = [d['label'] for d in GEE_DEMS]

# Bytes por píxel según dtype
DTYPE_BYTES = {'Int16': 2, 'Int32': 4, 'Float32': 4, 'Float64': 8, 'Byte': 1}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_ee(feedback):
    try:
        import ee
        return ee
    except ImportError:
        raise QgsProcessingException(
            "Falta el módulo 'earthengine-api'.\n"
            "Instálalo en la OSGeo4W Shell (Windows):\n"
            "   python -m pip install earthengine-api\n"
            "Linux / macOS:\n"
            "   pip install earthengine-api"
        )


def _autenticar_gee(ee, correo, proyecto, feedback):
    try:
        kwargs = {}
        if proyecto.strip():
            kwargs['project'] = proyecto.strip()

        if correo.strip():
            feedback.pushInfo(f"Conectando GEE | cuenta: {correo}")
            feedback.pushInfo(f"Proyecto       : {proyecto or '(por defecto)'}")
            ee.Initialize(opt_url='https://earthengine.googleapis.com', **kwargs)
        else:
            feedback.pushInfo("Conectando GEE con credenciales locales guardadas...")
            ee.Initialize(**kwargs)

        feedback.pushInfo("Conexión a Google Earth Engine exitosa.")

    except Exception as ex:
        msg = str(ex)
        if any(k in msg.lower() for k in ('authorize', 'credentials', 'oauth', 'token')):
            raise QgsProcessingException(
                "No se encontraron credenciales GEE válidas.\n\n"
                "Abre el diálogo geeSWAT (⚙ Config GEE) y haz clic en\n"
                "'Autenticar con Google' para guardar el token.\n\n"
                "O ejecuta en la Consola Python de QGIS:\n"
                "   import ee\n"
                "   ee.Authenticate()                          # abre navegador\n"
                "   ee.Initialize(project='TU_PROYECTO_GEE')  # valida la sesión\n\n"
                "Luego vuelve a ejecutar esta herramienta."
            )
        raise QgsProcessingException(f"Error al conectar GEE: {msg}")


def _construir_imagen(ee, dem_info, region):
    cid  = dem_info['collection']
    band = dem_info['band']
    if dem_info['tipo'] == 'Image':
        img = ee.Image(cid).select(band)
    else:
        img = (ee.ImageCollection(cid)
                 .filterBounds(region)
                 .mosaic()
                 .select(band))
    return img.clip(region)


def _estimar_pixeles(lon_min, lat_min, lon_max, lat_max, scale):
    """Píxeles con corrección coseno latitudinal."""
    lat_med = (lat_min + lat_max) / 2.0
    cos_lat = math.cos(math.radians(lat_med))
    ancho_m = (lon_max - lon_min) * 111320.0 * cos_lat
    alto_m  = (lat_max - lat_min) * 111320.0
    px_x    = max(1, int(ancho_m / scale))
    px_y    = max(1, int(alto_m  / scale))
    return px_x * px_y, px_x, px_y


def _estimar_bytes(total_px, dtype):
    bpp = DTYPE_BYTES.get(dtype, 4)
    return total_px * bpp


def _descarga_directa(ee, imagen, scale, region, crs, output_path, feedback):
    """getDownloadURL → GeoTIFF local. Maneja ZIP o TIF directo."""
    import zipfile
    import shutil
    from urllib.parse import urlparse
    import requests

    feedback.pushInfo(f"Generando URL de descarga directa en GEE (CRS: {crs})...")
    try:
        url = imagen.getDownloadURL({
            'scale' : scale,
            'region': region,
            'format': 'GEO_TIFF',
            'crs'   : crs,
        })
    except Exception as ex:
        msg = str(ex)
        if '50331648' in msg or 'request size' in msg.lower() or 'must be less than' in msg.lower():
            raise QgsProcessingException(
                "GEE rechaza la descarga directa porque el archivo supera 48 MB.\n"
                "Reduce el área o aumenta la escala.\n\n"
                f"Detalle GEE: {msg}"
            )
        raise QgsProcessingException(f"Error al generar URL: {msg}")

    # Validar esquema de URL (seguridad)
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise QgsProcessingException(
            f"Esquema de URL no permitido: {parsed.scheme}"
        )

    feedback.pushInfo("Descargando GeoTIFF desde GEE...")
    tmp = output_path + '_tmp.bin'

    try:
        with requests.get(url, stream=True, timeout=120) as r:
            r.raise_for_status()
            total = int(r.headers.get('content-length', 0))
            descargado = 0
            with open(tmp, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        descargado += len(chunk)
                        if total > 0:
                            feedback.setProgress(min(int(descargado * 100 / total), 100))
    except Exception as ex:
        raise QgsProcessingException(f"Error en descarga: {str(ex)}")

    # Descomprimir si es ZIP
    if zipfile.is_zipfile(tmp):
        feedback.pushInfo("Descomprimiendo ZIP de GEE...")
        with zipfile.ZipFile(tmp, 'r') as zf:
            tifs = [n for n in zf.namelist() if n.lower().endswith('.tif')]
            if not tifs:
                raise QgsProcessingException("El ZIP no contiene archivos .tif")
            extracted = zf.extract(tifs[0], os.path.dirname(output_path) or '.')
        shutil.move(extracted, output_path)
        os.remove(tmp)
    else:
        shutil.move(tmp, output_path)

    feedback.pushInfo(f"MDE guardado en: {output_path}")


def _descarga_tiled(ee, imagen, crs_str, total_px, lon_min, lat_min, lon_max, lat_max, scale_eff, output_path, feedback):
    import math, os
    try:
        from osgeo import gdal
    except ImportError:
        raise QgsProcessingException("La librería GDAL no está disponible en tu entorno QGIS.")

    num_tiles = math.ceil(total_px / LIMITE_TILE_PX)

    if num_tiles <= 1:
        feedback.pushInfo("Tamaño dentro del límite seguro -> descarga en un solo bloque.")
        region = ee.Geometry.Rectangle([lon_min, lat_min, lon_max, lat_max])
        _descarga_directa(ee, imagen, scale_eff, region, crs_str, output_path, feedback)
        return

    n_cols = max(1, math.ceil(math.sqrt(num_tiles * ((lon_max - lon_min) / max(1e-9, lat_max - lat_min)))))
    n_rows = max(1, math.ceil(num_tiles / n_cols))
    
    total_tiles = n_cols * n_rows
    feedback.pushInfo(f"Área grande. Dividiendo en {n_cols}x{n_rows} = {total_tiles} cuadrantes para evitar límites de GEE...")
    
    lon_step = (lon_max - lon_min) / n_cols
    lat_step = (lat_max - lat_min) / n_rows
    
    temp_files = []
    k = 0
    
    for i in range(n_cols):
        for j in range(n_rows):
            if feedback.isCanceled():
                raise QgsProcessingException("Cancelado por el usuario.")
            k += 1
            feedback.pushInfo(f"Descargando cuadrante {k}/{total_tiles}...")
            
            sub_lon_min = lon_min + i * lon_step
            sub_lon_max = lon_min + (i + 1) * lon_step
            sub_lat_min = lat_min + j * lat_step
            sub_lat_max = lat_min + (j + 1) * lat_step
            
            sub_region = ee.Geometry.Rectangle([sub_lon_min, sub_lat_min, sub_lon_max, sub_lat_max])
            temp_file = output_path.replace('.tif', f'_tile_{i}_{j}.tif')
            _descarga_directa(ee, imagen, scale_eff, sub_region, crs_str, temp_file, feedback)
            temp_files.append(temp_file)
            feedback.setProgress(int((k / total_tiles) * 100))
    
    if feedback.isCanceled():
        raise QgsProcessingException("Cancelado por el usuario.")

    feedback.pushInfo("Uniendo cuadrantes (Mosaico con GDAL)...")
    vrt_path = output_path.replace('.tif', '_temp.vrt')
    gdal.BuildVRT(vrt_path, temp_files)
    
    warp_options = gdal.WarpOptions(format='GTiff', creationOptions=['COMPRESS=LZW'])
    gdal.Warp(output_path, vrt_path, options=warp_options)
    
    feedback.pushInfo("Limpiando archivos temporales...")
    if os.path.exists(vrt_path):
        os.remove(vrt_path)
    for tf in temp_files:
        if os.path.exists(tf):
            os.remove(tf)
    feedback.pushInfo(f"MDE guardado en: {output_path}")


# ─────────────────────────────────────────────────────────────────────────────
# Algoritmo QGIS Processing
# ─────────────────────────────────────────────────────────────────────────────

class MDEDescargarMDE(QgsProcessingAlgorithm):
    """Descarga un MDE desde Google Earth Engine para uso en SWAT."""

    EXTENT  = 'EXTENT'
    EMAIL   = 'EMAIL'
    PROJECT = 'PROJECT'
    DATASET = 'DATASET'
    SCALE   = 'SCALE'
    OPEN    = 'OPEN'
    OUTPUT  = 'OUTPUT'

    def createInstance(self):
        return MDEDescargarMDE()

    def name(self):
        return 'descargar_mde'

    def displayName(self):
        return 'Descargar MDE (DEM desde GEE)'

    def group(self):
        return 'MDE'

    def groupId(self):
        return 'mde_geeswat'

    def tags(self):
        return [
            'mde', 'dem', 'elevacion', 'srtm', 'copernicus', 'nasadem',
            'alos', 'merit', 'aster', '3dep', 'hydrosheds', 'gee',
            'google earth engine', 'google drive', 'descarga', 'cuenca',
            'swat', 'hidrologia', 'modelo', 'terreno', 'digital',
        ]

    def icon(self):
        icon_path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                 'Icons', 'dem.png')
        if os.path.exists(icon_path):
            return QIcon(icon_path)
        return QIcon()

    def shortHelpString(self):
        catalogo = ''.join(
            f'<li><b>{d["label"]}</b><br>'
            f'<small>{d["desc"]} | Nativo: {d["scale"]} m | Tipo: {d["dtype"]}</small></li>'
            for d in GEE_DEMS
        )
        return f"""
<b>Descargar MDE desde Google Earth Engine — geeSWAT</b><br><br>

La herramienta realiza la descarga directa al disco local, dividiendo automáticamente el área en cuadrantes si es muy grande para evitar los límites de tamaño de GEE.<br><br>

<b>Configuración inicial (una sola vez):</b><br>
<ol>
  <li>Cuenta aprobada en
      <a href="https://earthengine.google.com">earthengine.google.com</a></li>
  <li><code>python -m pip install earthengine-api</code></li>
  <li>Abre el diálogo <b>geeSWAT → ⚙ Configuración GEE</b> y haz clic en
      <b>Autenticar con Google</b>.</li>
</ol>

<b>DEMs disponibles:</b><br><ul>{catalogo}</ul>

<b>Recomendación para SWAT:</b><br>
Usa <b>HydroSHEDS 90 m</b> o <b>MERIT DEM 90 m</b> para cuencas grandes,
o <b>SRTM/Copernicus 30 m</b> para cuencas pequeñas y medianas.
"""

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterString(
            self.EMAIL,
            'Correo de cuenta Google Earth Engine (opcional)',
            defaultValue='',
            optional=True
        ))
        self.addParameter(QgsProcessingParameterString(
            self.PROJECT,
            'ID del proyecto GEE (Google Cloud Project)',
            defaultValue='',
            optional=True
        ))
        self.addParameter(QgsProcessingParameterExtent(
            self.EXTENT,
            'Extensión del área de interés (cuenca)'
        ))
        self.addParameter(QgsProcessingParameterEnum(
            self.DATASET,
            'Fuente de elevación (DEM)',
            options=DEM_LABELS,
            defaultValue=0
        ))
        self.addParameter(QgsProcessingParameterNumber(
            self.SCALE,
            'Escala de salida (m)  —  0 = resolución nativa del DEM',
            type=QgsProcessingParameterNumber.Type.Integer,
            defaultValue=0,
            minValue=0,
            optional=True
        ))
        self.addParameter(QgsProcessingParameterBoolean(
            self.OPEN,
            'Cargar MDE en QGIS al finalizar (solo descarga directa)',
            defaultValue=True
        ))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUTPUT,
            'Archivo de salida local (solo descarga directa)',
            fileFilter='GeoTIFF (*.tif)'
        ))

    def processAlgorithm(self, parameters, context, feedback):
        correo   = self.parameterAsString(parameters, self.EMAIL,   context).strip()
        proyecto = self.parameterAsString(parameters, self.PROJECT, context).strip()
        idx_dem  = self.parameterAsEnum(parameters,   self.DATASET, context)
        escala   = self.parameterAsInt(parameters,    self.SCALE,   context)
        output   = self.parameterAsFileOutput(parameters, self.OUTPUT, context)
        cargar   = self.parameterAsBool(parameters,   self.OPEN,    context)

        ext     = self.parameterAsExtent(parameters, self.EXTENT, context)
        crs_ext = self.parameterAsExtentCrs(parameters, self.EXTENT, context)

        # ── Reproyectar a WGS84 ───────────────────────────────────────────────
        crs_geo = QgsCoordinateReferenceSystem('EPSG:4326')
        if not crs_ext.isGeographic():
            tr   = QgsCoordinateTransform(crs_ext, crs_geo, QgsProject.instance())
            pmin = tr.transform(ext.xMinimum(), ext.yMinimum())
            pmax = tr.transform(ext.xMaximum(), ext.yMaximum())
            lon_min, lat_min = pmin.x(), pmin.y()
            lon_max, lat_max = pmax.x(), pmax.y()
        else:
            lon_min, lat_min = ext.xMinimum(), ext.yMinimum()
            lon_max, lat_max = ext.xMaximum(), ext.yMaximum()

        dem_info  = GEE_DEMS[idx_dem]
        scale_eff = escala if escala > 0 else dem_info['scale']

        # ── Estimar tamaño ────────────────────────────────────────────────────
        total_px, px_x, px_y = _estimar_pixeles(
            lon_min, lat_min, lon_max, lat_max, scale_eff)
        peso_mb = _estimar_bytes(total_px, dem_info['dtype']) / 1_048_576

        feedback.pushInfo('─' * 60)
        feedback.pushInfo(f"DEM             : {dem_info['label']}")
        feedback.pushInfo(f"Colección GEE   : {dem_info['collection']}")
        feedback.pushInfo(f"Banda / Tipo    : {dem_info['band']} / {dem_info['dtype']}")
        feedback.pushInfo(f"Escala efectiva : {scale_eff} m")
        feedback.pushInfo(f"Bbox WGS84      : [{lon_min:.5f}, {lat_min:.5f}, "
                          f"{lon_max:.5f}, {lat_max:.5f}]")
        feedback.pushInfo(f"Dimensión aprox : {px_y} x {px_x} px  "
                          f"({total_px/1e6:.2f} MP  |  ~{peso_mb:.1f} MB)")
        feedback.pushInfo('─' * 60)

        # ── Autenticar GEE ────────────────────────────────────────────────────
        ee = _ensure_ee(feedback)
        _autenticar_gee(ee, correo, proyecto, feedback)

        # ── Construir imagen ──────────────────────────────────────────────────
        region = ee.Geometry.Rectangle([lon_min, lat_min, lon_max, lat_max])
        imagen = _construir_imagen(ee, dem_info, region)

        # ── Ejecutar ──────────────────────────────────────────────────────────
        crs_output = crs_ext.authid() if crs_ext.isValid() else 'EPSG:4326'
        
        _descarga_tiled(ee, imagen, crs_output, total_px, lon_min, lat_min, lon_max, lat_max, scale_eff, output, feedback)
        
        self._output    = output
        self._cargar    = cargar
        self._via_drive = False
        self._dem_name  = dem_info['label']
        return {self.OUTPUT: output}

    def postProcessAlgorithm(self, context, feedback):
        if getattr(self, '_via_drive', False):
            return {}
        if getattr(self, '_cargar', False) and getattr(self, '_output', None):
            nombre = self._dem_name.split('—')[0].strip()
            rlayer = QgsRasterLayer(self._output, f'MDE — {nombre}')
            if rlayer.isValid():
                QgsProject.instance().addMapLayer(rlayer)
            else:
                feedback.pushWarning(
                    'El raster se descargó pero no pudo cargarse automáticamente.\n'
                    f'Cárgalo manualmente desde: {self._output}'
                )
        return {}

    def run(self):
        from qgis import processing
        processing.execAlgorithmDialog('geeswat:descargar_mde')
