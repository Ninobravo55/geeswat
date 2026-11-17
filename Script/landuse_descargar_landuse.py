# -*- coding: utf-8 -*-
"""
landuse_descargar_landuse.py — geeSWAT
Descarga uso del suelo desde GEE para SWAT.

Fuentes disponibles:
  1. Copernicus 2019 — COPERNICUS/Landcover/100m/Proba-V-C3/Global/2019
                        banda: discrete_classification  | escala nativa: 100 m
  2. ESA GlobCover 2009 — ESA/GLOBCOVER_L4_200901_200912_V2_3
                          banda: landcover               | escala nativa: 300 m

Salidas por ejecucion:
  <carpeta_salida>/LANDUSE/
      <proyecto>_<fuente>_<timestamp>.tif             — raster Int16
      <proyecto>_<fuente>_<timestamp>_Landuse_lookup.csv — LANDUSE_ID, SWAT_CODE

geeSWAT v1.2.3 — Geomatica Ambiental
"""
import os
import math
import time
import datetime
import csv

from qgis.PyQt.QtCore import QThread, pyqtSignal

# ── Tablas SWAT embebidas (del notebook 05_LANDUSE.ipynb) ────────────────────
_TABLA_2019 = [
    {"LANDUSE_ID": 0,   "SWAT_CODE": "RNGB"},
    {"LANDUSE_ID": 20,  "SWAT_CODE": "RNGE"}, # Shrubland
    {"LANDUSE_ID": 30,  "SWAT_CODE": "PAST"},
    {"LANDUSE_ID": 40,  "SWAT_CODE": "AGRL"},
    {"LANDUSE_ID": 50,  "SWAT_CODE": "URHD"}, # Urban High Density
    {"LANDUSE_ID": 60,  "SWAT_CODE": "BARR"}, # Barren Sparse Vegetation 
    {"LANDUSE_ID": 70,  "SWAT_CODE": "WATR"},
    {"LANDUSE_ID": 80,  "SWAT_CODE": "WATR"},
    {"LANDUSE_ID": 90,  "SWAT_CODE": "WETL"},
    {"LANDUSE_ID": 100, "SWAT_CODE": "RNGE"},
    {"LANDUSE_ID": 111, "SWAT_CODE": "FRSE"},
    {"LANDUSE_ID": 112, "SWAT_CODE": "FRSE"},
    {"LANDUSE_ID": 113, "SWAT_CODE": "FRSD"},
    {"LANDUSE_ID": 114, "SWAT_CODE": "FRSD"},
    {"LANDUSE_ID": 115, "SWAT_CODE": "FRST"},
    {"LANDUSE_ID": 116, "SWAT_CODE": "FRST"},
    {"LANDUSE_ID": 121, "SWAT_CODE": "RNGB"},
    {"LANDUSE_ID": 122, "SWAT_CODE": "RNGB"},
    {"LANDUSE_ID": 123, "SWAT_CODE": "RNGB"},
    {"LANDUSE_ID": 124, "SWAT_CODE": "RNGB"},
    {"LANDUSE_ID": 125, "SWAT_CODE": "FRST"},
    {"LANDUSE_ID": 126, "SWAT_CODE": "FRST"},
    {"LANDUSE_ID": 200, "SWAT_CODE": "WATR"},
]

_TABLA_2009 = [
    {"LANDUSE_ID": 11,  "SWAT_CODE": "WETL"},
    {"LANDUSE_ID": 14,  "SWAT_CODE": "AGRL"},
    {"LANDUSE_ID": 20,  "SWAT_CODE": "AGRL"},
    {"LANDUSE_ID": 30,  "SWAT_CODE": "RNGB"},
    {"LANDUSE_ID": 40,  "SWAT_CODE": "FRSE"},
    {"LANDUSE_ID": 50,  "SWAT_CODE": "FRSD"},
    {"LANDUSE_ID": 60,  "SWAT_CODE": "FRSD"},
    {"LANDUSE_ID": 70,  "SWAT_CODE": "FRSE"},
    {"LANDUSE_ID": 90,  "SWAT_CODE": "FRST"},
    {"LANDUSE_ID": 100, "SWAT_CODE": "FRST"},
    {"LANDUSE_ID": 110, "SWAT_CODE": "RNGE"},
    {"LANDUSE_ID": 120, "SWAT_CODE": "RNGB"},
    {"LANDUSE_ID": 130, "SWAT_CODE": "RNGB"},
    {"LANDUSE_ID": 140, "SWAT_CODE": "RNGE"},
    {"LANDUSE_ID": 150, "SWAT_CODE": "BSVG"},
    {"LANDUSE_ID": 160, "SWAT_CODE": "WETF"},
    {"LANDUSE_ID": 170, "SWAT_CODE": "WETF"},
    {"LANDUSE_ID": 180, "SWAT_CODE": "WETL"},
    {"LANDUSE_ID": 190, "SWAT_CODE": "URHD"},
    {"LANDUSE_ID": 200, "SWAT_CODE": "BARR"},
    {"LANDUSE_ID": 210, "SWAT_CODE": "WATR"},
    {"LANDUSE_ID": 220, "SWAT_CODE": "WATR"},
    {"LANDUSE_ID": 230, "SWAT_CODE": "RNGB"},
]

_TABLA_ESRI_10M = [
    {"LANDUSE_ID": 1,  "SWAT_CODE": "WATR"}, # Water
    {"LANDUSE_ID": 2,  "SWAT_CODE": "FRST"}, # Trees
    {"LANDUSE_ID": 4,  "SWAT_CODE": "WETL"}, # Flooded Vegetation
    {"LANDUSE_ID": 5,  "SWAT_CODE": "AGRL"}, # Crops
    {"LANDUSE_ID": 7,  "SWAT_CODE": "URBN"}, # Built Area
    {"LANDUSE_ID": 8,  "SWAT_CODE": "BARR"}, # Bare Ground
    {"LANDUSE_ID": 9,  "SWAT_CODE": "WATR"}, # Snow/Ice
    {"LANDUSE_ID": 10, "SWAT_CODE": "WATR"}, # Clouds
    {"LANDUSE_ID": 11, "SWAT_CODE": "RNGE"}, # Rangeland
]

_TABLA_MODIS_500M = [
    {"LANDUSE_ID": 0,  "SWAT_CODE": "WATR"},
    {"LANDUSE_ID": 1,  "SWAT_CODE": "FRSE"},
    {"LANDUSE_ID": 2,  "SWAT_CODE": "FRSE"},
    {"LANDUSE_ID": 3,  "SWAT_CODE": "FRSD"},
    {"LANDUSE_ID": 4,  "SWAT_CODE": "FRSD"},
    {"LANDUSE_ID": 5,  "SWAT_CODE": "RNGB"},
    {"LANDUSE_ID": 6,  "SWAT_CODE": "PAST"},
    {"LANDUSE_ID": 7,  "SWAT_CODE": "AGRL"},
    {"LANDUSE_ID": 8,  "SWAT_CODE": "ORCD"},
    {"LANDUSE_ID": 9,  "SWAT_CODE": "URMD"},
    {"LANDUSE_ID": 10, "SWAT_CODE": "WATR"},
    {"LANDUSE_ID": 11, "SWAT_CODE": "SWRN"},
]

_TABLA_MAPBIOMAS_LATAM = [
    {"LANDUSE_ID":0,  "SWAT_CODE":"BARR"},  # No observado
    {"LANDUSE_ID":1,  "SWAT_CODE":"FRST"},  # Formación boscosa
    {"LANDUSE_ID":3,  "SWAT_CODE":"FRST"},  # Bosque
    {"LANDUSE_ID":4,  "SWAT_CODE":"FRSD"},  # Sabana arbolada / Bosque abierto
    {"LANDUSE_ID":5,  "SWAT_CODE":"WETF"},  # Manglar
    {"LANDUSE_ID":6,  "SWAT_CODE":"WETF"},  # Bosque inundable
    {"LANDUSE_ID":9,  "SWAT_CODE":"FRST"},  # Plantación forestal / Silvicultura
    {"LANDUSE_ID":10, "SWAT_CODE":"RNGE"},  # Formación natural no boscosa
    {"LANDUSE_ID":11, "SWAT_CODE":"WETL"},  # Humedal
    {"LANDUSE_ID":12, "SWAT_CODE":"RNGE"},  # Pastizal / Herbazal
    {"LANDUSE_ID":13, "SWAT_CODE":"RNGE"},  # Otra formación no boscosa
    {"LANDUSE_ID":14, "SWAT_CODE":"AGRL"},  # Área agropecuaria
    {"LANDUSE_ID":15, "SWAT_CODE":"PAST"},  # Pastizal pecuario
    {"LANDUSE_ID":18, "SWAT_CODE":"AGRL"},  # Agricultura en descanso
    {"LANDUSE_ID":19, "SWAT_CODE":"AGRR"},  # Cultivo temporal
    {"LANDUSE_ID":20, "SWAT_CODE":"SUGC"},  # Caña de azúcar
    {"LANDUSE_ID":21, "SWAT_CODE":"AGRL"},  # Mosaico agrícola
    {"LANDUSE_ID":22, "SWAT_CODE":"BARR"},  # Área sin vegetación
    {"LANDUSE_ID":23, "SWAT_CODE":"BARR"},  # Playa, duna y arena
    {"LANDUSE_ID":24, "SWAT_CODE":"URBN"},  # Infraestructura urbana
    {"LANDUSE_ID":25, "SWAT_CODE":"BARR"},  # Otra área sin vegetación
    {"LANDUSE_ID":26, "SWAT_CODE":"WATR"},  # Agua
    {"LANDUSE_ID":27, "SWAT_CODE":"BARR"},  # No observado
    {"LANDUSE_ID":29, "SWAT_CODE":"BARR"},  # Afloramiento rocoso
    {"LANDUSE_ID":30, "SWAT_CODE":"BARR"},  # Minería
    {"LANDUSE_ID":31, "SWAT_CODE":"WATR"},  # Acuicultura
    {"LANDUSE_ID":32, "SWAT_CODE":"BARR"},  # Salina costera
    {"LANDUSE_ID":33, "SWAT_CODE":"WATR"},  # Río, lago y océano
    {"LANDUSE_ID":34, "SWAT_CODE":"WATR"},  # Glaciar
    {"LANDUSE_ID":35, "SWAT_CODE":"OILP"},  # Palma aceitera
    {"LANDUSE_ID":36, "SWAT_CODE":"ORCD"},  # Cultivo perenne
    {"LANDUSE_ID":39, "SWAT_CODE":"SOYB"},  # Soja
    {"LANDUSE_ID":40, "SWAT_CODE":"RICE"},  # Arroz
    {"LANDUSE_ID":41, "SWAT_CODE":"AGRR"},  # Otros cultivos temporales
    {"LANDUSE_ID":46, "SWAT_CODE":"COFF"},  # Café
    {"LANDUSE_ID":47, "SWAT_CODE":"ORCD"},  # Citrus
    {"LANDUSE_ID":48, "SWAT_CODE":"ORCD"},  # Otros cultivos perennes
    {"LANDUSE_ID":49, "SWAT_CODE":"FRST"},  # Vegetación boscosa sobre arena
    {"LANDUSE_ID":50, "SWAT_CODE":"RNGB"},  # Vegetación xerófila
    {"LANDUSE_ID":59, "SWAT_CODE":"FRST"},  # Bosque primario
    {"LANDUSE_ID":60, "SWAT_CODE":"FRST"},  # Bosque secundario
    {"LANDUSE_ID":61, "SWAT_CODE":"BARR"},  # Salina
    {"LANDUSE_ID":62, "SWAT_CODE":"COTS"},  # Algodón
    {"LANDUSE_ID":63, "SWAT_CODE":"RNGE"},  # Estepa
    {"LANDUSE_ID":66, "SWAT_CODE":"RNGB"},  # Matorral
    {"LANDUSE_ID":67, "SWAT_CODE":"FRSD"},  # Bosque achaparrado
    {"LANDUSE_ID":68, "SWAT_CODE":"BARR"},  # Otra área natural sin vegetación
    {"LANDUSE_ID":70, "SWAT_CODE":"RNGE"},  # Loma costera
    {"LANDUSE_ID":72, "SWAT_CODE":"AGRL"},  # Otros cultivos
    {"LANDUSE_ID":74, "SWAT_CODE":"BANA"},  # Plátano y banano
    {"LANDUSE_ID":75, "SWAT_CODE":"URBN"},  # Planta fotovoltaica
    {"LANDUSE_ID":79, "SWAT_CODE":"PINE"},  # Plantación de pinos
    {"LANDUSE_ID":80, "SWAT_CODE":"EUCA"},  # Plantación de eucaliptus
    {"LANDUSE_ID":81, "SWAT_CODE":"RNGE"},  # Herbazal andino
    {"LANDUSE_ID":82, "SWAT_CODE":"WETL"},  # Herbazal inundable
    {"LANDUSE_ID":83, "SWAT_CODE":"FRST"},  # Otros usos forestales
]

_TABLA_GLC_FCS30D = [
    {"LANDUSE_ID":10,  "DESCRIPCION":"Cultivo de secano",                                    "HEX_COLOR":"#ffff64", "SWAT_CODE":"AGRL"},
    {"LANDUSE_ID":11,  "DESCRIPCION":"Cultivo herbáceo",                                     "HEX_COLOR":"#ffff64", "SWAT_CODE":"AGRL"},
    {"LANDUSE_ID":12,  "DESCRIPCION":"Cultivo con cobertura arbórea o arbustiva",            "HEX_COLOR":"#ffff00", "SWAT_CODE":"ORCD"},
    {"LANDUSE_ID":20,  "DESCRIPCION":"Cultivo irrigado",                                     "HEX_COLOR":"#aaf0f0", "SWAT_CODE":"AGRL"},
    {"LANDUSE_ID":51,  "DESCRIPCION":"Bosque latifoliado perenne abierto",                   "HEX_COLOR":"#4c7300", "SWAT_CODE":"FRSE"},
    {"LANDUSE_ID":52,  "DESCRIPCION":"Bosque latifoliado perenne cerrado",                   "HEX_COLOR":"#006400", "SWAT_CODE":"FRSE"},
    {"LANDUSE_ID":61,  "DESCRIPCION":"Bosque latifoliado caducifolio abierto",               "HEX_COLOR":"#a8c800", "SWAT_CODE":"FRSD"},
    {"LANDUSE_ID":62,  "DESCRIPCION":"Bosque latifoliado caducifolio cerrado",               "HEX_COLOR":"#00a000", "SWAT_CODE":"FRSD"},
    {"LANDUSE_ID":71,  "DESCRIPCION":"Bosque aciculifoliado perenne abierto",                "HEX_COLOR":"#005000", "SWAT_CODE":"FRSE"},
    {"LANDUSE_ID":72,  "DESCRIPCION":"Bosque aciculifoliado perenne cerrado",                "HEX_COLOR":"#003c00", "SWAT_CODE":"FRSE"},
    {"LANDUSE_ID":81,  "DESCRIPCION":"Bosque aciculifoliado caducifolio abierto",            "HEX_COLOR":"#286400", "SWAT_CODE":"FRSD"},
    {"LANDUSE_ID":82,  "DESCRIPCION":"Bosque aciculifoliado caducifolio cerrado",            "HEX_COLOR":"#285000", "SWAT_CODE":"FRSD"},
    {"LANDUSE_ID":91,  "DESCRIPCION":"Bosque mixto abierto",                                 "HEX_COLOR":"#a0b432", "SWAT_CODE":"FRST"},
    {"LANDUSE_ID":92,  "DESCRIPCION":"Bosque mixto cerrado",                                 "HEX_COLOR":"#788200", "SWAT_CODE":"FRST"},
    {"LANDUSE_ID":120, "DESCRIPCION":"Matorral",                                             "HEX_COLOR":"#966400", "SWAT_CODE":"RNGB"},
    {"LANDUSE_ID":121, "DESCRIPCION":"Matorral perenne",                                     "HEX_COLOR":"#964b00", "SWAT_CODE":"RNGB"},
    {"LANDUSE_ID":122, "DESCRIPCION":"Matorral caducifolio",                                 "HEX_COLOR":"#966400", "SWAT_CODE":"RNGB"},
    {"LANDUSE_ID":130, "DESCRIPCION":"Pastizal",                                             "HEX_COLOR":"#ffb432", "SWAT_CODE":"RNGE"},
    {"LANDUSE_ID":140, "DESCRIPCION":"Líquenes y musgos",                                    "HEX_COLOR":"#ffdcd2", "SWAT_CODE":"RNGE"},
    {"LANDUSE_ID":150, "DESCRIPCION":"Vegetación dispersa",                                  "HEX_COLOR":"#ffebaf", "SWAT_CODE":"RNGE"},
    {"LANDUSE_ID":152, "DESCRIPCION":"Matorral disperso",                                    "HEX_COLOR":"#ffd278", "SWAT_CODE":"RNGB"},
    {"LANDUSE_ID":153, "DESCRIPCION":"Vegetación herbácea dispersa",                         "HEX_COLOR":"#ffebaf", "SWAT_CODE":"RNGE"},
    {"LANDUSE_ID":181, "DESCRIPCION":"Pantano boscoso",                                      "HEX_COLOR":"#00a884", "SWAT_CODE":"WETF"},
    {"LANDUSE_ID":182, "DESCRIPCION":"Pantano herbáceo",                                     "HEX_COLOR":"#73ffdf", "SWAT_CODE":"WETL"},
    {"LANDUSE_ID":183, "DESCRIPCION":"Llanura inundable",                                    "HEX_COLOR":"#9ebb3b", "SWAT_CODE":"WETL"},
    {"LANDUSE_ID":184, "DESCRIPCION":"Área salina",                                          "HEX_COLOR":"#828282", "SWAT_CODE":"BARR"},
    {"LANDUSE_ID":185, "DESCRIPCION":"Manglar",                                              "HEX_COLOR":"#f57ab6", "SWAT_CODE":"WETF"},
    {"LANDUSE_ID":186, "DESCRIPCION":"Marisma salina",                                       "HEX_COLOR":"#66cdab", "SWAT_CODE":"WETL"},
    {"LANDUSE_ID":187, "DESCRIPCION":"Planicie de marea",                                    "HEX_COLOR":"#444f89", "SWAT_CODE":"WETL"},
    {"LANDUSE_ID":190, "DESCRIPCION":"Superficies impermeables",                             "HEX_COLOR":"#c31400", "SWAT_CODE":"URBN"},
    {"LANDUSE_ID":200, "DESCRIPCION":"Áreas desnudas",                                       "HEX_COLOR":"#fff5d7", "SWAT_CODE":"BARR"},
    {"LANDUSE_ID":201, "DESCRIPCION":"Áreas desnudas consolidadas",                          "HEX_COLOR":"#dcdcdc", "SWAT_CODE":"BARR"},
    {"LANDUSE_ID":202, "DESCRIPCION":"Áreas desnudas no consolidadas",                       "HEX_COLOR":"#fff5d7", "SWAT_CODE":"BARR"},
    {"LANDUSE_ID":210, "DESCRIPCION":"Cuerpo de agua",                                       "HEX_COLOR":"#0046c8", "SWAT_CODE":"WATR"},
    {"LANDUSE_ID":220, "DESCRIPCION":"Hielo y nieve permanente",                             "HEX_COLOR":"#ffffff", "SWAT_CODE":"WATR"}
]

# ── Fuentes disponibles ───────────────────────────────────────────────────────
LANDUSE_SOURCES = [
    {
        'id'        : 'GLC_FCS30D',
        'label'     : 'GLC_FCS30D Global Land Cover (30 m)',
        'collection': 'projects/sat-io/open-datasets/GLC-FCS30D',
        'is_glcfcs30d': True,
        'has_year'  : True,
        'year_min'  : 1985,
        'year_max'  : 2022,
        'year'      : 2022,
        'banda'     : 'classification',
        'scale'     : 30,
        'tabla_swat': _TABLA_GLC_FCS30D,
        'desc'      : (
            'GLC_FCS30D (1985-2022) 30m. 35 subcategorías globales. '
            'Disponible: 1985, 1990, 1995, y de 2000 a 2022 anualmente.'
        ),
    },
    {
        'id'        : 'MAPBIOMAS_PERU',
        'label'     : 'MAPBIOMAS Cobertura Perú (30 m)',
        'collection': 'projects/mapbiomas-public/assets/peru/collection3/mapbiomas_peru_collection3_integration_v1',
        'is_collection': False,
        'has_year'  : True,
        'year_min'  : 1985,
        'year_max'  : 2024,
        'year'      : 2024,
        'banda'     : 'classification_{year}',
        'scale'     : 30,
        'tabla_swat': _TABLA_MAPBIOMAS_LATAM,
        'desc'      : (
            'MapBiomas Perú. '
            'Clasificación anual 1985-2024. Resolución nativa 30 m.'
        ),
    },
    {
        'id'        : 'MAPBIOMAS_BRASIL',
        'label'     : 'MAPBIOMAS Cobertura Brasil (30 m)',
        'collection': 'projects/mapbiomas-public/assets/brazil/lulc/collection10/mapbiomas_brazil_collection10_integration_v2',
        'is_collection': False,
        'has_year'  : True,
        'year_min'  : 1985,
        'year_max'  : 2024,
        'year'      : 2024,
        'banda'     : 'classification_{year}',
        'scale'     : 30,
        'tabla_swat': _TABLA_MAPBIOMAS_LATAM,
        'desc'      : (
            'MapBiomas Brasil. '
            'Clasificación anual 1985-2024. Resolución nativa 30 m.'
        ),
    },
    {
        'id'        : 'MAPBIOMAS_BOLIVIA',
        'label'     : 'MAPBIOMAS Cobertura Bolivia (30 m)',
        'collection': 'projects/mapbiomas-public/assets/bolivia/lulc/collection3/mapbiomas_bolivia_collection3_integration_v1',
        'is_collection': False,
        'has_year'  : True,
        'year_min'  : 1985,
        'year_max'  : 2024,
        'year'      : 2024,
        'banda'     : 'classification_{year}',
        'scale'     : 30,
        'tabla_swat': _TABLA_MAPBIOMAS_LATAM,
        'desc'      : (
            'MapBiomas Bolivia. '
            'Clasificación anual 1985-2024. Resolución nativa 30 m.'
        ),
    },
    {
        'id'        : 'MAPBIOMAS_COLOMBIA',
        'label'     : 'MAPBIOMAS Cobertura Colombia (30 m)',
        'collection': 'projects/mapbiomas-colombia/assets/LULC/COLECCION3/INTEGRACION/COLOMBIA-1',  # pragma: allowlist secret
        'is_collection': False,
        'has_year'  : True,
        'year_min'  : 1985,
        'year_max'  : 2024,
        'year'      : 2024,
        'banda'     : 'classification_{year}',
        'scale'     : 30,
        'tabla_swat': _TABLA_MAPBIOMAS_LATAM,
        'desc'      : (
            'MapBiomas Colombia. '
            'Clasificación anual 1985-2024. Resolución nativa 30 m.'
        ),
    },
    {
        'id'        : 'MAPBIOMAS_ECUADOR',
        'label'     : 'MAPBIOMAS Cobertura Ecuador (30 m)',
        'collection': 'projects/mapbiomas-public/assets/ecuador/lulc/collection3/mapbiomas_ecuador_collection3_coverage_v3',
        'is_collection': False,
        'has_year'  : True,
        'year_min'  : 1985,
        'year_max'  : 2024,
        'year'      : 2024,
        'banda'     : 'classification_{year}',
        'scale'     : 30,
        'tabla_swat': _TABLA_MAPBIOMAS_LATAM,
        'desc'      : (
            'MapBiomas Ecuador. '
            'Clasificación anual 1985-2024. Resolución nativa 30 m.'
        ),
    },
    {
        'id'        : 'MAPBIOMAS_VENEZUELA',
        'label'     : 'MAPBIOMAS Cobertura Venezuela (30 m)',
        'collection': 'projects/mapbiomas-public/assets/venezuela/lulc/collection3/mapbiomas_venezuela_collection3_coverage_v1',
        'is_collection': False,
        'has_year'  : True,
        'year_min'  : 1985,
        'year_max'  : 2024,
        'year'      : 2024,
        'banda'     : 'classification_{year}',
        'scale'     : 30,
        'tabla_swat': _TABLA_MAPBIOMAS_LATAM,
        'desc'      : (
            'MapBiomas Venezuela. '
            'Clasificación anual 1985-2024. Resolución nativa 30 m.'
        ),
    },
    {
        'id'        : 'MAPBIOMAS_PARAGUAY',
        'label'     : 'MAPBIOMAS Cobertura Paraguay (30 m)',
        'collection': 'projects/mapbiomas-public/assets/paraguay/collection2/mapbiomas_paraguay_collection2_integration_v1',
        'is_collection': False,
        'has_year'  : True,
        'year_min'  : 1985,
        'year_max'  : 2023,
        'year'      : 2023,
        'banda'     : 'classification_{year}',
        'scale'     : 30,
        'tabla_swat': _TABLA_MAPBIOMAS_LATAM,
        'desc'      : (
            'MapBiomas Paraguay. '
            'Clasificación anual 1985-2023. Resolución nativa 30 m.'
        ),
    },
    {
        'id'        : 'MAPBIOMAS_CHILE',
        'label'     : 'MAPBIOMAS Cobertura Chile (30 m)',
        'collection': 'projects/mapbiomas-chile/assets/LULC/COLLECTION-02/CLASSIFICATIONS/classification-final/clasificacion-final-2',  # pragma: allowlist secret
        'is_collection': False,
        'has_year'  : True,
        'year_min'  : 1999,
        'year_max'  : 2024,
        'year'      : 2024,
        'banda'     : 'classification_{year}',
        'scale'     : 30,
        'tabla_swat': _TABLA_MAPBIOMAS_LATAM,
        'desc'      : (
            'MapBiomas Chile. '
            'Clasificación anual 1999-2024. Resolución nativa 30 m.'
        ),
    },
    {
        'id'        : 'MAPBIOMAS_URUGUAY',
        'label'     : 'MAPBIOMAS Cobertura Uruguay (30 m)',
        'collection': 'projects/mapbiomas-public/assets/uruguay/lulc/collection3/mapbiomas_uruguay_collection3_coverage_v1',
        'is_collection': False,
        'has_year'  : True,
        'year_min'  : 1985,
        'year_max'  : 2024,
        'year'      : 2024,
        'banda'     : 'classification_{year}',
        'scale'     : 30,
        'tabla_swat': _TABLA_MAPBIOMAS_LATAM,
        'desc'      : (
            'MapBiomas Uruguay. '
            'Clasificación anual 1985-2024. Resolución nativa 30 m.'
        ),
    },
    {
        'id'        : 'MAPBIOMAS_ARGENTINA',
        'label'     : 'MAPBIOMAS Cobertura Argentina (30 m)',
        'collection': 'projects/mapbiomas-public/assets/argentina/lulc/collection2/mapbiomas_argentina_collection2_integration_v3',
        'is_collection': False,
        'has_year'  : True,
        'year_min'  : 1985,
        'year_max'  : 2024,
        'year'      : 2024,
        'banda'     : 'classification_{year}',
        'scale'     : 30,
        'tabla_swat': _TABLA_MAPBIOMAS_LATAM,
        'desc'      : (
            'MapBiomas Argentina. '
            'Clasificación anual 1985-2024. Resolución nativa 30 m.'
        ),
    },
    {
        'id'        : 'MODIS_MCD12Q1',
        'label'     : 'MODIS MCD12Q1 LC_Type5 (500 m)',
        'collection': 'MODIS/061/MCD12Q1',
        'is_collection': True,
        'has_year'  : True,
        'year_min'  : 2001,
        'year_max'  : 2024,
        'year'      : 2019,
        'banda'     : 'LC_Type5',
        'banda_original': 'LC_Type5',
        'scale'     : 500,
        'tabla_swat': _TABLA_MODIS_500M,
        'desc'      : (
            'Cobertura MODIS MCD12Q2.061 (Plant Functional Types) - Ano 2019. '
            '12 clases globales. Resolucion 500m.'
        ),
    },
    {
        'id'        : 'ESRI_10M',
        'label'     : 'ESRI Global LULC 10m',
        'collection': 'projects/sat-io/open-datasets/landcover/ESRI_Global-LULC_10m_TS',  # pragma: allowlist secret
        'is_collection': True,
        'has_year'  : True,
        'year_min'  : 2017,
        'year_max'  : 2025,
        'year'      : 2025,
        'banda'     : 'classification',
        'banda_original': 'b1',
        'scale'     : 10,
        'tabla_swat': _TABLA_ESRI_10M,
        'desc'      : (
            'Cobertura de clasificacion de ESRI 10m (S2TSLULC) - Ano 2017 al 2025. '
            '9 clases remapeadas internamente. Alta resolucion (10m).'
        ),
    },
    {
        'id'        : 'COPERNICUS',
        'label'     : 'Copernicus Global Land Cover (100 m)',
        'collection': 'COPERNICUS/Landcover/100m/Proba-V-C3/Global/{year}',  # pragma: allowlist secret
        'has_year'  : True,
        'year_min'  : 2015,
        'year_max'  : 2019,
        'year'      : 2019,
        'banda'     : 'discrete_classification',
        'scale'     : 100,
        'tabla_swat': _TABLA_2019,
        'desc'      : (
            'Copernicus Global Land Cover Layers CGLS-LC100 Collection 3. '
            '23 clases. Resolucion nativa 100 m. '
            'Disponible para los años 2015 a 2019.'
        ),
    },
    {
        'id'        : 'GLOBCOVER_2009',
        'label'     : 'ESA GlobCover 2009 (300 m)',
        'collection': 'ESA/GLOBCOVER_L4_200901_200912_V2_3',  # pragma: allowlist secret
        'banda'     : 'landcover',
        'scale'     : 300,
        'tabla_swat': _TABLA_2009,
        'desc'      : (
            'ESA GlobCover L4 2009. 23 clases. Resolucion nativa 300 m. '
            'Util para estudios historicos o comparaciones temporales.'
        ),
    },
]
LANDUSE_LABELS = [s['label'] for s in LANDUSE_SOURCES]

GDRIVE_FOLDER = 'GEE_geeSWAT'
LIMITE_MP     = 10_000_000
POLL_INTERVAL = 12


# ═════════════════════════════════════════════════════════════════════════════
# WORKER THREAD
# ═════════════════════════════════════════════════════════════════════════════
class LanduseWorker(QThread):
    log      = pyqtSignal(str)
    progreso = pyqtSignal(int)
    ok       = pyqtSignal(str, str, bool)   # (ruta_tif, ruta_csv, via_drive)
    error    = pyqtSignal(str)

    def __init__(self, source_info, lon_min, lat_min, lon_max, lat_max,
                 scale_eff, crs_epsg, output_raster, output_csv, project_id):
        super().__init__()
        self.source_info   = source_info
        self.lon_min       = lon_min
        self.lat_min       = lat_min
        self.lon_max       = lon_max
        self.lat_max       = lat_max
        self.scale_eff     = scale_eff
        self.crs_epsg      = crs_epsg
        self.output_raster = output_raster
        self.output_csv    = output_csv
        self.project_id    = project_id
        self._cancelar     = False

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

            banda_str = src['banda']
            if '{year}' in banda_str and 'year' in src:
                banda_str = banda_str.replace('{year}', str(src['year']))

            col_str = src['collection']
            if '{year}' in col_str and 'year' in src:
                col_str = col_str.replace('{year}', str(src['year']))

            self.log.emit(f"Fuente        : {src['label']}")
            self.log.emit(f"Asset GEE     : {col_str}")
            self.log.emit(f"Banda         : {banda_str}")
            self.log.emit(f"Escala        : {self.scale_eff} m")
            self.log.emit(f"Proyeccion    : {crs_str}")

            # Cargar imagen
            if src.get('is_glcfcs30d'):
                year = src.get('year', 2022)
                if year in [1985, 1990, 1995]:
                    col = ee.ImageCollection('projects/sat-io/open-datasets/GLC-FCS30D/five-years').mosaic()
                    b_name = {1985: 'b1', 1990: 'b2', 1995: 'b3'}[year]
                elif 2000 <= year <= 2022:
                    col = ee.ImageCollection('projects/sat-io/open-datasets/GLC-FCS30D/annual').mosaic()
                    b_name = f'b{year - 1999}'
                else:
                    raise RuntimeError(f"GLC_FCS30D no tiene datos para el año {year}. Use 1985, 1990, 1995 o 2000-2022.")
                
                imagen = col.select([b_name], [banda_str])
            elif src.get('is_collection'):
                col = ee.ImageCollection(col_str)
                if 'year' in src:
                    col = col.filterDate(f"{src['year']}-01-01", f"{src['year']}-12-31")
                # Selecciona la primera banda (ej. 'b1') y la renombra a 'classification' o lo que pida src['banda']
                b_orig = src.get('banda_original', 0)
                imagen = col.mosaic().select([b_orig], [banda_str])
            else:
                imagen = ee.Image(col_str).select(banda_str)

            imagen = (imagen
                .clip(region)
                .toInt16()
                .reproject(crs=crs_str, scale=self.scale_eff)
                .unmask(-9999)
            )
            self.progreso.emit(10)

            # Estimar tamano
            cos_lat = math.cos(math.radians((self.lat_min + self.lat_max) / 2.0))
            ancho_m = (self.lon_max - self.lon_min) * 111320.0 * cos_lat
            alto_m  = (self.lat_max - self.lat_min) * 111320.0
            total_px = max(1, int(ancho_m / self.scale_eff)) * \
                       max(1, int(alto_m  / self.scale_eff))
            peso_mb  = total_px * 2 / 1_048_576
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

    # ── Generar CSV Landuse_lookup ─────────────────────────────────────────────
    def _generar_csv(self, ee, imagen, region, src):
        """
        Usa reduceRegion con frequencyHistogram para obtener las clases presentes
        en el raster recortado, luego cruza con la tabla SWAT embebida y genera
        el CSV Landuse_lookup.csv con columnas LANDUSE_ID, SWAT_CODE.
        """
        try:
            self.log.emit("Generando Landuse_lookup.csv...")
            banda = src['banda']
            if '{year}' in banda and 'year' in src:
                banda = banda.replace('{year}', str(src['year']))
            histo = imagen.reduceRegion(
                reducer   = ee.Reducer.frequencyHistogram(),
                geometry  = region,
                scale     = self.scale_eff,
                maxPixels = 1e13,
                bestEffort= True,
            )
            clases_dict = histo.get(banda).getInfo()
            if not clases_dict:
                self.log.emit("Histograma vacio; CSV no generado.")
                return False
            ids_presentes = sorted([int(k) for k in clases_dict.keys()])
            # Filtrar nodata
            ids_presentes = [i for i in ids_presentes if i != -9999]
            self.log.emit(
                f"Clases presentes en AOI: {len(ids_presentes)} — {ids_presentes}")
            # Lookup embebido
            lookup = {r['LANDUSE_ID']: r['SWAT_CODE'] for r in src['tabla_swat']}
            with open(self.output_csv, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(['LANDUSE_ID', 'SWAT_CODE'])
                for lid in ids_presentes:
                    code = lookup.get(lid, f'LU{lid:04d}')
                    writer.writerow([lid, code])
            self.log.emit(f"Landuse_lookup.csv guardado: {self.output_csv}")
            return True
        except Exception as ex:
            self.log.emit(f"Error al generar CSV: {ex}")
            return False
