# -*- coding: utf-8 -*-
"""
cn_descargar_cn.py — geeSWAT
Descarga de Curva Número (GCN250) desde GEE para SWAT.

Fuente disponible:
  - jaafarhadi/GCN250/GCN250Average (Escala nativa: 250 m)

Salidas por ejecución:
  <carpeta_salida>/CN/
      <proyecto>_<fuente>_<timestamp>.tif
      <proyecto>_<fuente>_<timestamp>_Tabla_CN.csv (Opcional, si se proporciona subcuencas)
"""

import os
import math
import time
import datetime
import csv
import json

from qgis.PyQt.QtCore import QThread, pyqtSignal

# ── Fuentes disponibles ───────────────────────────────────────────────────────
CN_SOURCES = [
    {
        'id'        : 'GCN250',
        'label'     : 'GCN250 Average (250 m)',
        'collection': 'users/jaafarhadi/GCN250/GCN250Average',
        'banda'     : 'b1', # The single band of the image
        'scale'     : 250,
        'desc'      : (
            'Curva Número promedio global a 250m de resolución espacial. '
            'Basado en la cobertura terrestre de ESA CCI y datos globales de suelos.'
        ),
    }
]

GDRIVE_FOLDER = 'GEE_geeSWAT'
LIMITE_MP     = 10_000_000
POLL_INTERVAL = 12


# ═════════════════════════════════════════════════════════════════════════════
# WORKER THREAD
# ═════════════════════════════════════════════════════════════════════════════
class CNWorker(QThread):
    log      = pyqtSignal(str)
    progreso = pyqtSignal(int)
    ok       = pyqtSignal(str, str, bool)   # (ruta_tif, ruta_csv, via_drive)
    error    = pyqtSignal(str)

    def __init__(self, source_info, lon_min, lat_min, lon_max, lat_max,
                 scale_eff, crs_epsg, output_raster, output_csv, project_id, subcuencas_data):
        super().__init__()
        self.source_info     = source_info
        self.lon_min         = lon_min
        self.lat_min         = lat_min
        self.lon_max         = lon_max
        self.lat_max         = lat_max
        self.scale_eff       = scale_eff
        self.crs_epsg        = crs_epsg
        self.output_raster   = output_raster
        self.output_csv      = output_csv
        self.project_id      = project_id
        self.subcuencas_data = subcuencas_data # Dict: {"type": "asset", "id": "..."} o {"type": "geojson", "data": {...}}
        self._cancelar       = False

    def cancelar(self):
        self._cancelar = True

    def run(self):
        try:
            import ee
            kwargs = {}
            if self.project_id.strip():
                kwargs['project'] = self.project_id.strip()
            self.log.emit(f"Conectando GEE — proyecto: {self.project_id or '(local)'}")
            ee.Initialize(opt_url='https://earthengine.googleapis.com', **kwargs)
            self.log.emit("GEE conectado.")

            src     = self.source_info
            region  = ee.Geometry.Rectangle(
                [self.lon_min, self.lat_min, self.lon_max, self.lat_max])
            crs_str = f'EPSG:{self.crs_epsg}' if self.crs_epsg else 'EPSG:4326'

            self.log.emit(f"Fuente        : {src['label']}")
            self.log.emit(f"Asset GEE     : {src['collection']}")
            self.log.emit(f"Escala        : {self.scale_eff} m")
            self.log.emit(f"Proyección    : {crs_str}")

            # Cargar imagen GCN250
            imagen_raw = ee.Image(src['collection']).select(0)
            imagen_raw = imagen_raw.clip(region).reproject(crs=crs_str, scale=self.scale_eff)
            
            # ── Eliminar valores fuera de rango CN válido (1-100) ──
            # multiply(valido) pone 0 donde no es válido (incluye 255)
            # .unmask(0) fuerza que los píxeles enmascarados por clip() también sean 0
            valido = imagen_raw.gte(1).And(imagen_raw.lte(100))
            imagen_limpia = imagen_raw.multiply(valido).unmask(0).toByte()
            self.log.emit("Valores fuera de [1-100] eliminados (convertidos a 0).")

            self.progreso.emit(10)

            # Estimar tamaño
            cos_lat = math.cos(math.radians((self.lat_min + self.lat_max) / 2.0))
            ancho_m = (self.lon_max - self.lon_min) * 111320.0 * cos_lat
            alto_m  = (self.lat_max - self.lat_min) * 111320.0
            total_px = max(1, int(ancho_m / self.scale_eff)) * \
                       max(1, int(alto_m  / self.scale_eff))
            peso_mb  = total_px * 2 / 1_048_576 # Int16 = 2 bytes
            self.log.emit(
                f"Dimensión aprox: {total_px/1e6:.2f} MP  |  ~{peso_mb:.1f} MB")

            if self._cancelar:
                return

            usar_drive = total_px > LIMITE_MP
            
            # Construir FeatureCollection de subcuencas si existe
            subcuencas_fc = None
            if self.subcuencas_data:
                if self.subcuencas_data.get('type') == 'asset':
                    asset_id = self.subcuencas_data.get('id')
                    self.log.emit(f"Cargando subcuencas desde Asset: {asset_id}")
                    subcuencas_fc = ee.FeatureCollection(asset_id)
                elif self.subcuencas_data.get('type') == 'geojson':
                    self.log.emit("Cargando subcuencas desde capa vectorial local (GeoJSON)...")
                    fc_json = self.subcuencas_data.get('data')
                    subcuencas_fc = ee.FeatureCollection(fc_json)

            if usar_drive:
                self.log.emit(
                    f"Área grande ({total_px/1e6:.1f} MP) "
                    f"-> exportando a Google Drive / {GDRIVE_FOLDER}")
                
                # Exportar Raster y Tabla usando la imagen limpia
                self._exportar_drive(ee, imagen_limpia, region, crs_str, subcuencas_fc)
            else:
                self.log.emit(f"Tamaño dentro del límite -> descarga directa")
                self._descarga_directa(ee, imagen_limpia, region, crs_str)
                self.progreso.emit(70)
                
                # Exportar CSV usando la imagen limpia
                csv_generado = False
                if subcuencas_fc:
                    csv_generado = self._generar_csv_local(ee, imagen_limpia, subcuencas_fc)
                self.progreso.emit(100)
                
                self.ok.emit(self.output_raster, self.output_csv if csv_generado else '', False)

        except Exception as ex:
            self.error.emit(str(ex))

    # ── Descarga directa ──────────────────────────────────────────────────────
    def _descarga_directa(self, ee, imagen, region, crs_str):
        import zipfile, requests
        from urllib.parse import urlparse
        self.log.emit("Generando URL de descarga GEE...")
        try:
            url = imagen.getDownloadURL({
                'scale' : self.scale_eff,
                'region': region,
                'format': 'GEO_TIFF',
                'crs'   : crs_str,
            })
        except Exception as ex:
            raise RuntimeError(f"Error al generar URL: {ex}")
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https'):
            raise RuntimeError(f"URL inválida: esquema '{parsed.scheme}'")
        self.log.emit("Descargando GeoTIFF de Curva Número...")
        tmp = self.output_raster + '_tmp.bin'
        try:
            with requests.get(url, stream=True, timeout=180) as r:
                r.raise_for_status()
                total = int(r.headers.get('content-length', 0))
                desc  = 0
                with open(tmp, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=16384):
                        if self._cancelar:
                            raise RuntimeError("Cancelado por el usuario.")
                        if chunk:
                            f.write(chunk)
                            desc += len(chunk)
                            if total > 0:
                                self.progreso.emit(
                                    10 + min(int(desc * 50 / total), 50))
        except Exception as ex:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise RuntimeError(f"Error HTTP: {ex}")
        if zipfile.is_zipfile(tmp):
            self.log.emit("Descomprimiendo ZIP...")
            with zipfile.ZipFile(tmp, 'r') as zf:
                tifs = [n for n in zf.namelist() if n.lower().endswith('.tif')]
                if not tifs:
                    raise RuntimeError("El ZIP no contiene archivos .tif")
                extracted = zf.extract(
                    tifs[0], os.path.dirname(self.output_raster) or '.')
            import shutil as _sh
            _sh.move(extracted, self.output_raster)
            os.remove(tmp)
        else:
            import shutil as _sh
            _sh.move(tmp, self.output_raster)
        self.log.emit(f"Raster guardado: {self.output_raster}")

    # ── Exportar a GEE Drive ─────────────────────────────────────────────────
    def _exportar_drive(self, ee, imagen, region, crs_str, subcuencas_fc):
            
        task_name = f'GEE_GCN250_{datetime.datetime.now().strftime("%Y%m%d_%H%M%S")}'
        self.output_raster = f"Drive/{GDRIVE_FOLDER}/{task_name}.tif"

        # 1) Iniciar tarea de la imagen
        task_img = ee.batch.Export.image.toDrive(
            image=imagen,
            description=task_name,
            folder=GDRIVE_FOLDER,
            fileNamePrefix=task_name,
            region=region,
            scale=self.scale_eff,
            crs=crs_str,
            maxPixels=1e13
        )
        task_img.start()
        
        # 2) Iniciar tarea de la tabla (si hay subcuencas)
        task_table = None
        if subcuencas_fc:
            self.log.emit("Calculando CN promedio por subcuenca...")
            cn_por_subcuenca = imagen.reduceRegions(
                collection = subcuencas_fc,
                reducer = ee.Reducer.mean(),
                scale = self.scale_eff
            )
            task_table = ee.batch.Export.table.toDrive(
                collection = cn_por_subcuenca,
                description = f"{task_name}_Tabla",
                folder = GDRIVE_FOLDER,
                fileFormat = "CSV"
            )
            task_table.start()
            self.log.emit(f"Task Tabla iniciado: {task_table.id}")

        self.log.emit("Monitorea en: https://code.earthengine.google.com/tasks")
        
        ESTADOS_FIN = {'COMPLETED', 'FAILED', 'CANCELLED', 'CANCEL_REQUESTED'}
        ultimo_img = ''
        ultimo_tbl = ''
        espera = 0
        spinner = ['|', '/', '-', '\\']
        idx = 0
        
        while True:
            time.sleep(POLL_INTERVAL)
            espera += POLL_INTERVAL
            if self._cancelar:
                task_img.cancel()
                if task_table:
                    task_table.cancel()
                raise RuntimeError("Cancelado por el usuario.")
            
            try:
                st_img = task_img.status()
                est_img = st_img.get('state', 'UNKNOWN')
                prog_img = st_img.get('progress', 0.0)
                
                est_tbl = 'COMPLETED'
                if task_table:
                    st_tbl = task_table.status()
                    est_tbl = st_tbl.get('state', 'UNKNOWN')
            except Exception as ex:
                self.log.emit(f"No se pudo consultar estado: {ex}")
                continue
                
            if est_img != ultimo_img:
                self.log.emit(f"Estado Imagen GEE: {est_img}")
                ultimo_img = est_img
            if task_table and est_tbl != ultimo_tbl:
                self.log.emit(f"Estado Tabla GEE: {est_tbl}")
                ultimo_tbl = est_tbl
                
            if prog_img and prog_img > 0:
                self.progreso.emit(int(prog_img * 100))
                
            m, s = espera // 60, espera % 60
            self.log.emit(
                f"{spinner[idx % 4]} "
                f"IMG: {'%.1f%%' % (prog_img*100) if prog_img else 'procesando...'} "
                f"| TBL: {est_tbl if task_table else 'N/A'} | {m}m {s}s")
            idx += 1
            
            if est_img in ESTADOS_FIN and est_tbl in ESTADOS_FIN:
                break
                
        if est_img == 'COMPLETED':
            self.log.emit(f"Exportación de imagen completada -> Drive/{GDRIVE_FOLDER}/{task_name}.tif")
            if task_table and est_tbl == 'COMPLETED':
                self.log.emit(f"Exportación de tabla completada -> Drive/{GDRIVE_FOLDER}/{task_name}_Tabla.csv")
                self.ok.emit(self.output_raster, self.output_csv, True)
            else:
                self.ok.emit(self.output_raster, '', True)
            self.progreso.emit(100)
        else:
            err = st_img.get('error_message', 'Error desconocido')
            raise RuntimeError(f"GEE Drive falló [{est_img}]: {err}")

    # ── Generar CSV localmente ───────────────────────────────────────────────
    def _generar_csv_local(self, ee, imagen, subcuencas_fc):
        try:
            self.log.emit("Calculando CN promedio por subcuenca (local)...")
            cn_por_subcuenca = imagen.reduceRegions(
                collection = subcuencas_fc,
                reducer = ee.Reducer.mean(),
                scale = self.scale_eff
            )
            
            self.log.emit("Descargando atributos de subcuencas...")
            # Descargamos los atributos a la memoria RAM local.
            fc_info = cn_por_subcuenca.getInfo()
            features = fc_info.get('features', [])
            
            if not features:
                self.log.emit("No se encontraron features en la colección.")
                return False
                
            self.log.emit("Guardando CSV de Curva Número...")
            
            # Extraer nombres de las propiedades de la primera feature
            if features:
                fieldnames = list(features[0]['properties'].keys())
                # Asegurar que 'mean' u otras de interés estén
                with open(self.output_csv, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
                    for feat in features:
                        writer.writerow(feat['properties'])
                        
            self.log.emit(f"Tabla CSV guardada: {self.output_csv}")
            return True
        except Exception as ex:
            self.log.emit(f"Error al generar CSV local: {ex}")
            return False
