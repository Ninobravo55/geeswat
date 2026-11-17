import os
import csv
import math
import traceback

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

# Definición de productos climáticos
CLIMATIC_PRODUCTS = [
    {
        "id": "CFR",
        "name": "NOAA CFSR (CFR 2018 - actualidad)",
        "collection": "NOAA/CFSR",
        "is_daily": False, 
        "vars": ["Temp", "HR", "Wind", "Solar"],
        "desc": "Temperatura, Humedad Relativa, Viento, Radiación",
        "date_range": "2018-12-13 hasta actualidad",
        "res": "~38 km (0.3°)"
    },
    {
        "id": "PERSIANN",
        "name": "PERSIANN-CDR Precipitation",
        "collection": "NOAA/PERSIANN-CDR",
        "is_daily": True,
        "prec_band": "precipitation",
        "vars": ["Prec"],
        "desc": "Precipitación",
        "date_range": "1983-01-01 hasta 2025-11-30",
        "res": "28 km (0.25°)" 
    },
    {
        "id": "CHIRPS",
        "name": "CHIRPS Daily Precipitation",
        "collection": "UCSB-CHG/CHIRPS/DAILY",
        "is_daily": True,
        "prec_band": "precipitation",
        "vars": ["Prec"],
        "desc": "Precipitación",
        "date_range": "1981-01-01 hasta actualidad",
        "res": "5 km (0.05°)"
    },
    {
        "id": "ERA5",
        "name": "ERA5-Land Daily Aggr",
        "collection": "ECMWF/ERA5_LAND/DAILY_AGGR",
        "is_daily": True,
        "temp_2m_band": "temperature_2m", 
        "prec_band": "total_precipitation_sum", 
        "vars": ["Temp", "Prec"],
        "desc": "Precipitación, Temperatura (Media)",
        "date_range": "1950-01-01 hasta actualidad",
        "res": "11 km (0.1°)"
    },
    {
        "id": "GPM",
        "name": "NASA GPM IMERG V07",
        "collection": "NASA/GPM_L3/IMERG_V07",
        "is_daily": False, 
        "prec_band": "precipitationCal", 
        "vars": ["Prec"],
        "desc": "Precipitación",
        "date_range": "1998-01-01 hasta actualidad",
        "res": "11 km (0.1°)"
    },
    {
        "id": "RAIN4PE",
        "name": "RAIN4PE Perú y Ecuador",
        "collection": "projects/sat-io/open-datasets/rainpe/daily",
        "is_daily": True,
        "prec_band": "b1",
        "vars": ["Prec"],
        "desc": "Precipitación",
        "date_range": "1981-01-01 hasta 2015-12-31",
        "res": "10 km (0.1°)"
    },
    {
        "id": "PISCO",
        "name": "PISCOee pm V1 (1981-2016)",
        "collection": "users/lgutierrezlf/PISCOeo_pm/daily",
        "is_daily": True,
        "prec_band": "pp",
        "vars": ["Prec"],
        "desc": "Precipitación",
        "date_range": "1981-01-01 hasta 2016-12-31",
        "res": "1 km (0.01°)"
    },
    {
        "id": "GSMAP",
        "name": "JAXA GSMaP v8 Operational Precipitation",
        "collection": "JAXA/GPM_L3/GSMaP/v8/operational",
        "is_daily": True,
        "prec_band": "hourlyPrecipRateGC",
        "vars": ["Prec"],
        "desc": "Precipitación GSMaP Operational (v8)",
        "date_range": "1998-01-01 hasta actualidad",
        "res": "10 km (0.1°)"
    },
    {
        "id": "PISCOV3",
        "name": "PISCOp v3.0 Perú (1981-2025)",
        "collection": "projects/gee-unas/assets/PISCO_V3",
        "is_daily": True,
        "prec_band": "precipitation",
        "vars": ["Prec"],
        "desc": "Precipitación PISCOp v3.0",
        "date_range": "1981-01-01 hasta 2025-12-31",
        "res": "10 km (0.1°)"
    }
]

class ClimaticosWorker(QThread):
    progreso = pyqtSignal(int)
    log = pyqtSignal(str)
    ok = pyqtSignal(str)
    error = pyqtSignal(str)
    stations_generated = pyqtSignal(str) # To signal loading shapefile

    def __init__(self, output_dir, geom_json, product_id, date_start, date_end, estaciones_layer=None, grid_spacing=None):
        super().__init__()
        self.output_dir = output_dir
        self.geom_json = geom_json
        import json
        self.geom_dict = json.loads(self.geom_json)
        self.product_id = product_id
        self.date_start = date_start
        self.date_end = date_end
        self.estaciones_layer = estaciones_layer
        self.grid_spacing = grid_spacing
        self._cancelar = False

    def cancelar(self):
        self._cancelar = True

    def run(self):
        try:
            self.log.emit(f"Iniciando descarga climática ({self.product_id})...")
            
            prod = next((p for p in CLIMATIC_PRODUCTS if p["id"] == self.product_id), None)
            if not prod:
                raise Exception(f"Producto {self.product_id} no soportado.")

            estaciones = self._obtener_estaciones()
            if not estaciones:
                raise Exception("No se obtuvieron estaciones.")

            n_estaciones = len(estaciones)
            self.log.emit(f"Se procesarán {n_estaciones} estaciones.")

            carpetas = {}
            for var_type in prod["vars"]:
                var_dir = os.path.join(self.output_dir, f"{var_type}_SWAT")
                os.makedirs(var_dir, exist_ok=True)
                carpetas[var_type] = var_dir
            
            self._escribir_listas_estaciones(estaciones, carpetas)

            col = ee.ImageCollection(prod["collection"])
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

                df_est = self._extraer_serie(col, prod, pt_geom, start_d, end_d)
                
                self._escribir_txt_swat(df_est, est, carpetas, prod["vars"], self.date_start.replace("-", ""))

                # Poblar diccionario para matriz
                for row in df_est:
                    d = row["date"]
                    if "Prec" in row:
                        if "prec" not in matrices_data: matrices_data["prec"] = {}
                        if d not in matrices_data["prec"]: matrices_data["prec"][d] = {}
                        matrices_data["prec"][d][est["NAME"]] = row["Prec"]
                        
                    if "Temp" in row:
                        tmax, tmin = row["Temp"].split(",")
                        if "tmax" not in matrices_data: matrices_data["tmax"] = {}
                        if d not in matrices_data["tmax"]: matrices_data["tmax"][d] = {}
                        matrices_data["tmax"][d][est["NAME"]] = tmax
                        
                        if "tmin" not in matrices_data: matrices_data["tmin"] = {}
                        if d not in matrices_data["tmin"]: matrices_data["tmin"][d] = {}
                        matrices_data["tmin"][d][est["NAME"]] = tmin
                        
                    if "HR" in row:
                        if "humidity" not in matrices_data: matrices_data["humidity"] = {}
                        if d not in matrices_data["humidity"]: matrices_data["humidity"][d] = {}
                        matrices_data["humidity"][d][est["NAME"]] = row["HR"]
                        
                    if "Wind" in row:
                        if "wind" not in matrices_data: matrices_data["wind"] = {}
                        if d not in matrices_data["wind"]: matrices_data["wind"][d] = {}
                        matrices_data["wind"][d][est["NAME"]] = row["Wind"]
                        
                    if "Solar" in row:
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
            
            self.log.emit("Descarga climática completada exitosamente.")
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
            
            out_shp = self._crear_shapefile_estaciones(estaciones)
            if out_shp:
                self.stations_generated.emit(out_shp)
                
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
            
            # Garantizar que la malla caiga sobre pixeles válidos del producto climático seleccionado
            prod = next((p for p in CLIMATIC_PRODUCTS if p["id"] == self.product_id), None)
            if prod:
                col_mask = ee.ImageCollection(prod["collection"]).filterDate(self.date_start, self.date_end)
                first_img = col_mask.first()
                # Intersectar máscara del clima con DEM para que sampleRegions descarte los vacíos automáticamente
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
        out_shp = os.path.join(self.output_dir, "Estaciones_Virtuales.shp")
        out_csv = os.path.join(self.output_dir, "Estaciones_Virtuales.csv")
        
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

    def _extraer_serie(self, col, prod, pt_geom, start_ee_d, end_ee_d):
        import datetime
        start_dt = datetime.datetime.strptime(self.date_start, "%Y-%m-%d")
        end_dt = datetime.datetime.strptime(self.date_end, "%Y-%m-%d")
        
        all_data = []
        chunk_size = 2000
        current_dt = start_dt
        
        while current_dt < end_dt:
            next_dt = min(current_dt + datetime.timedelta(days=chunk_size), end_dt)
            
            c_start = ee.Date(current_dt.strftime("%Y-%m-%d"))
            c_end = ee.Date(next_dt.strftime("%Y-%m-%d"))
            
            chunk_data = self._extraer_chunk(col, prod, pt_geom, c_start, c_end)
            all_data.extend(chunk_data)
            
            current_dt = next_dt
            
        return all_data

    def _extraer_chunk(self, col, prod, pt_geom, start_d, end_d):
        n_days = end_d.difference(start_d, 'day')
        dates = ee.List.sequence(0, n_days.subtract(1))

        if prod["id"] == "CFR":
            def daily_cfr(d):
                date = start_d.advance(d, 'day')
                daily_col = col.filterDate(date, date.advance(1, 'day'))
                
                size = daily_col.size()
                def process():
                    temp = daily_col.select("Temperature_surface")
                    tmax = ee.Number(temp.max().reduceRegion(ee.Reducer.mean(), pt_geom, 20000).get("Temperature_surface", -9999))
                    tmin = ee.Number(temp.min().reduceRegion(ee.Reducer.mean(), pt_geom, 20000).get("Temperature_surface", -9999))
                    
                    rh = daily_col.select("Relative_humidity_entire_atmosphere_single_layer")
                    humidity = ee.Number(rh.mean().reduceRegion(ee.Reducer.mean(), pt_geom, 20000).get("Relative_humidity_entire_atmosphere_single_layer", -9999))
                    
                    u = daily_col.select("u-component_of_wind_hybrid")
                    v = daily_col.select("v-component_of_wind_hybrid")
                    u_val = ee.Number(u.mean().reduceRegion(ee.Reducer.mean(), pt_geom, 20000).get("u-component_of_wind_hybrid", 0))
                    v_val = ee.Number(v.mean().reduceRegion(ee.Reducer.mean(), pt_geom, 20000).get("v-component_of_wind_hybrid", 0))
                    
                    rad = daily_col.select("Downward_Short-Wave_Radiation_Flux_surface")
                    radiation = ee.Number(rad.mean().reduceRegion(ee.Reducer.mean(), pt_geom, 20000).get("Downward_Short-Wave_Radiation_Flux_surface", -9999))
                    
                    return ee.Feature(None, {
                        "date": date.format("YYYYMMDD"),
                        "tmax": tmax,
                        "tmin": tmin,
                        "humidity": humidity,
                        "u": u_val,
                        "v": v_val,
                        "radiation": radiation
                    })
                return ee.Algorithms.If(size.gt(0), process(), ee.Feature(None, {"date": date.format("YYYYMMDD"), "tmax": -9999, "tmin": -9999, "humidity": -9999, "u": 0, "v": 0, "radiation": -9999}))

            fc = ee.FeatureCollection(dates.map(daily_cfr))
            res = fc.getInfo()["features"]

            data = []
            for r in res:
                p = r["properties"]
                
                tmax = p.get("tmax", -9999)
                if tmax is None: tmax = -9999
                tmin = p.get("tmin", -9999)
                if tmin is None: tmin = -9999
                
                if tmax != -9999: tmax -= 273.15
                if tmin != -9999: tmin -= 273.15
                
                u = p.get("u", 0)
                if u is None: u = 0
                v = p.get("v", 0)
                if v is None: v = 0
                wind = math.sqrt(u**2 + v**2) if u!=0 or v!=0 else -9999

                rad = p.get("radiation", -9999)
                if rad is None: rad = -9999
                if rad != -9999: rad *= 0.0864

                hum = p.get("humidity")
                if hum is None: hum = -9999
                
                data.append({
                    "date": p["date"],
                    "Temp": f"{tmax:.2f},{tmin:.2f}" if tmax != -9999 else "-99,-99",
                    "HR": f"{hum:.2f}" if hum != -9999 else "-99",
                    "Wind": f"{wind:.2f}" if wind != -9999 else "-99",
                    "Solar": f"{rad:.2f}" if rad != -9999 else "-99"
                })
            return data

        elif prod["id"] == "GPM":
            def daily_gpm(d):
                date = start_d.advance(d, 'day')
                daily_col = col.filterDate(date, date.advance(1, 'day'))
                def to_mm(img): return img.select(prod["prec_band"]).multiply(0.5)
                daily_mm = daily_col.map(to_mm).sum()
                prec = ee.Number(daily_mm.reduceRegion(ee.Reducer.mean(), pt_geom, 10000).get(prod["prec_band"], -9999))
                return ee.Feature(None, {"date": date.format("YYYYMMDD"), "Prec": prec})
            
            fc = ee.FeatureCollection(dates.map(daily_gpm))
            res = fc.getInfo()["features"]
            data = []
            for r in res:
                p = r["properties"]
                val = p.get("Prec", -9999)
                if val is None: val = -9999
                data.append({"date": p["date"], "Prec": f"{val:.2f}" if val != -9999 else "-99"})
            return data

        elif prod["id"] == "GSMAP":
            def daily_gsmap(d):
                date = start_d.advance(d, 'day')
                daily_col = col.filterDate(date, date.advance(1, 'day'))
                size = daily_col.size()
                
                def process():
                    daily_mm = daily_col.select(prod["prec_band"]).sum()
                    prec = ee.Number(daily_mm.reduceRegion(ee.Reducer.mean(), pt_geom, 10000).get(prod["prec_band"], -9999))
                    return ee.Feature(None, {"date": date.format("YYYYMMDD"), "Prec": prec})
                    
                return ee.Algorithms.If(size.gt(0), process(), ee.Feature(None, {"date": date.format("YYYYMMDD"), "Prec": -9999}))
            
            fc = ee.FeatureCollection(dates.map(daily_gsmap))
            res = fc.getInfo()["features"]
            data = []
            for r in res:
                p = r["properties"]
                val = p.get("Prec", -9999)
                if val is None: val = -9999
                data.append({"date": p["date"], "Prec": f"{val:.2f}" if val != -9999 else "-99"})
            return data

        elif prod["id"] == "PISCOV3":
            def daily_piscov3(d):
                date = start_d.advance(d, 'day')
                anio = ee.Number(date.get('year'))
                img = col.filter(ee.Filter.stringContains('system:index', anio.format())).first()
                dia = ee.Number(date.getRelative('day', 'year')).add(1)
                banda = ee.String('b').cat(dia.format())
                
                def process():
                    img_daily = img.select([banda], ['precipitation'])
                    prec = ee.Number(img_daily.reduceRegion(ee.Reducer.mean(), pt_geom, 10000).get('precipitation', -9999))
                    return ee.Feature(None, {"date": date.format("YYYYMMDD"), "Prec": prec})
                    
                return ee.Algorithms.If(img, process(), ee.Feature(None, {"date": date.format("YYYYMMDD"), "Prec": -9999}))
            
            fc = ee.FeatureCollection(dates.map(daily_piscov3))
            res = fc.getInfo()["features"]
            data = []
            for r in res:
                p = r["properties"]
                val = p.get("Prec", -9999)
                if val is None: val = -9999
                data.append({"date": p["date"], "Prec": f"{val:.2f}" if val != -9999 else "-99"})
            return data

        else:
            def daily_generic(d):
                date = start_d.advance(d, 'day')
                daily_col = col.filterDate(date, date.advance(1, 'day'))
                size = daily_col.size()
                
                def process():
                    img = daily_col.first()
                    props = {"date": date.format("YYYYMMDD")}
                    
                    # Extraer todas las bandas dinámicamente con resolución ~1km para captar variabilidad
                    res_dict = img.reduceRegion(ee.Reducer.mean(), pt_geom, 1000)
                    return ee.Feature(None, props).set(res_dict)
                    
                default_props = {"date": date.format("YYYYMMDD")}
                return ee.Algorithms.If(size.gt(0), process(), ee.Feature(None, default_props))

            fc = ee.FeatureCollection(dates.map(daily_generic))
            res = fc.getInfo()["features"]
            
            data = []
            for r in res:
                p = r["properties"]
                row = {"date": p["date"]}
                
                if "Temp" in prod["vars"]:
                    # Soporte dinámico para bandas tmax, tmin o promedios
                    tmax = p.get("tmax") or p.get("Tmax") or p.get("TMAX") or p.get("tx")
                    tmin = p.get("tmin") or p.get("Tmin") or p.get("TMIN") or p.get("tn")
                    
                    if tmax is not None and tmin is not None:
                        row["Temp"] = f"{tmax:.2f},{tmin:.2f}"
                    else:
                        tm = p.get(prod.get("temp_2m_band", "")) or p.get("tmean") or p.get("Temperature") or -9999
                        if tm != -9999 and prod["id"] == "ERA5": 
                            tm -= 273.15
                        row["Temp"] = f"{tm:.2f},{tm:.2f}" if tm != -9999 else "-99,-99"
                    
                if "Prec" in prod["vars"]:
                    pr = p.get(prod.get("prec_band", "")) or p.get("precipitation") or p.get("Prec") or p.get("prec") or p.get("pp") or p.get("b1") or -9999
                    if pr != -9999 and prod["id"] == "ERA5":
                        pr *= 1000
                    row["Prec"] = f"{pr:.2f}" if pr != -9999 else "-99"
                data.append(row)
                
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
