import requests
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

from bs4 import BeautifulSoup
from flask import Flask, Response
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

from datetime import datetime
from zoneinfo import ZoneInfo

app = Flask(__name__)

# === CONFIGURAZIONE ===
SPREADSHEET_ID = "1pzjZZUS_bJRGDXCzLCPoTjf1AXXlb1PR55H9YcYuzi4"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "en-US,en;q=0.9"
}

# ---------------- POWERPLAY ----------------

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


def safe_request(url):
    for attempt in range(3):
        try:
            print("\n==============================")
            print(f"[REQUEST] Tentativo {attempt + 1}")
            print(f"[URL] {url}")

            r = requests.get(
                url,
                headers=HEADERS,
                timeout=20
            )

            print(f"[STATUS CODE] {r.status_code}")
            print(f"[CONTENT LENGTH] {len(r.text)}")

            print("\n[HTML PREVIEW]")
            print(r.text[:500])
            print("\n==============================")

            if r.status_code == 200:
                return r

        except Exception as e:
            print(f"[REQUEST ERROR] {e}")

        time.sleep(2)

    print("[REQUEST FAILED]")
    return None


def run_powerplay():

    print("\n========== AVVIO POWERPLAY ==========")

    all_data = []

    def fetch(label, url):

        print(f"\n[{label}] Inizio fetch")

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

            print(f"[{label}] Tabella selezionata")

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
                    if i < len(cols)
                    and cols[i] not in EXCLUDED_COLUMNS
                }

                rows.append(rd)

            print(f"[{label}] Righe estratte: {len(rows)}")

            if len(rows) > 0:
                print(f"[{label}] Prima riga:")
                print(rows[0])

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

    print(f"\n[POWERPLAY] Totale righe raccolte: {len(all_data)}")

    if not all_data:
        print("[POWERPLAY] Nessun dato raccolto")
        return "❌ Errore Powerplay"

    df = pd.DataFrame(all_data)

    print("\n[POWERPLAY] DataFrame creato")
    print(df.head())

    print("\n[POWERPLAY] Colonne DataFrame:")
    print(df.columns.tolist())

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

        print("[POWERPLAY] Ordinamento colonne OK")

    except KeyError as e:

        print(f"[PP] Colonne mancanti: {e}")

        return "❌ Struttura Inara cambiata"

    df[' '] = ""
    df['Systems'] = ""
    df['Last Update'] = ""

    if len(df) > 0:

        now = datetime.now(
            ZoneInfo("Europe/Rome")
        ).strftime("%d/%m/%Y %H:%M")

        df.at[0, 'Systems'] = len(df)
        df.at[0, 'Last Update'] = now

    df = df.fillna("")

    print("\n[GOOGLE SHEETS] Connessione in corso")

    creds = Credentials.from_service_account_file(
        "credentials.json",
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
    )

    client = gspread.authorize(creds)

    print("[GOOGLE SHEETS] Login OK")

    sheet = client.open_by_key(
        SPREADSHEET_ID
    ).worksheet(SHEET_POWERPLAY)

    print("[GOOGLE SHEETS] Worksheet aperto")

    print(f"[GOOGLE SHEETS] Scrittura righe: {len(df)}")

    sheet.clear()

    print("[GOOGLE SHEETS] Sheet pulito")

    sheet.update(
        [df.columns.tolist()] + df.values.tolist(),
        'A1'
    )

    print("[GOOGLE SHEETS] Scrittura completata")

    return f"✅ Mahon: {len(df)} sistemi"


# ---------------- EXCP ----------------

SHEET_EXCP = "EXCP"


def run_excp():

    print("\n========== AVVIO EXCP ==========")

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

        sys_idx = headers.index("STAR SYSTEM") if "STAR SYSTEM" in headers else 0

        print(f"[EXCP] STAR SYSTEM index: {sys_idx}")

        data = []

        for row in t.find_all("tr"):

            if row.parent.name == 'thead':
                continue

            tags = str(row.get("data-tags", ""))

            if 'ctrl' in tags and 'noctrl' not in tags:

                cells = row.find_all(["td", "th"])

                if not cells or len(cells) <= sys_idx:
                    continue

                name = cells[sys_idx].get_text(strip=True)

                name = name.replace("︎", "").strip()

                if name and name.lower() != "star system":
                    data.append([name])

        print(f"[EXCP] Sistemi trovati: {len(data)}")

        if len(data) > 0:
            print("[EXCP] Primo sistema:")
            print(data[0])

        write = [["Star system", "", "Controlled Systems"]]

        for i, d in enumerate(data):

            if i == 0:
                write.append([d[0], "", len(data)])
            else:
                write.append([d[0], "", ""])

        print("[EXCP] Connessione Google Sheets")

        creds = Credentials.from_service_account_file(
            "credentials.json",
            scopes=[
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"
            ]
        )

        client = gspread.authorize(creds)

        print("[EXCP] Login OK")

        sheet = client.open_by_key(
            SPREADSHEET_ID
        ).worksheet(SHEET_EXCP)

        print("[EXCP] Worksheet aperto")

        sheet.clear()

        print("[EXCP] Sheet pulito")

        sheet.update(write, 'A1')

        print("[EXCP] Scrittura completata")

        return f"✅ EXCP: {len(data)} sistemi"

    except Exception as e:

        print(f"[EXCP] Errore: {e}")

        return "❌ Errore EXCP"


# ---------------- MATCH ----------------

SHEET_MATCH = "EXCP_Mahon"


def run_match():

    print("\n========== AVVIO MATCH ==========")

    try:

        creds = Credentials.from_service_account_file(
            "credentials.json",
            scopes=[
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"
            ]
        )

        client = gspread.authorize(creds)

        print("[MATCH] Login Google OK")

        df_p = pd.DataFrame(
            client.open_by_key(
                SPREADSHEET_ID
            ).worksheet(SHEET_POWERPLAY).get_all_records()
        )

        print(f"[MATCH] Mahon rows: {len(df_p)}")

        df_e = pd.DataFrame(
            client.open_by_key(
                SPREADSHEET_ID
            ).worksheet(SHEET_EXCP).get_all_records()
        )

        print(f"[MATCH] EXCP rows: {len(df_e)}")

        for c in [' ', 'Systems', '']:

            if c in df_p.columns:
                df_p = df_p.drop(columns=[c])

            if c in df_e.columns:
                df_e = df_e.drop(columns=[c])

        print("[MATCH] Cleanup colonne completato")

        df_p = df_p.rename(
            columns={
                next(
                    c for c in df_p.columns
                    if 'system' in c.lower()
                ): 'Star system'
            }
        )

        print("[MATCH] Rename colonne OK")

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
            res.at[0, 'Systems'] = len(res)

        res = res.fillna("")

        sheet = client.open_by_key(
            SPREADSHEET_ID
        ).worksheet(SHEET_MATCH)

        print("[MATCH] Worksheet aperto")

        sheet.clear()

        print("[MATCH] Sheet pulito")

        sheet.update(
            [res.columns.tolist()] + res.values.tolist(),
            'A1'
        )

        print("[MATCH] Scrittura completata")

        return f"✅ Incrocio: {len(res)} sistemi"

    except Exception as e:

        print(f"[MATCH] Errore: {e}")

        return "❌ Errore MATCH"


# ---------------- ROUTES ----------------

@app.route('/aggiorna-tutto')

def route_full():

    print("\n################################")
    print("######## AVVIO PROCESSO ########")
    print("################################\n")

    res_pp = run_powerplay()
    res_ex = run_excp()
    res_mt = run_match()

    output = f"""=== AGGIORNAMENTO DATI ===

[1] Powerplay Mahon
{res_pp}

[2] Fazione EXCP
{res_ex}

[3] Incrocio dati
{res_mt}

--------------------------
Stato: ✅ COMPLETATO
"""

    print("\n######## FINE PROCESSO ########")

    return Response(output, mimetype="text/plain")


if __name__ == "__main__":

    app.run(
        host='0.0.0.0',
        port=int(os.environ.get("PORT", 8080))
    )