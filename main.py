import math
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests


# ============================================================
# CONFIG
# ============================================================

VAULT_URL = "https://vault.elitehub.eu/graphql"

APPS_SCRIPT_URL = (
    "https://script.google.com/macros/s/"
    "AKfycbxJEP-D3C3DiOy-elE_Q9Jaq01D7g-MJtc3vc1Vvd2cdGABhQF95r98D-Lw95J0SWad/"
    "exec"
)

# Expanders Corp
EXCP_FACTION_ID = "35b7ec6b-9465-4c62-bc5b-110ee790967a"

# Edmund Mahon
MAHON_POWER_ID = "ce1142ad-61e6-4115-b458-b1ddeadada81"
MAHON_POWER_NAME = "Edmund Mahon"


# ============================================================
# VAULT SETTINGS
# ============================================================

VAULT_BATCH_SIZE = 50
VAULT_MIN_BATCH_SIZE = 10

# Con sole ~5 richieste non serve essere aggressivi.
VAULT_REQUEST_DELAY = 1.5

VAULT_TIMEOUT = 60
VAULT_MAX_RETRIES = 5


# ============================================================
# FORMATTING
# ============================================================

def format_number(value):
    if value is None:
        return ""

    if isinstance(value, float) and value.is_integer():
        return int(value)

    return value


def format_progress(value):
    """
    Vault restituisce progress come valore 0..1.
    Esempio:
        0.587608 -> 58.7608%
    """
    if value is None:
        return ""

    percentage = value * 100

    # Mantiene una precisione utile senza zeri inutili.
    text = f"{percentage:.4f}".rstrip("0").rstrip(".")

    return f"{text}%"


def parse_vault_datetime(value):
    if not value:
        return None

    try:
        # Vault attualmente restituisce timestamp senza Z:
        # 2026-09-08T22:40:57.785000
        dt = datetime.fromisoformat(value)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(timezone.utc)

    except Exception:
        return None


def format_relative_time(value):
    dt = parse_vault_datetime(value)

    if dt is None:
        return ""

    now = datetime.now(timezone.utc)
    seconds = max(0, int((now - dt).total_seconds()))

    if seconds < 60:
        return "just now"

    minutes = seconds // 60

    if minutes < 60:
        return f"{minutes} min ago"

    hours = minutes // 60

    if hours < 24:
        return f"{hours} hours ago"

    days = hours // 24

    if days < 30:
        return f"{days} days ago"

    months = days // 30

    if months < 12:
        return f"{months} months ago"

    years = days // 365
    return f"{years} years ago"


def rome_now():
    return datetime.now(
        ZoneInfo("Europe/Rome")
    ).strftime("%d/%m/%Y %H:%M")


# ============================================================
# APPS SCRIPT
# ============================================================

def post_apps_script(sheet_name, values):
    payload = {
        "action": "write",
        "sheet": sheet_name,
        "values": values,
    }

    last_error = None

    for attempt in range(1, 4):
        try:
            print(
                f"[Sheets] Scrittura {sheet_name} "
                f"({len(values) - 1} righe)..."
            )

            response = requests.post(
                APPS_SCRIPT_URL,
                json=payload,
                timeout=60,
            )

            response.raise_for_status()

            print(
                f"[Sheets] {sheet_name} aggiornato con successo."
            )

            return

        except Exception as exc:
            last_error = exc

            print(
                f"[Sheets] Errore tentativo {attempt}/3 "
                f"su {sheet_name}: {exc}"
            )

            if attempt < 3:
                time.sleep(5 * attempt)

    raise RuntimeError(
        f"Impossibile aggiornare {sheet_name}: {last_error}"
    )


# ============================================================
# VAULT GRAPHQL
# ============================================================

EXCP_POWERPLAY_QUERY = """
query ExcpPowerplay(
    $first: Int!,
    $offset: Int!,
    $factionId: UUID!,
    $powerId: UUID!
) {
    systems(
        first: $first
        offset: $offset
        condition: {
            controllingFactionId: $factionId
        }
    ) {
        totalCount

        nodes {
            name

            powerplayState
            powerplayStateControlProgress
            powerplayStateReinforcement
            powerplayStateUndermining
            updatedAt

            systemPowerplayPowers(
                condition: {
                    powerId: $powerId
                }
            ) {
                totalCount
            }

            powerplayConflicts {
                totalCount

                nodes {
                    conflictProgress
                    updatedAt

                    power {
                        name
                    }
                }
            }
        }
    }
}
"""


def is_query_cost_error(data):
    errors = data.get("errors") or []

    for error in errors:
        message = str(error.get("message", "")).lower()

        if (
            "query cost" in message
            and "limit" in message
        ):
            return True

    return False


def vault_request(query, variables):
    last_error = None

    for attempt in range(1, VAULT_MAX_RETRIES + 1):
        try:
            response = requests.post(
                VAULT_URL,
                json={
                    "query": query,
                    "variables": variables,
                },
                timeout=VAULT_TIMEOUT,
            )

            # Rate limit
            if response.status_code == 429:
                retry_after = response.headers.get(
                    "Retry-After",
                    "5",
                )

                try:
                    wait = max(1, int(float(retry_after)))
                except Exception:
                    wait = 5

                print(
                    f"[Vault] Rate limit. "
                    f"Retry-After: {wait}s"
                )

                time.sleep(wait)
                continue

            response.raise_for_status()

            data = response.json()

            if data.get("errors"):
                messages = "; ".join(
                    str(error.get("message"))
                    for error in data["errors"]
                )

                raise RuntimeError(
                    f"GraphQL error: {messages}"
                )

            return data["data"]

        except Exception as exc:
            last_error = exc

            print(
                f"[Vault] Errore tentativo "
                f"{attempt}/{VAULT_MAX_RETRIES}: {exc}"
            )

            if attempt < VAULT_MAX_RETRIES:
                time.sleep(3 * attempt)

    raise RuntimeError(
        f"Vault non disponibile: {last_error}"
    )


# ============================================================
# EXCP + POWERPLAY
# ============================================================

def fetch_excp_powerplay():
    """
    Recupera SOLO i sistemi controllati da Expanders Corp,
    insieme ai dati Powerplay necessari per stabilire
    quali appartengono/interessano a Edmund Mahon.

    Nessuna scansione globale dei sistemi Mahon.
    """

    systems = []

    offset = 0
    batch_size = VAULT_BATCH_SIZE
    total_count = None

    print()
    print("========================================")
    print(" VAULT: EXCP + POWERPLAY")
    print("========================================")

    while total_count is None or offset < total_count:

        variables = {
            "first": batch_size,
            "offset": offset,
            "factionId": EXCP_FACTION_ID,
            "powerId": MAHON_POWER_ID,
        }

        try:
            data = vault_request(
                EXCP_POWERPLAY_QUERY,
                variables,
            )

        except RuntimeError as exc:

            # Se il costo della query dovesse cambiare
            # in futuro, riduciamo automaticamente il batch.
            if (
                "query cost" in str(exc).lower()
                and batch_size > VAULT_MIN_BATCH_SIZE
            ):
                new_batch = max(
                    VAULT_MIN_BATCH_SIZE,
                    batch_size // 2,
                )

                print(
                    f"[Vault] Query troppo costosa. "
                    f"Batch {batch_size} -> {new_batch}"
                )

                batch_size = new_batch
                continue

            raise

        result = data["systems"]

        if total_count is None:
            total_count = result["totalCount"]

            print(
                f"[Vault] Sistemi EXCP controllati: "
                f"{total_count}"
            )

        nodes = result.get("nodes") or []

        systems.extend(nodes)

        offset += len(nodes)

        print(
            f"[Vault] {min(offset, total_count)}/"
            f"{total_count} sistemi"
            f" | Batch: {batch_size}"
        )

        if not nodes:
            break

        if offset < total_count:
            time.sleep(VAULT_REQUEST_DELAY)

    print(
        f"[Vault] Recuperati {len(systems)} "
        f"sistemi EXCP."
    )

    return systems


# ============================================================
# POWERPLAY CLASSIFICATION
# ============================================================

def find_mahon_conflict(system):
    conflicts = (
        system.get("powerplayConflicts", {})
        .get("nodes", [])
    )

    for conflict in conflicts:
        power = conflict.get("power") or {}

        if power.get("name") == MAHON_POWER_NAME:
            return conflict

    return None


def build_mahon_row(system):
    """
    Restituisce una riga EXCP_Mahon oppure None
    se il sistema non è associato a Mahon.
    """

    relation = (
        system.get("systemPowerplayPowers") or {}
    )

    mahon_relation_count = (
        relation.get("totalCount") or 0
    )

    if mahon_relation_count == 0:
        return None

    name = system.get("name", "")
    state = system.get("powerplayState")

    # --------------------------------------------------------
    # OCCUPIED
    # --------------------------------------------------------

    if state in (
        "Exploited",
        "Fortified",
        "Stronghold",
    ):
        return [
            name,
            state,
            format_number(
                system.get(
                    "powerplayStateUndermining"
                )
            ),
            format_number(
                system.get(
                    "powerplayStateReinforcement"
                )
            ),
            format_progress(
                system.get(
                    "powerplayStateControlProgress"
                )
            ),
            format_relative_time(
                system.get("updatedAt")
            ),
        ]

    # --------------------------------------------------------
    # UNOCCUPIED
    # --------------------------------------------------------

    if state == "Unoccupied":

        conflict = find_mahon_conflict(system)

        # Relazione Mahon presente ma nessun conflict Mahon:
        # situazione anomala/incompleta.
        if conflict is None:
            print(
                f"[WARN] {name}: relazione Mahon presente "
                f"ma conflict Mahon assente."
            )

            return None

        conflicts_info = (
            system.get("powerplayConflicts") or {}
        )

        total_conflicts = (
            conflicts_info.get("totalCount") or 0
        )

        if total_conflicts == 1:
            output_state = "Expansion"
        else:
            output_state = "Contested"

        return [
            name,
            output_state,
            "",
            "",
            format_progress(
                conflict.get("conflictProgress")
            ),
            format_relative_time(
                conflict.get("updatedAt")
                or system.get("updatedAt")
            ),
        ]

    # Relazione Mahon presente ma stato non riconosciuto.
    print(
        f"[WARN] {name}: stato Powerplay "
        f"non gestito: {state}"
    )

    return None


# ============================================================
# BUILD SHEETS
# ============================================================

def build_excp_values(systems):
    systems_sorted = sorted(
        systems,
        key=lambda x: x.get("name", "").lower(),
    )

    values = [
        [
            "Star system",
            "",
            "Controlled Systems",
            "Last Update",
        ]
    ]

    count = len(systems_sorted)
    timestamp = rome_now()

    for index, system in enumerate(systems_sorted):
        values.append(
            [
                system.get("name", ""),
                "",
                count if index == 0 else "",
                timestamp if index == 0 else "",
            ]
        )

    return values


def build_excp_mahon_values(systems):
    rows = []

    for system in systems:
        row = build_mahon_row(system)

        if row is not None:
            rows.append(row)

    rows.sort(
        key=lambda row: row[0].lower()
    )

    count = len(rows)
    timestamp = rome_now()

    values = [
        [
            "Star system",
            "State",
            "Under",
            "Reinf",
            "Progress",
            "Updated",
            "",
            "Systems",
            "Last Update",
        ]
    ]

    for index, row in enumerate(rows):

        values.append(
            row
            + [""]
            + [
                count if index == 0 else "",
                timestamp if index == 0 else "",
            ]
        )

    return values, rows


# ============================================================
# SUMMARY
# ============================================================

def print_summary(excp_count, mahon_rows):
    states = {}

    for row in mahon_rows:
        state = row[1]
        states[state] = states.get(state, 0) + 1

    print()
    print("========================================")
    print(" RISULTATO")
    print("========================================")
    print(f"EXCP controlled : {excp_count}")
    print(f"EXCP_Mahon      : {len(mahon_rows)}")
    print()

    for state in (
        "Stronghold",
        "Fortified",
        "Exploited",
        "Expansion",
        "Contested",
    ):
        print(
            f"{state:<12}: "
            f"{states.get(state, 0)}"
        )


# ============================================================
# MAIN
# ============================================================

def main():
    start = time.time()

    print()
    print("========================================")
    print(" ELITE VAULT - EXCP POWERPLAY")
    print("========================================")

    # --------------------------------------------------------
    # 1. Vault
    # --------------------------------------------------------

    systems = fetch_excp_powerplay()

    if not systems:
        raise RuntimeError(
            "Vault ha restituito zero sistemi EXCP. "
            "I fogli NON verranno modificati."
        )

    # --------------------------------------------------------
    # 2. Costruzione dati in memoria
    # --------------------------------------------------------

    excp_values = build_excp_values(systems)

    excp_mahon_values, mahon_rows = (
        build_excp_mahon_values(systems)
    )

    # Protezione ulteriore:
    # se per qualche anomalia Vault restituisse EXCP
    # ma nessun Mahon, non distruggiamo il foglio esistente.
    if not mahon_rows:
        raise RuntimeError(
            "Nessun sistema EXCP_Mahon rilevato. "
            "Risultato anomalo: i fogli NON verranno modificati."
        )

    # --------------------------------------------------------
    # 3. Scrittura
    # --------------------------------------------------------

    post_apps_script(
        "EXCP",
        excp_values,
    )

    time.sleep(2)

    post_apps_script(
        "EXCP_Mahon",
        excp_mahon_values,
    )

    # --------------------------------------------------------
    # 4. Summary
    # --------------------------------------------------------

    print_summary(
        len(systems),
        mahon_rows,
    )

    elapsed = time.time() - start

    print()
    print(
        f"Completato in {elapsed:.1f} secondi."
    )


if __name__ == "__main__":
    main()
