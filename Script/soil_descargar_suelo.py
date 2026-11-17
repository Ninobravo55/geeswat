# -*- coding: utf-8 -*-
from .export_router_enhanced import upload_associated_files_to_drive
"""
soil_descargar_suelo.py — geeSWAT
Descarga tipo de suelo desde GEE para SWAT.

Fuentes disponibles:
  1. FAO DSMW  — FeatureCollection vectorial, campo SNUM, rasterizado a escala deseada.
                 Assets GEE publicos usados:
                   - FAO/HWSD/soil_data (o asset propio DSMW_GEO)
  2. DSoilMap  — Raster Digital Soil Open Land Map (250 m).
                 'projects/gee-unas/assets/DSOLMap'

Salidas por ejecucion:
  <carpeta_salida>/SOIL/
      <proyecto>_<fuente>_<timestamp>.tif    — raster de tipo de suelo (Int16)
      <proyecto>_<fuente>_<timestamp>_Soil_lookup.csv — tabla SNUM,SNAM para SWAT

Proyeccion: usa el EPSG del proyecto configurado en geeSWAT.

geeSWAT v1.2.3 — Geomatica Ambiental
"""
import os
import math
import time
import datetime
import csv

from qgis.PyQt.QtCore import QThread, pyqtSignal

# ── Fuentes de suelo disponibles ─────────────────────────────────────────────
SOIL_SOURCES = [
    {
        'id'   : 'FAO',
        'label': 'DSMW - Suelo FAO/UNESCO v3.6',
        'tipo' : 'vector',
        'asset': 'projects/gee-unas/assets/DSMW_GEO',  # pragma: allowlist secret
        'campo': 'SNUM',
        'campo_nombre': 'SNAM',
        'scale': 250,
        'dtype': 'Int16',
        'desc' : (
            'Corresponde a la versión digital del histórico Mapa Mundial de Suelos FAO–UNESCO, elaborado originalmente a una escala de 1:5 000 000, con el propósito de proporcionar una representación estandarizada de la distribución de los principales tipos de suelos a nivel mundial, tiene una escala de 1:5 000 000. '
            'Fuente: <a href="https://data.apps.fao.org/catalog/dataset/446ed430-8383-11db-b9b2-000d939bc5d8">https://data.apps.fao.org/catalog/dataset/446ed430-8383-11db-b9b2-000d939bc5d8</a>'
        ),
        'tabla_asset': 'projects/gee-unas/assets/SWAT/Equivalencia_Suelo_FAO',  # pragma: allowlist secret
        'nodata': -9999,
    },
    {
        'id'   : 'DSoilMap',
        'label': 'DSOLMap - Digital SoiL OpenLand Map 250m',
        'tipo' : 'raster',
        'asset': 'projects/gee-unas/assets/DSOLMap',  # pragma: allowlist secret
        'campo': 'SNUM',
        'campo_nombre': 'SNAM',
        'scale': 250,
        'dtype': 'Int16',
        'desc' : (
            'OpenLand Map es un mapa de suelos global (excepto la Antártida) derivado de métodos de aprendizaje automático y cientos de miles de observaciones del perfil del suelo. '
            'La resolución raster es de 250 m y el mapa incluye seis capas de suelo. '
            'Fuente: <a href="https://www.wateritech.com/data?brid=YWdncwFnRu3B7EkWCMR1_e4CtYO3">https://www.wateritech.com/data?brid=YWdncwFnRu3B7EkWCMR1_e4CtYO3</a>'
        ),
        'tabla_asset': 'projects/gee-unas/assets/SWAT/Equivalencia_DSOLMap',  # pragma: allowlist secret
        'campo_id_tabla': 'OBJECTID',
        'nodata': -9999,
    },
    {
        'id'   : 'HWSD2',
        'label': 'HWSD v2.0 Harmonized World Soil Database',
        'tipo' : 'raster',
        'asset': 'projects/sat-io/open-datasets/FAO/HWSD_V2_SMU',
        'campo': 'HWSD2_ID',
        'campo_nombre': 'SNAM',
        'scale': 250,
        'dtype': 'Int32',
        'desc' : (
            'HWSD v2.0 amplía sus versiones anteriores al incluir datos de varias bases de datos nacionales de suelos, '
            'ofrecer atributos de suelo detallados para siete capas de suelo diferentes (un aumento significativo con respecto a '
            'las dos capas en HWSD v1.2) y adoptar un sistema de referencia de suelo consistente que combina las normas FAO 1990 '
            'con la Base de Referencia Mundial para los Recursos del Suelo. '
            'Fuente: <a href="https://www.fao.org/soils-portal/data-hub/soil-maps-and-databases/harmonized-world-soil-database-v20/en/">https://www.fao.org/soils-portal/data-hub/soil-maps-and-databases/harmonized-world-soil-database-v20/en/</a>'
        ),
        'tabla_asset': 'projects/gee-unas/assets/SWAT/Equivalencia_HWSD2',  # pragma: allowlist secret
        'campo_id_tabla': 'SOIL_ID',
        'nodata': -9999,
    },
    {
        'id'   : 'HWSD12',
        'label': 'HWSD v1.21 Harmonized World Soil Database',
        'tipo' : 'raster',
        'asset': 'projects/gee-unas/assets/SWAT/HWSD_v12',
        'campo': 'HWSD12_ID',
        'campo_nombre': 'SNAM',
        'scale': 250,
        'dtype': 'Int32',
        'desc' : (
            'Compilada por la FAO y el IIASA (v1.21) es de 2012. El mapa viene en una resolución horizontal de 30 segundos de arco (1 km) y dos capas de suelo: capa superior del suelo (0-30 cm) y subsuelo (30-100 cm). '
            'Fuente: <a href="https://www.wateritech.com/data?brid=YWdncwFnRu3B7EkWCMR1_e4CtYO3">https://www.wateritech.com/data?brid=YWdncwFnRu3B7EkWCMR1_e4CtYO3</a>'
        ),
        'tabla_asset': 'projects/gee-unas/assets/SWAT/Equivalencia_HWSD12',  # pragma: allowlist secret
        'campo_id_tabla': 'SOIL_ID',
        'nodata': -9999,
    },
]
SOIL_LABELS = [s['label'] for s in SOIL_SOURCES]

GDRIVE_FOLDER  = 'GEE_geeSWAT'
LIMITE_MP      = 10_000_000
POLL_INTERVAL  = 12


# ═════════════════════════════════════════════════════════════════════════════
# WORKER THREAD — Descarga Suelo + Tabla lookup
# ═════════════════════════════════════════════════════════════════════════════
class SoilWorker(QThread):
    log      = pyqtSignal(str)
    progreso = pyqtSignal(int)
    ok       = pyqtSignal(str, str, bool)  # (ruta_raster, ruta_csv, via_drive)
    error    = pyqtSignal(str)

    def __init__(self, source_info, lon_min, lat_min, lon_max, lat_max,
                 scale_eff, crs_epsg, output_raster, output_csv,
                 project_id):
        super().__init__()
        self.source_info   = source_info
        self.lon_min       = lon_min
        self.lat_min       = lat_min
        self.lon_max       = lon_max
        self.lat_max       = lat_max
        self.scale_eff     = scale_eff
        self.crs_epsg      = crs_epsg          # ej. 32718
        self.output_raster = output_raster
        self.output_csv    = output_csv
        self.project_id    = project_id
        self._cancelar     = False

    def cancelar(self):
        self._cancelar = True

    def run(self):
        try:
            import ee
            # ── Conectar ──────────────────────────────────────────────────────
            kwargs = {}
            if self.project_id.strip():
                kwargs['project'] = self.project_id.strip()
            self.log.emit(f"Conectando GEE — proyecto: {self.project_id or '(local)'}")
            ee.Initialize(opt_url='https://earthengine.googleapis.com', **kwargs)
            self.log.emit("GEE conectado.")

            src    = self.source_info
            region = ee.Geometry.Rectangle(
                [self.lon_min, self.lat_min, self.lon_max, self.lat_max])
            crs_str = f'EPSG:{self.crs_epsg}' if self.crs_epsg else 'EPSG:4326'

            # ── Construir imagen de suelo ──────────────────────────────────────
            self.log.emit(f"Fuente        : {src['label']}")
            self.log.emit(f"Asset GEE     : {src['asset']}")
            self.log.emit(f"Escala        : {self.scale_eff} m")
            self.log.emit(f"Proyeccion    : {crs_str}")

            if src['tipo'] == 'vector':
                self.log.emit(f"Rasterizando FeatureCollection {src['id']}...")
                fc     = ee.FeatureCollection(src['asset'])
                imagen = (fc
                    .reduceToImage(
                        properties=[src['campo']],
                        reducer=ee.Reducer.first()
                    )
                    .rename([src['campo']])
                )
            else:
                self.log.emit(f"Cargando raster {src['id']}...")
                imagen = ee.Image(src['asset'])
                # Para assets con multiples bandas, seleccionar la correcta
                if src['id'] == 'HWSD2':
                    imagen = imagen.select([src['campo']])
                else:
                    imagen = imagen.select([0])
                imagen = imagen.rename([src['campo']])

            # Convertir al tipo de dato correcto
            if src.get('dtype') == 'Int32':
                imagen = imagen.toInt32()
            else:
                imagen = imagen.toInt16()

            imagen = (imagen
                .reproject(crs=crs_str, scale=self.scale_eff)
                .unmask(src['nodata'])
                .clip(region)
            )
            self.progreso.emit(10)

            # ── Decidir: descarga directa o Drive ─────────────────────────────
            lat_med = (self.lat_min + self.lat_max) / 2.0
            cos_lat = math.cos(math.radians(lat_med))
            ancho_m = (self.lon_max - self.lon_min) * 111320.0 * cos_lat
            alto_m  = (self.lat_max - self.lat_min) * 111320.0
            total_px= max(1, int(ancho_m / self.scale_eff)) * \
                      max(1, int(alto_m  / self.scale_eff))
            peso_mb = total_px * 2 / 1_048_576  # Int16 = 2 bytes/px

            self.log.emit(
                f"Dimension aprox: {total_px/1e6:.2f} MP  |  ~{peso_mb:.1f} MB")

            if self._cancelar:
                return

            self._descarga_tiled(ee, imagen, crs_str, total_px)
            self.progreso.emit(80)
            self._generar_csv(ee, imagen, region, src)
            self.progreso.emit(100)
            self.ok.emit(self.output_raster, self.output_csv, False)

        except Exception as ex:
            self.error.emit(str(ex))

    # ── Descarga directa o Tiling ──────────────────────────────────────────────────────
    def _descarga_tiled(self, ee, imagen, crs_str, total_px):
        import math, os
        try:
            from osgeo import gdal
        except ImportError:
            raise RuntimeError("La libreria GDAL no esta disponible en tu entorno QGIS.")

        limite_tile_px = 2_000_000  # 2 MP per tile to be very safe
        num_tiles = math.ceil(total_px / limite_tile_px)

        if num_tiles <= 1:
            self.log.emit("Tamano dentro del limite seguro -> descarga en un solo bloque.")
            region = ee.Geometry.Rectangle([self.lon_min, self.lat_min, self.lon_max, self.lat_max])
            self._descarga_bloque(ee, imagen, region, crs_str, self.output_raster)
            return

        n_cols = max(1, math.ceil(math.sqrt(num_tiles * ((self.lon_max - self.lon_min) / max(1e-9, self.lat_max - self.lat_min)))))
        n_rows = max(1, math.ceil(num_tiles / n_cols))
        
        total_tiles = n_cols * n_rows
        self.log.emit(f"Area grande. Dividiendo en {n_cols}x{n_rows} = {total_tiles} cuadrantes para evitar limites de GEE...")
        
        lon_step = (self.lon_max - self.lon_min) / n_cols
        lat_step = (self.lat_max - self.lat_min) / n_rows
        
        temp_files = []
        k = 0
        
        for i in range(n_cols):
            for j in range(n_rows):
                if self._cancelar:
                    raise RuntimeError("Cancelado por el usuario.")
                k += 1
                self.log.emit(f"Descargando cuadrante {k}/{total_tiles}...")
                
                sub_lon_min = self.lon_min + i * lon_step
                sub_lon_max = self.lon_min + (i + 1) * lon_step
                sub_lat_min = self.lat_min + j * lat_step
                sub_lat_max = self.lat_min + (j + 1) * lat_step
                
                sub_region = ee.Geometry.Rectangle([sub_lon_min, sub_lat_min, sub_lon_max, sub_lat_max])
                temp_file = self.output_raster.replace('.tif', f'_tile_{i}_{j}.tif')
                self._descarga_bloque(ee, imagen, sub_region, crs_str, temp_file)
                temp_files.append(temp_file)
                self.progreso.emit(10 + int((k / total_tiles) * 60))
        
        if self._cancelar:
            raise RuntimeError("Cancelado por el usuario.")

        self.log.emit("Uniendo cuadrantes (Mosaico con GDAL)...")
        vrt_path = self.output_raster.replace('.tif', '_temp.vrt')
        gdal.BuildVRT(vrt_path, temp_files)
        
        warp_options = gdal.WarpOptions(format='GTiff', creationOptions=['COMPRESS=LZW'])
        gdal.Warp(self.output_raster, vrt_path, options=warp_options)
        
        self.log.emit("Limpiando archivos temporales...")
        if os.path.exists(vrt_path):
            os.remove(vrt_path)
        for tf in temp_files:
            if os.path.exists(tf):
                os.remove(tf)

    def _descarga_bloque(self, ee, imagen, region, crs_str, out_path):
        import zipfile, shutil, requests, os
        from urllib.parse import urlparse
        
        try:
            url = imagen.getDownloadURL({
                'scale' : self.scale_eff,
                'region': region,
                'format': 'GEO_TIFF',
                'crs'   : crs_str,
            })
        except Exception as ex:
            raise RuntimeError(f"Error al generar URL en GEE: {ex}")

        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https'):
            raise RuntimeError(f"URL invalida: esquema '{parsed.scheme}'")

        tmp = out_path + '_tmp.bin'
        try:
            with requests.get(url, stream=True, timeout=180) as r:
                r.raise_for_status()
                with open(tmp, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=16384):
                        if self._cancelar:
                            raise RuntimeError("Cancelado por el usuario.")
                        if chunk:
                            f.write(chunk)
        except Exception as ex:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise RuntimeError(f"Error HTTP: {ex}")

        if zipfile.is_zipfile(tmp):
            with zipfile.ZipFile(tmp, 'r') as zf:
                tifs = [n for n in zf.namelist() if n.lower().endswith('.tif')]
                if not tifs:
                    raise RuntimeError("El ZIP no contiene archivos .tif")
                extracted = zf.extract(tifs[0], os.path.dirname(out_path) or '.')
            shutil.move(extracted, out_path)
            os.remove(tmp)
        else:
            shutil.move(tmp, out_path)

    # ── Generar CSV Soil_lookup ───────────────────────────────────────────────
    def _generar_csv(self, ee, imagen, region, src):
        """
        Extrae los IDs unicos del raster, cruza con la tabla de equivalencia GEE
        y genera el CSV Soil_lookup.csv compatible con SWAT.

        Columnas: SNUM, SNAM
        """
        try:
            self.log.emit("Generando tabla Soil_lookup.csv...")

            # ── IDs unicos presentes en el raster recortado ───────────────────
            ids_list = (
                imagen
                .reduceRegion(
                    reducer   = ee.Reducer.toList(),
                    geometry  = region,
                    scale     = self.scale_eff,
                    maxPixels = 1e10,
                    tileScale = 16,
                    bestEffort= True,
                )
                .get(imagen.bandNames().get(0))
            )
            ids_uniq = ee.List(ids_list).distinct()
            ids_py   = [int(x) for x in ids_uniq.getInfo()
                        if x != src['nodata'] and x is not None]
            ids_py   = sorted(set(ids_py))
            self.log.emit(f"IDs unicos en AOI: {len(ids_py)}")

            # ── Intentar descargar tabla de equivalencia de GEE o Local ────────
            lookup = {}
            try:
                if 'tabla_local' in src and os.path.exists(src['tabla_local']):
                    self.log.emit("Leyendo tabla de equivalencia local...")
                    with open(src['tabla_local'], 'r', encoding='utf-8-sig') as ft:
                        reader = csv.DictReader(ft)
                        for row in reader:
                            sid_str = row.get('SOIL_ID') or row.get('OBJECTID') or row.get('SNUM')
                            snam_str = row.get('SNAM') or row.get('SOIL_NAME')
                            if sid_str and snam_str:
                                try:
                                    sid = int(float(sid_str))
                                    if sid in ids_py:
                                        lookup[sid] = snam_str.strip()
                                except ValueError:
                                    pass
                    self.log.emit(f"Tabla local leida: {len(lookup)} entradas coincidentes")
                elif 'tabla_asset' in src:
                    tabla_fc = ee.FeatureCollection(src['tabla_asset'])
                    # Campo ID en tabla
                    campo_id   = src.get('campo_id_tabla', 'OBJECTID')
                    campo_nom  = src.get('campo_nombre', 'SNAM')
                    filas = tabla_fc.select([campo_id, campo_nom]).getInfo()
                    for feat in filas.get('features', []):
                        props = feat.get('properties', {})
                        # El campo OBJECTID puede venir con BOM en FAO
                        sid  = None
                        snam = str(props.get(campo_nom, ''))
                        for k, v in props.items():
                            k_clean = k.lstrip('\ufeff').strip()
                            if k_clean.upper() in (campo_id.upper(), 'SOIL_ID',
                                                   'OBJECTID', 'SNUM', 'ID'):
                                try:
                                    sid = int(float(v))
                                except Exception:  # nosec
                                    pass
                                break
                        if sid is not None and sid in ids_py:
                            lookup[sid] = snam
                    self.log.emit(
                        f"Tabla GEE descargada: {len(lookup)} entradas coincidentes")
            except Exception as ex:
                self.log.emit(
                    f"Tabla de equivalencia no disponible ({ex}). "
                    "CSV generado solo con IDs numericos.")

            # ── Escribir CSV ──────────────────────────────────────────────────
            with open(self.output_csv, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(['SNUM', 'SNAM'])
                for sid in ids_py:
                    if src['id'] == 'HWSD2':
                        fallback = f"HWSD2_{sid}"
                    elif src['id'] == 'HWSD12':
                        fallback = f"HWSD12_{sid}"
                    else:
                        fallback = f"SOIL_{sid:04d}"
                    snam = lookup.get(sid, fallback)
                    writer.writerow([sid, snam])

            self.log.emit(f"Soil_lookup.csv guardado: {self.output_csv}")
            return True

        except Exception as ex:
            self.log.emit(f"Error al generar CSV: {ex}")
            return False
