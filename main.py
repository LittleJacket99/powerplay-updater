import requests
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from bs4 import BeautifulSoup

from concurrent.futures import ThreadPoolExecutor, as_completed

from datetime import datetime
from zoneinfo import ZoneInfo

import time
import os
import json


# =========================
# CONFIGURAZIONE
# =========================

SPREADSHEET_ID = "1pzjZZUS_bJRGDXCzLCPoTjf1AXXlb1PR55H9YcYuzi4"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/136.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,it;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Connection": "keep-alive",
    "Referer": "https://inara.cz/",
    "DNT": "1",
    "Upgrade-Insecure-Requests": "1"
}


# =========================
# GOOGLE AUTH
# =========================

creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])

creds = Credentials.from_service_account_info(
    creds_dict,
    scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
)

client = gspread.authorize(creds)


# =========================
# REQUEST SICURA
# =========================

def safe_request(url):

    session = requests.Session()
    session.headers.update(HEADERS)

    for attempt in range(3):

        print("\n==============================")
        print(f"[REQUEST] Tentativo {attempt + 1}")
        print(f"[URL] {url}")

        try:

            r = session.get(
                url,
                timeout=30,
                allow_redirects=True
            )

            print(f"[STATUS CODE] {r.status_code}")
            print(f"[CONTENT LENGTH] {len(r.text)}")

            preview = r.text[:500].replace("\n", " ")

            print("[HTML PREVIEW]")
            print(preview)

            print("==============================\n")

            if (
                r.status_code == 200
                and "something happened" not in r.text.lower()
            ):
                print("[REQUEST SUCCESS]")
                return r

        except Exception as e:
            print(f"[REQUEST ERROR] {e}")

        time.sleep(5)

    print("[REQUEST FAILED]")
    return None


# =========================
# POWERPLAY
# =========================

SHEET_POWERPLAY = "Mahon"

EXCLUDED_COLUMNS = [
    "Priority",
    "Dist",
    "Distance",
    "",
    "Opposing power",
    "Opposing"
]

URLS_POWERPLAY = {
    "Controlled": "https://inara.cz/elite/power-controlled/3/",
    "Exploited": "https://inara.cz/elite/power-exploited/3/",
    "Contested": "https://inara.cz/elite/power-contested/3/"
}


def run_powerplay():

    print("\n==============================")
    print("AVVIO POWERPLAY")
    print("==============================\n")

    all_data = []

    def fetch(label, url):

        print(f"\n[{label}] Inizio scraping")

        try:

            r = safe_request(url)

            if not r:
                print(f"[{label}] Request fallita")
                return []

            print(f"[{label}] Parsing HTML")

            s = BeautifulSoup(r.text, "html.parser")

            tables = s.find_all("table")

            print(f"[{label}] Tabelle trovate: {len(tables)}")

            if not tables:
                print(f"[{label}] Nessuna tabella trovata")
                return []

            t = (
                s.find("table", class_="dataTable")
                or max(tables, key=lambda x: len(x.find_all("tr")))
            )

            cols = [th.get_text(strip=True) for th in t.find_all("th")]

            print(f"[{label}] Colonne trovate:")
            print(cols)

            rows = []

            for row in t.find_all("tr"):

                cells = row.find_all(["td", "th"])

                if not cells or row.parent.name == 'thead':
                    continue

                if cells[0].get_text(strip=True) == cols[0]:
                    continue

                rd = {
                    cols[i]: cells[i].get_text(strip=True).replace("︎", "").strip()
                    for i in range(len(cells))
                    if i < len(cols) and cols[i] not in EXCLUDED_COLUMNS
                }

                rows.append(rd)

            print(f"[{label}] Righe raccolte: {len(rows)}")

            return rows

        except Exception as e:
            print(f"[PP] Errore {label}: {e}")
            return []

    with ThreadPoolExecutor(max_workers=3) as ex:

        futures = [
            ex.submit(fetch, l, u)
            for l, u in URLS_POWERPLAY.items()
        ]

        for f in as_completed(futures):

            result = f.result()

            print(f"[THREAD] Ricevute {len(result)} righe")

            all_data.extend(result)

    print(f"[POWERPLAY] Totale righe raccolte: {len(all_data)}")

    if not all_data:
        print("[POWERPLAY] Nessun dato raccolto")
        return "❌ Errore Powerplay"

    df = pd.DataFrame(all_data)

    print("[POWERPLAY] DataFrame creato")
    print(df.head())

    try:

        df = df[
            [
                "Star system",
                "State",
                "Under",
                "Reinf",
                "Progress",
                "Updated"
            ]
        ]

        print("[POWERPLAY] Ordine colonne OK")

    except KeyError as e:

        print(f"[PP] Colonne mancanti: {e}")
        print(df.columns.tolist())

        return "❌ Struttura Inara cambiata"

    df[' '] = ""
    df['Systems'] = ""
    df['Last Update'] = ""

    if len(df) > 0:

        now = datetime.now(
            ZoneInfo("Europe/Rome")
        ).strftime("%d/%m/%Y %H:%M")

        df.at[0, 'Systems'] = str(len(df))
        df.at[0, 'Last Update'] = now

    df = df.fillna("")

    print("[POWERPLAY] Connessione Google Sheets")

    sheet = client.open_by_key(
        SPREADSHEET_ID
    ).worksheet(SHEET_POWERPLAY)

    print("[POWERPLAY] Clear sheet")

    sheet.clear()

    print("[POWERPLAY] Upload dati")

    sheet.update(
        [df.columns.tolist()] + df.values.tolist(),
        'A1'
    )

    print("[POWERPLAY] COMPLETATO")

    return f"✅ Mahon: {len(df)} sistemi"


# =========================
# EXCP
# =========================

SHEET_EXCP = "EXCP"


def run_excp():

    print("\n==============================")
    print("AVVIO EXCP")
    print("==============================\n")

    try:

        r = safe_request(
            "https://inara.cz/elite/minorfaction-presence/77761/"
        )

        if not r:
            print("[EXCP] Request fallita")
            return "❌ Request fallita"

        print("[EXCP] Parsing HTML")

        s = BeautifulSoup(r.text, "html.parser")

        tables = s.find_all("table")

        print(f"[EXCP] Tabelle trovate: {len(tables)}")

        if not tables:
            print("[EXCP] Nessuna tabella trovata")
            return "❌ Tabella EXCP non trovata"

        t = (
            s.find("table", class_="dataTable")
            or max(tables, key=lambda x: len(x.find_all("tr")))
        )

        headers = [
            th.get_text(strip=True).upper()
            for th in t.find_all("th")
        ]

        print("[EXCP] Headers trovati:")
        print(headers)

        sys_idx = (
            headers.index("STAR SYSTEM")
            if "STAR SYSTEM" in headers
            else 0
        )

        data = []

        for row in t.find_all("tr"):

            if row.parent.name == 'thead':
                continue

            tags = str(row.get("data-tags", ""))

            if 'ctrl' in tags and 'noctrl' not in tags:

                cells = row.find_all(["td", "th"])

                if not cells or len(cells) <= sys_idx:
                    continue

                name = (
                    cells[sys_idx]
                    .get_text(strip=True)
                    .replace("︎", "")
                    .strip()
                )

                if (
                    name
                    and name.lower() != "star system"
                ):
                    data.append([name])

        print(f"[EXCP] Sistemi raccolti: {len(data)}")

        write = [["Star system", "", "Controlled Systems"]]

        for i, d in enumerate(data):

            if i == 0:
                write.append([d[0], "", len(data)])
            else:
                write.append([d[0], "", ""])

        print("[EXCP] Upload Google Sheets")

        sheet = client.open_by_key(
            SPREADSHEET_ID
        ).worksheet(SHEET_EXCP)

        sheet.clear()
        sheet.update(write, 'A1')

        print("[EXCP] COMPLETATO")

        return f"✅ EXCP: {len(data)} sistemi"

    except Exception as e:

        print(f"[EXCP] Errore: {e}")

        return "❌ Errore EXCP"


# =========================
# MATCH
# =========================

SHEET_MATCH = "EXCP_Mahon"


def run_match():

    print("\n==============================")
    print("AVVIO MATCH")
    print("==============================\n")

    try:

        df_p = pd.DataFrame(
            client.open_by_key(
                SPREADSHEET_ID
            ).worksheet(
                SHEET_POWERPLAY
            ).get_all_records()
        )

        print(f"[MATCH] Righe Powerplay: {len(df_p)}")

        df_e = pd.DataFrame(
            client.open_by_key(
                SPREADSHEET_ID
            ).worksheet(
                SHEET_EXCP
            ).get_all_records()
        )

        print(f"[MATCH] Righe EXCP: {len(df_e)}")

        for c in [' ', 'Systems', '']:

            if c in df_p.columns:
                df_p = df_p.drop(columns=[c])

            if c in df_e.columns:
                df_e = df_e.drop(columns=[c])

        df_p = df_p.rename(
            columns={
                next(
                    c for c in df_p.columns
                    if 'system' in c.lower()
                ): 'Star system'
            }
        )

        print("[MATCH] Merge in corso")

        res = pd.merge(
            df_p,
            df_e,
            on='Star system',
            how='inner'
        ).fillna("")

        print(f"[MATCH] Match trovati: {len(res)}")

        res[' '] = ""
        res['Systems'] = ""

        if len(res) > 0:
            res.at[0, 'Systems'] = str(len(res))

        res = res.fillna("")

        print("[MATCH] Upload Google Sheets")

        sheet = client.open_by_key(
            SPREADSHEET_ID
        ).worksheet(SHEET_MATCH)

        sheet.clear()

        sheet.update(
            [res.columns.tolist()] + res.values.tolist(),
            'A1'
        )

        print("[MATCH] COMPLETATO")

        return f"✅ Incrocio: {len(res)} sistemi"

    except Exception as e:

        print(f"[MATCH] Errore: {e}")

        return "❌ Errore MATCH"


# =========================
# MAIN
# =========================

if __name__ == "__main__":

    print("\n===================================")
    print("=== AVVIO AGGIORNAMENTO DATI ===")
    print("===================================\n")

    res_pp = run_powerplay()
    print(res_pp)

    res_ex = run_excp()
    print(res_ex)

    res_mt = run_match()
    print(res_mt)

    print("\n===================================")
    print("=== AGGIORNAMENTO COMPLETATO ===")
    print("===================================\n")

