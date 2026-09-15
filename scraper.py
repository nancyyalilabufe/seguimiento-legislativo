"""
Scraper de proyectos de CEDOP (Camara de Diputados de Catamarca)
------------------------------------------------------------------
Este programa recorre los proyectos de cedop.gob.ar uno por uno
(usando el ID interno que vimos en la URL, ej: /buscar/Proyecto/9613)
y guarda los datos de cada uno en un archivo CSV (se abre con Excel).

Como usarlo:
    python scraper.py

La primera vez, se recomienda probar con un rango chico (ver las
variables ID_INICIO y ID_FIN mas abajo) para confirmar que todo
funciona antes de correrlo con miles de proyectos.
"""

import csv
import time
import requests
from bs4 import BeautifulSoup

# ======================= CONFIGURACION =======================
# Cambia estos numeros segun lo que quieras hacer.

ID_INICIO = 1          # desde que ID empezar (solo se usa la primera vez)
ID_FIN = 999999        # tope muy alto: en la practica, el freno automatico
                        # de "no encontrados seguidos" es el que decide
                        # cuando parar cada vez que corre

ARCHIVO_SALIDA = "proyectos_cedop.csv"

# Cuantos "no existe" seguidos toleramos antes de asumir que
# llegamos al final de lo que hay cargado y frenar solos.
# Con el historial completo le damos mas margen, por si hay
# huecos en la numeracion en algun tramo.
MAX_FALLOS_SEGUIDOS = 50

# Pausa entre pedido y pedido (en segundos). No la bajes de 0.5:
# es importante no saturar el servidor de la Camara.
PAUSA_SEGUNDOS = 0.7

URL_BASE = "https://cedop.gob.ar/buscar/Proyecto/"

# ======================= FUNCIONES =======================


def leer_proyecto(id_proyecto):
    """
    Pide la pagina de un proyecto puntual y extrae sus datos.
    Devuelve un diccionario con los datos, o None si el proyecto
    no existe (o la pagina dio error).
    """
    url = URL_BASE + str(id_proyecto)

    try:
        respuesta = requests.get(url, timeout=15)
    except requests.RequestException as error:
        print(f"  [ID {id_proyecto}] Error de conexion: {error}")
        return None

    # Si el servidor devuelve un error HTTP (por ejemplo 500),
    # lo tratamos como "no existe".
    if respuesta.status_code != 200:
        return None

    html = respuesta.text

    # La pagina de error de Laravel (la que vimos con "ErrorException")
    # tiene esta frase en el HTML. Si aparece, el proyecto no existe.
    if "ErrorException" in html or "Attempt to read property" in html:
        return None

    sopa = BeautifulSoup(html, "html.parser")
    texto_pagina = sopa.get_text(" ", strip=True)

    # Si la pagina no tiene la palabra "Proyecto N" en ningun lado,
    # probablemente tampoco es una pagina valida de proyecto.
    if "Proyecto N" not in texto_pagina:
        return None

    datos = {"id_interno": id_proyecto}

    # --- Numero publico de proyecto ---
    # Esta dentro de un <div class="borde-ley">Proyecto N° 536</div>
    caja_numero = sopa.find("div", class_="borde-ley")
    datos["numero_proyecto"] = caja_numero.get_text(strip=True) if caja_numero else ""

    # --- Extracto ---
    # Esta dentro de un <div class="borde-extracto">...</div>
    caja_extracto = sopa.find("div", class_="borde-extracto")
    datos["extracto"] = caja_extracto.get_text(" ", strip=True) if caja_extracto else ""

    # --- Campos tipo "Fecha:", "Año:", "Estado:", etc ---
    # Cada uno es un <li> que contiene dos <p>: el primero es la
    # etiqueta en negrita (ej "Fecha:") y el segundo es el valor.
    etiquetas_buscadas = [
        "Fecha", "Año", "Estado", "Proyecto",
        "Expediente Electrónico", "TEXTO ORIGINAL",
    ]
    for etiqueta in etiquetas_buscadas:
        datos[etiqueta] = ""

    for li in sopa.find_all("li"):
        parrafos = li.find_all("p")
        if len(parrafos) < 2:
            continue
        texto_etiqueta = parrafos[0].get_text(strip=True).rstrip(":")
        texto_valor = parrafos[1].get_text(strip=True)
        for etiqueta in etiquetas_buscadas:
            if texto_etiqueta == etiqueta:
                datos[etiqueta] = texto_valor

    datos["Autores"] = extraer_autores(sopa)

    return datos


def extraer_autores(sopa):
    """
    Busca la tabla "Autor/es" del proyecto y devuelve los nombres
    encontrados, separados por punto y coma si hay mas de uno.
    Devuelve cadena vacia si no encuentra la tabla o no hay autores.
    """
    encabezado = sopa.find("h2", string=lambda t: t and "Autor" in t)
    if not encabezado:
        return ""

    tabla = encabezado.find_next("table")
    if not tabla:
        return ""

    cuerpo = tabla.find("tbody")
    filas = cuerpo.find_all("tr") if cuerpo else []

    nombres = []
    for fila in filas:
        celdas = fila.find_all("td")
        # La segunda celda (indice 1) es "Apellido y Nombre/s"
        if len(celdas) >= 2:
            nombre = celdas[1].get_text(strip=True)
            if nombre:
                nombres.append(nombre)

    return "; ".join(nombres)


def buscar_ultimo_id_guardado():
    """
    Si ya existe un archivo de resultados de una corrida anterior,
    busca cual fue el ultimo ID que se guardo, para poder retomar
    desde ahi en vez de empezar de cero.
    Devuelve None si el archivo no existe todavia.
    """
    import os
    if not os.path.exists(ARCHIVO_SALIDA):
        return None

    ultimo_id = None
    with open(ARCHIVO_SALIDA, "r", newline="", encoding="utf-8-sig") as archivo:
        lector = csv.DictReader(archivo, delimiter=";")
        for fila in lector:
            try:
                ultimo_id = int(fila["id_interno"])
            except (KeyError, ValueError):
                pass
    return ultimo_id


def migrar_columnas_si_hace_falta(columnas):
    """
    Si ya existe un CSV de una corrida anterior con columnas distintas
    a las actuales (por ejemplo, porque agregamos "Autores" despues),
    reescribe el archivo agregando la columna nueva vacia en las filas
    viejas, sin perder ningun dato ya guardado.
    """
    import os
    if not os.path.exists(ARCHIVO_SALIDA):
        return

    with open(ARCHIVO_SALIDA, "r", newline="", encoding="utf-8-sig") as archivo:
        lector = csv.DictReader(archivo, delimiter=";")
        columnas_actuales = lector.fieldnames
        if columnas_actuales == columnas:
            return  # ya esta al dia, no hay que hacer nada
        filas = list(lector)

    print(
        f"Se detecto un cambio de columnas (antes: {columnas_actuales}). "
        "Actualizando el archivo existente sin perder datos...\n"
    )

    with open(ARCHIVO_SALIDA, "w", newline="", encoding="utf-8-sig") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=columnas, delimiter=";")
        escritor.writeheader()
        for fila in filas:
            fila_completa = {col: fila.get(col, "") for col in columnas}
            escritor.writerow(fila_completa)


def main():
    columnas = [
        "id_interno", "numero_proyecto", "extracto",
        "Fecha", "Año", "Estado", "Proyecto",
        "Expediente Electrónico", "TEXTO ORIGINAL", "Autores",
    ]

    migrar_columnas_si_hace_falta(columnas)

    ultimo_guardado = buscar_ultimo_id_guardado()
    if ultimo_guardado is not None and ultimo_guardado >= ID_INICIO:
        id_inicio_real = ultimo_guardado + 1
        modo_apertura = "a"  # append: sigue agregando al archivo existente
        escribir_encabezado = False
        print(
            f"Se encontro una corrida anterior. Retomando desde el ID "
            f"{id_inicio_real} (ya habia {ultimo_guardado - ID_INICIO + 1} "
            "guardados).\n"
        )
    else:
        id_inicio_real = ID_INICIO
        modo_apertura = "w"  # write: arranca un archivo nuevo
        escribir_encabezado = True

    print(f"Recorriendo proyectos desde el ID {id_inicio_real} hasta el {ID_FIN}...")
    print(f"Los resultados se van a guardar en: {ARCHIVO_SALIDA}\n")

    fallos_seguidos = 0
    total_guardados = 0

    with open(ARCHIVO_SALIDA, modo_apertura, newline="", encoding="utf-8-sig") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=columnas, delimiter=";")
        if escribir_encabezado:
            escritor.writeheader()

        id_actual = id_inicio_real
        while id_actual <= ID_FIN:
            datos = leer_proyecto(id_actual)

            if datos is None:
                fallos_seguidos += 1
                print(f"[ID {id_actual}] no encontrado ({fallos_seguidos} seguidos)")
                if fallos_seguidos >= MAX_FALLOS_SEGUIDOS:
                    print(
                        f"\nSe encontraron {MAX_FALLOS_SEGUIDOS} 'no encontrado' "
                        "seguidos. Asumo que llegue al final y freno aca."
                    )
                    break
            else:
                fallos_seguidos = 0
                escritor.writerow(datos)
                archivo.flush()  # guarda en disco al toque, no espera al final
                total_guardados += 1
                print(f"[ID {id_actual}] OK -> {datos.get('numero_proyecto', '')}")

            id_actual += 1
            time.sleep(PAUSA_SEGUNDOS)

    print(f"\nListo. Se guardaron {total_guardados} proyectos en '{ARCHIVO_SALIDA}'.")


if __name__ == "__main__":
    main()
