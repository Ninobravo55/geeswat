import os
import csv
import traceback
import math

from qgis.PyQt.QtCore import QThread, pyqtSignal
from qgis.core import (
    QgsVectorLayer,
    QgsVectorFileWriter,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProject,
    QgsField,
    QgsFields,
    QgsWkbTypes
)
from qgis.PyQt.QtCore import QVariant

try:
    import ee
except ImportError:
    ee = None

MODELS = [
    "ACCESS-CM2", "ACCESS-ESM1-5", "BCC-CSM2-MR", "CESM2", "CESM2-WACCM", "CMCC-CM2-SR5", "CMCC-ESM2", "CNRM-CM6-1",
    "CNRM-ESM2-1", "CanESM5", "EC-Earth3", "EC-Earth3-Veg-LR", "FGOALS-g3", "GFDL-CM4", "GFDL-ESM4", "GISS-E2-1-G",
    "HadGEM3-GC31-LL", "HadGEM3-GC31-MM", "IITM-ESM", "INM-CM4-8", "INM-CM5-0", "IPSL-CM6A-LR", "KACE-1-0-G",
    "KIOST-ESM", "MIROC-ES2L", "MIROC6", "MPI-ESM1-2-HR", "MPI-ESM1-2-LR", "MRI-ESM2-0", "NESM3", "NorESM2-LM",
    "NorESM2-MM", "TaiESM1", "UKESM1-0-LL"
]

SCENARIOS = {
    "historical": "historical",
    "ssp245": "ssp245",
    "ssp585": "ssp585"
}

class CambioClimaticoWorker(QThread):
    progreso = pyqtSignal(int)
    log = pyqtSignal(str)
    ok = pyqtSignal(str)
    error = pyqtSignal(str)
    stations_generated = pyqtSignal(str)

    def __init__(self, output_dir, geom_json, model_name, scenario, date_start, date_end, estaciones_layer=None, grid_spacing=None, vars_to_write=None):
        super().__init__()
        self.output_dir = output_dir
        self.geom_json = geom_json
        import json
        self.geom_dict = json.loads(self.geom_json)
        self.model_name = model_name
        self.scenario = scenario
        self.date_start = date_start
        self.date_end = date_end
        self.estaciones_layer = estaciones_layer
        self.grid_spacing = grid_spacing
        self.vars_to_write = vars_to_write if vars_to_write else ["Prec", "Temp", "HR", "Wind", "Solar"]
        self._cancelar = False

    def cancelar(self):
        self._cancelar = True

    def run(self):
        try:
            self.log.emit(f"Iniciando descarga CMIP6 (Modelo: {self.model_name}, Escenario: {self.scenario})...")
            
            estaciones = self._obtener_estaciones()
            if not estaciones:
                raise Exception("No se obtuvieron estaciones.")

            n_estaciones = len(estaciones)
            self.log.emit(f"Se procesarán {n_estaciones} estaciones.")

            vars_to_write = self.vars_to_write
            carpetas = {}
            for var_type in vars_to_write:
                var_dir = os.path.join(self.output_dir, f"{var_type}_SWAT")
                os.makedirs(var_dir, exist_ok=True)
                carpetas[var_type] = var_dir
            
            self._escribir_listas_estaciones(estaciones, carpetas)

            col = ee.ImageCollection('NASA/GDDP-CMIP6') \
                .filter(ee.Filter.eq('model', self.model_name)) \
                .filter(ee.Filter.eq('scenario', self.scenario))
                
            start_d = ee.Date(self.date_start)
            end_d = ee.Date(self.date_end)
            
            matriz_folder = os.path.join(self.output_dir, "Matriz")
            os.makedirs(matriz_folder, exist_ok=True)
            matrices_data = {}
            
            for i, est in enumerate(estaciones):
                if self._cancelar:
                    self.log.emit("Proceso cancelado por el usuario.")
                    return
                
                self.log.emit(f"Procesando estación {i+1}/{n_estaciones} ({est['NAME']})...")
                pt_geom = ee.Geometry.Point([est["LONG"], est["LAT"]])

                df_est = self._extraer_serie(col, pt_geom, start_d, end_d)
                
                self._escribir_txt_swat(df_est, est, carpetas, vars_to_write, self.date_start.replace("-", ""))

                # Poblar diccionario para matriz
                for row in df_est:
                    d = row["date"]
                    if "Prec" in row and "Prec" in vars_to_write:
                        if "prec" not in matrices_data: matrices_data["prec"] = {}
                        if d not in matrices_data["prec"]: matrices_data["prec"][d] = {}
                        matrices_data["prec"][d][est["NAME"]] = row["Prec"]
                        
                    if "Temp" in row and "Temp" in vars_to_write:
                        tmax, tmin = row["Temp"].split(",")
                        if "tmax" not in matrices_data: matrices_data["tmax"] = {}
                        if d not in matrices_data["tmax"]: matrices_data["tmax"][d] = {}
                        matrices_data["tmax"][d][est["NAME"]] = tmax
                        
                        if "tmin" not in matrices_data: matrices_data["tmin"] = {}
                        if d not in matrices_data["tmin"]: matrices_data["tmin"][d] = {}
                        matrices_data["tmin"][d][est["NAME"]] = tmin
                        
                    if "HR" in row and "HR" in vars_to_write:
                        if "humidity" not in matrices_data: matrices_data["humidity"] = {}
                        if d not in matrices_data["humidity"]: matrices_data["humidity"][d] = {}
                        matrices_data["humidity"][d][est["NAME"]] = row["HR"]
                        
                    if "Wind" in row and "Wind" in vars_to_write:
                        if "wind" not in matrices_data: matrices_data["wind"] = {}
                        if d not in matrices_data["wind"]: matrices_data["wind"][d] = {}
                        matrices_data["wind"][d][est["NAME"]] = row["Wind"]
                        
                    if "Solar" in row and "Solar" in vars_to_write:
                        if "radiation" not in matrices_data: matrices_data["radiation"] = {}
                        if d not in matrices_data["radiation"]: matrices_data["radiation"][d] = {}
                        matrices_data["radiation"][d][est["NAME"]] = row["Solar"]

                prog = int((i + 1) / n_estaciones * 100)
                self.progreso.emit(prog)

            # Exportar Matrices CSV
            self.log.emit("Exportando matrices consolidadas (CSV)...")
            station_names = [e["NAME"] for e in estaciones]
            
            for var_key, date_dict in matrices_data.items():
                csv_path = os.path.join(matriz_folder, f"{var_key}_all_stations.csv")
                with open(csv_path, 'w', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow(["Date"] + station_names)
                    
                    sorted_dates = sorted(date_dict.keys())
                    for d in sorted_dates:
                        row_vals = [d]
                        for s_name in station_names:
                            row_vals.append(date_dict[d].get(s_name, "-99"))
                        writer.writerow(row_vals)
            
            self.log.emit("Descarga de Cambio Climático completada exitosamente.")
            self.ok.emit(self.output_dir)

        except Exception as e:
            err = traceback.format_exc()
            self.log.emit(f"Error: {str(e)}\n{err}")
            self.error.emit(str(e))

    def _obtener_estaciones(self):
        if self.estaciones_layer:
            estaciones = []
            features = self.estaciones_layer.getFeatures()
            crs_src = self.estaciones_layer.crs()
            crs_wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
            transform = QgsCoordinateTransform(crs_src, crs_wgs84, QgsProject.instance())

            dem = ee.Image("USGS/SRTMGL1_003").select("elevation")

            for i, feat in enumerate(features):
                geom = feat.geometry()
                geom.transform(transform)
                pt = geom.asPoint()
                
                elev = feat.attribute("ELEVATION") if "ELEVATION" in feat.fields().names() else None
                if elev is None or str(elev).strip() == "":
                    pt_ee = ee.Geometry.Point([pt.x(), pt.y()])
                    elev_dict = dem.reduceRegion(ee.Reducer.mean(), pt_ee, 30).getInfo()
                    elev = elev_dict.get('elevation', -9999)

                name = feat.attribute("NAME") if "NAME" in feat.fields().names() else f"Est{i+1}"
                _id = feat.attribute("ID") if "ID" in feat.fields().names() else i+1

                estaciones.append({
                    "ID": _id,
                    "NAME": name,
                    "LAT": pt.y(),
                    "LONG": pt.x(),
                    "ELEVATION": int(elev) if elev else -9999
                })
            return estaciones

        elif self.grid_spacing:
            lonlat = ee.Image.pixelLonLat()
            grid = lonlat.sample(
                region=ee.Geometry(self.geom_dict),
                scale=self.grid_spacing,
                geometries=True
            )
            points = grid.filterBounds(ee.Geometry(self.geom_dict))
            
            dem = ee.Image("USGS/SRTMGL1_003").select("elevation")
            
            # Garantizar que la malla caiga sobre pixeles válidos del producto climático (CMIP6 suele tener vacíos en oceanos)
            first_img = ee.ImageCollection('NASA/GDDP-CMIP6').first()
            if first_img:
                clim_mask = ee.Image(ee.Algorithms.If(first_img, first_img.select(0).mask(), ee.Image(1)))
                dem = dem.updateMask(clim_mask)

            points_elev = dem.sampleRegions(
                collection=points,
                scale=30,
                geometries=True
            )
            
            self.log.emit("Obteniendo puntos generados en GEE...")
            features = points_elev.getInfo()["features"]
            
            estaciones = []
            for i, f in enumerate(features):
                coords = f["geometry"]["coordinates"]
                elev = f["properties"].get("elevation", -9999)
                estaciones.append({
                    "ID": i+1,
                    "NAME": f"Est{i+1}",
                    "LAT": coords[1],
                    "LONG": coords[0],
                    "ELEVATION": int(elev)
                })
            
            out_shp = self._crear_shapefile_estaciones(estaciones)
            if out_shp:
                self.stations_generated.emit(out_shp)
                
            return estaciones
        else:
            return []

    def _crear_shapefile_estaciones(self, estaciones):
        out_shp = os.path.join(self.output_dir, "Estaciones_Virtuales_CC.shp")
        out_csv = os.path.join(self.output_dir, "Estaciones_Virtuales_CC.csv")
        
        with open(out_csv, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=["ID", "NAME", "LAT", "LONG", "ELEVATION"])
            writer.writeheader()
            writer.writerows(estaciones)
            
        fields = QgsFields()
        fields.append(QgsField("ID", QVariant.Int))
        fields.append(QgsField("NAME", QVariant.String))
        fields.append(QgsField("LAT", QVariant.Double))
        fields.append(QgsField("LONG", QVariant.Double))
        fields.append(QgsField("ELEVATION", QVariant.Int))
        
        writer = QgsVectorFileWriter(out_shp, "UTF-8", fields, QgsWkbTypes.Point, QgsCoordinateReferenceSystem("EPSG:4326"), "ESRI Shapefile")
        
        for est in estaciones:
            feat = QgsFeature()
            feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(est["LONG"], est["LAT"])))
            feat.setAttributes([est["ID"], est["NAME"], est["LAT"], est["LONG"], est["ELEVATION"]])
            writer.addFeature(feat)
        
        del writer
        self.log.emit(f"Estaciones guardadas en: {out_shp}")
        return out_shp

    def _escribir_listas_estaciones(self, estaciones, carpetas):
        for var_type, folder in carpetas.items():
            path = os.path.join(folder, f"Lista_estac_{var_type}.txt")
            with open(path, 'w') as f:
                f.write("ID,NAME,LAT,LONG,ELEVATION\n")
                for est in estaciones:
                    name_swat = f"{var_type}_{est['NAME']}"
                    f.write(f"{est['ID']},{name_swat},{est['LAT']:.5f},{est['LONG']:.5f},{est['ELEVATION']}\n")

    def _extraer_serie(self, col, pt_geom, start_ee_d, end_ee_d):
        import datetime
        start_dt = datetime.datetime.strptime(self.date_start, "%Y-%m-%d")
        end_dt = datetime.datetime.strptime(self.date_end, "%Y-%m-%d")
        
        all_data = []
        chunk_size = 365 # CMIP6 has daily data, 365 days chunk to avoid limits
        current_dt = start_dt
        
        while current_dt < end_dt:
            next_dt = min(current_dt + datetime.timedelta(days=chunk_size), end_dt)
            
            c_start = ee.Date(current_dt.strftime("%Y-%m-%d"))
            c_end = ee.Date(next_dt.strftime("%Y-%m-%d"))
            
            chunk_data = self._extraer_chunk(col, pt_geom, c_start, c_end)
            all_data.extend(chunk_data)
            
            current_dt = next_dt
            
        return all_data

    def _extraer_chunk(self, col, pt_geom, start_d, end_d):
        n_days = end_d.difference(start_d, 'day')
        dates = ee.List.sequence(0, n_days.subtract(1))

        def daily_cmip6(d):
            date = start_d.advance(d, 'day')
            daily_col = col.filterDate(date, date.advance(1, 'day'))
            
            size = daily_col.size()
            def process():
                img = daily_col.first()
                
                # Precipitation (pr) kg m-2 s-1 -> mm/day (* 86400)
                pr = ee.Number(img.select('pr').reduceRegion(ee.Reducer.mean(), pt_geom, 25000).get('pr', -9999))
                
                # Temperature (tasmax, tasmin) K -> C (- 273.15)
                tmax = ee.Number(img.select('tasmax').reduceRegion(ee.Reducer.mean(), pt_geom, 25000).get('tasmax', -9999))
                tmin = ee.Number(img.select('tasmin').reduceRegion(ee.Reducer.mean(), pt_geom, 25000).get('tasmin', -9999))
                
                # Relative Humidity (hurs) %
                hurs = ee.Number(img.select('hurs').reduceRegion(ee.Reducer.mean(), pt_geom, 25000).get('hurs', -9999))
                
                # Wind speed (sfcWind) m/s
                wind = ee.Number(img.select('sfcWind').reduceRegion(ee.Reducer.mean(), pt_geom, 25000).get('sfcWind', -9999))
                
                # Solar Radiation (rsds) W m-2 -> MJ m-2 day-1 (* 0.0864)
                rsds = ee.Number(img.select('rsds').reduceRegion(ee.Reducer.mean(), pt_geom, 25000).get('rsds', -9999))
                
                return ee.Feature(None, {
                    "date": date.format("YYYYMMDD"),
                    "pr": pr,
                    "tmax": tmax,
                    "tmin": tmin,
                    "hurs": hurs,
                    "wind": wind,
                    "rsds": rsds
                })
            return ee.Algorithms.If(size.gt(0), process(), ee.Feature(None, {"date": date.format("YYYYMMDD"), "pr": -9999, "tmax": -9999, "tmin": -9999, "hurs": -9999, "wind": -9999, "rsds": -9999}))

        fc = ee.FeatureCollection(dates.map(daily_cmip6))
        res = fc.getInfo()["features"]

        data = []
        for r in res:
            p = r["properties"]
            
            pr = p.get("pr", -9999)
            if pr is None: pr = -9999
            if pr != -9999: pr *= 86400
            
            tmax = p.get("tmax", -9999)
            if tmax is None: tmax = -9999
            if tmax != -9999: tmax -= 273.15
            
            tmin = p.get("tmin", -9999)
            if tmin is None: tmin = -9999
            if tmin != -9999: tmin -= 273.15
            
            hurs = p.get("hurs", -9999)
            if hurs is None: hurs = -9999
            
            wind = p.get("wind", -9999)
            if wind is None: wind = -9999
            
            rsds = p.get("rsds", -9999)
            if rsds is None: rsds = -9999
            if rsds != -9999: rsds *= 0.0864
            
            data.append({
                "date": p["date"],
                "Prec": f"{pr:.2f}" if pr != -9999 else "-99",
                "Temp": f"{tmax:.2f},{tmin:.2f}" if tmax != -9999 else "-99,-99",
                "HR": f"{hurs:.2f}" if hurs != -9999 else "-99",
                "Wind": f"{wind:.2f}" if wind != -9999 else "-99",
                "Solar": f"{rsds:.2f}" if rsds != -9999 else "-99"
            })
        return data

    def _escribir_txt_swat(self, df_est, est, carpetas, vars_to_write, start_date_str):
        for var_type in vars_to_write:
            folder = carpetas[var_type]
            fname = f"{var_type}_{est['NAME']}.txt"
            path = os.path.join(folder, fname)
            
            with open(path, 'w') as f:
                f.write(f"{start_date_str}\n")
                for row in df_est:
                    val = row.get(var_type, "-99")
                    if "null" in val or "None" in val: val = "-99"
                    f.write(f"{val}\n")
