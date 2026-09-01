import json
import math
import random
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
import cloudscraper
from bs4 import BeautifulSoup


# ============================================================
# CONFIG
# ============================================================

APPS_SCRIPT_URL = (
    "https://script.google.com/macros/s/"
    "AKfycbxJEP-D3C3DiOy-elE_Q9Jaq01D7g-MJtc3vc1Vvd2cdGABhQF95r98D-Lw95J0SWad"
    "/exec"
)

VAULT_URL = "https://vault.elitehub.eu/graphql"

MAHON_NAME = "Edmund Mahon"
MAHON_POWER_ID = "ce1142ad-61e6-4115-b458-b1ddeadada81"

EXCP_NAME = "Expanders Corp"
EXCP_FACTION_ID = "35b7ec6b-9465-4c62-bc5b-110ee790967a"


# ============================================================
# VAULT SETTINGS
# ============================================================

VAULT_MAHON_BATCH_SIZE = 100
VAULT_CONFLICT_BATCH_SIZE = 100
VAULT_EXCP_BATCH_SIZE = 100

VAULT_MIN_BATCH_SIZE = 10

VAULT_MAX_RETRIES = 3
VAULT_REQUEST_DELAY = 0.25


# ============================================================
# SANITY CHECKS
# ============================================================

MIN_VAULT_MAHON_SYSTEMS = 1000
MIN_VAULT_EXCP_SYSTEMS = 150


# ============================================================
# INARA FALLBACK
# ============================================================

INARA_POWERPLAY_URLS = [
    "https://inara.cz/elite/power-controlled/3/",
    "https://inara.cz/elite/power-exploited/3/",
    "https://inara.cz/elite/power-contested/3/",
]

INARA_EXCP_URL = (
    "https://inara.cz/elite/minorfaction-presence/77761/"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


# ============================================================
# GRAPHQL - MAHON OCCUPIED
# ============================================================

VAULT_MAHON_QUERY = """
query MahonSystems($first: Int!, $offset: Int!) {
  powerplayPowerByName(name: "Edmund Mahon") {
    systemPowerplayPowersByPowerId(
      first: $first
      offset: $offset
    ) {
      totalCount
      nodes {
        system {
          name
          powerplayState
          powerplayStateControlProgress
          powerplayStateReinforcement
          powerplayStateUndermining
          updatedAt
        }
      }
    }
  }
}
"""


# ============================================================
# GRAPHQL - MAHON EXPANSION / CONTESTED
# ============================================================

VAULT_CONFLICT_QUERY = """
query MahonConflicts($first: Int!, $offset: Int!) {
  powerplayPowerByName(name: "Edmund Mahon") {
    powerplayConflictsByPowerId(
      first: $first
      offset: $offset
    ) {
      totalCount
      nodes {
        conflictProgress
        updatedAt

        system {
          name
          powerplayState

          powerplayConflicts {
            totalCount
          }
        }
      }
    }
  }
}
"""


# ============================================================
# GRAPHQL - EXCP
# ============================================================

VAULT_EXCP_QUERY = """
query ExcpSystems(
  $first: Int!,
  $offset: Int!,
  $factionId: UUID!
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
    }
  }
}
"""


# ============================================================
# GENERIC HELPERS
# ============================================================

def clean_text(value):
    if value is None:
        return ""

    value = str(value)
    value = value.replace("︎", "")
    value = value.replace("", "")

    return " ".join(value.split()).strip()


def format_number(value):
    if value is None:
        return ""

    try:
        number = float(value)

        if math.isnan(number) or math.isinf(number):
            return ""

        if number.is_integer():
            return int(number)

        return number

    except (TypeError, ValueError):
        return value


def format_progress(value):
    if value is None:
        return ""

    try:
        percent = float(value) * 100
        text = f"{percent:.2f}".rstrip("0").rstrip(".")
        return f"{text}%"

    except (TypeError, ValueError):
        return ""


def format_relative_time(timestamp_string):
    if not timestamp_string:
        return ""

    try:
        dt = datetime.fromisoformat(
            timestamp_string.replace("Z", "+00:00")
        )

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)

        seconds = max(
            0,
            int(
                (
                    now - dt.astimezone(timezone.utc)
                ).total_seconds()
            ),
        )

        if seconds < 60:
            return (
                "1 second ago"
                if seconds == 1
                else f"{seconds} seconds ago"
            )

        minutes = seconds // 60

        if minutes < 60:
            return (
                "1 minute ago"
                if minutes == 1
                else f"{minutes} minutes ago"
            )

        hours = minutes // 60

        if hours < 24:
            return (
                "1 hour ago"
                if hours == 1
                else f"{hours} hours ago"
            )

        days = hours // 24

        if days < 7:
            return (
                "1 day ago"
                if days == 1
                else f"{days} days ago"
            )

        weeks = days // 7

        if weeks < 5:
            return (
                "1 week ago"
                if weeks == 1
                else f"{weeks} weeks ago"
            )

        months = days // 30

        if months < 12:
            return (
                "1 month ago"
                if months == 1
                else f"{months} months ago"
            )

        years = days // 365

        return (
            "1 year ago"
            if years == 1
            else f"{years} years ago"
        )

    except Exception:
        return timestamp_string


def is_query_cost_error(exc):
    text = str(exc).lower()

    return (
        "query cost" in text
        or "cost limit" in text
    )


def reduce_batch_size(current_batch):
    if current_batch <= VAULT_MIN_BATCH_SIZE:
        return None

    return max(
        VAULT_MIN_BATCH_SIZE,
        current_batch // 2,
    )


# ============================================================
# APPS SCRIPT
# ============================================================

def post_apps_script(payload, max_retries=3):
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(
                APPS_SCRIPT_URL,
                json=payload,
                timeout=90,
            )

            response.raise_for_status()

            try:
                result = response.json()

            except Exception:
                raise RuntimeError(
                    "Apps Script ha restituito una risposta non JSON: "
                    f"{response.text[:500]}"
                )

            if result.get("status") != "ok":
                raise RuntimeError(
                    f"Errore Apps Script: {result}"
                )

            return result

        except Exception as exc:
            last_error = exc

            print(
                f"[Apps Script] Tentativo "
                f"{attempt}/{max_retries} fallito: {exc}"
            )

            if attempt < max_retries:
                time.sleep(5 * attempt)

    raise RuntimeError(
        "Apps Script non raggiungibile dopo "
        f"{max_retries} tentativi"
    ) from last_error


# ============================================================
# VAULT REQUEST
# ============================================================

def vault_post(query, variables):
    payload = {
        "query": query,
        "variables": variables,
    }

    last_error = None

    for attempt in range(1, VAULT_MAX_RETRIES + 1):
        try:
            response = requests.post(
                VAULT_URL,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "Elite-Mahon-Sheets-Updater/2.0",
                },
                timeout=45,
            )

            if response.status_code == 429:
                retry_after = response.headers.get(
                    "Retry-After"
                )

                try:
                    wait = float(retry_after)

                except (TypeError, ValueError):
                    wait = 10 * attempt

                print(
                    f"[Vault] Rate limit. Attendo {wait:.1f}s..."
                )

                time.sleep(wait)
                continue

            response.raise_for_status()

            result = response.json()

            if result.get("errors"):
                error_text = json.dumps(
                    result["errors"],
                    ensure_ascii=False,
                )

                raise RuntimeError(
                    f"GraphQL errors: {error_text}"
                )

            data = result.get("data")

            if data is None:
                raise RuntimeError(
                    "Vault non ha restituito il campo data"
                )

            return data

        except Exception as exc:
            last_error = exc

            print(
                f"[Vault] Tentativo "
                f"{attempt}/{VAULT_MAX_RETRIES} "
                f"fallito: {exc}"
            )

            if is_query_cost_error(exc):
                raise

            if attempt < VAULT_MAX_RETRIES:
                time.sleep(5 * attempt)

    raise RuntimeError(
        "Vault non raggiungibile"
    ) from last_error


# ============================================================
# VAULT - MAHON OCCUPIED
# ============================================================

def fetch_mahon_occupied_from_vault():
    print()
    print("=" * 70)
    print(
        "[VAULT] MAHON - "
        "EXPLOITED / FORTIFIED / STRONGHOLD"
    )
    print("=" * 70)

    offset = 0
    total_count = None
    batch_size = VAULT_MAHON_BATCH_SIZE

    rows = {}

    while True:
        try:
            data = vault_post(
                VAULT_MAHON_QUERY,
                {
                    "first": batch_size,
                    "offset": offset,
                },
            )

        except RuntimeError as exc:
            if is_query_cost_error(exc):
                new_batch = reduce_batch_size(
                    batch_size
                )

                if new_batch is None:
                    raise

                print(
                    "[Vault Mahon] "
                    "Query troppo costosa. "
                    f"Batch {batch_size} -> {new_batch}"
                )

                batch_size = new_batch
                continue

            raise

        power = data.get(
            "powerplayPowerByName"
        )

        if not power:
            raise RuntimeError(
                "Edmund Mahon non trovato su Vault"
            )

        connection = power.get(
            "systemPowerplayPowersByPowerId"
        )

        if connection is None:
            raise RuntimeError(
                "Campo systemPowerplayPowersByPowerId assente"
            )

        if total_count is None:
            total_count = int(
                connection.get("totalCount")
                or 0
            )

            if total_count <= 0:
                raise RuntimeError(
                    "Mahon totalCount = 0"
                )

            print(
                "[Vault Mahon] "
                f"Associazioni Power: {total_count}"
            )

        nodes = connection.get(
            "nodes"
        ) or []

        if not nodes:
            if offset < total_count:
                raise RuntimeError(
                    "Pagina Mahon vuota prima della fine: "
                    f"{offset}/{total_count}"
                )

            break

        for node in nodes:
            system = (
                (node or {})
                .get("system")
            )

            if not system:
                continue

            name = clean_text(
                system.get("name")
            )

            state = system.get(
                "powerplayState"
            )

            if not name:
                continue

            if state == "Exploited":
                sheet_state = "Exploited"

            elif state == "Fortified":
                sheet_state = "Fortified"

            elif state == "Stronghold":
                sheet_state = "Stronghold"

            else:
                continue

            row = [
                name,
                sheet_state,
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
                    system.get(
                        "updatedAt"
                    )
                ),
            ]

            rows[name.lower()] = row

        offset += len(nodes)

        print(
            "[Vault Mahon] "
            f"{min(offset, total_count)}/{total_count}"
            f" | Occupied validi: {len(rows)}"
            f" | Batch: {batch_size}"
        )

        if offset >= total_count:
            break

        time.sleep(
            VAULT_REQUEST_DELAY
        )

    return rows


# ============================================================
# VAULT - MAHON EXPANSION / CONTESTED
# ============================================================

def fetch_mahon_conflicts_from_vault():
    print()
    print("=" * 70)
    print(
        "[VAULT] MAHON - "
        "EXPANSION / CONTESTED"
    )
    print("=" * 70)

    rows = {}

    offset = 0
    total_count = None
    batch_size = VAULT_CONFLICT_BATCH_SIZE

    while True:
        try:
            data = vault_post(
                VAULT_CONFLICT_QUERY,
                {
                    "first": batch_size,
                    "offset": offset,
                },
            )

        except RuntimeError as exc:
            if is_query_cost_error(exc):
                new_batch = reduce_batch_size(
                    batch_size
                )

                if new_batch is None:
                    raise

                print(
                    "[Vault Conflict] "
                    "Query troppo costosa. "
                    f"Batch {batch_size} -> {new_batch}"
                )

                batch_size = new_batch
                continue

            raise

        power = data.get(
            "powerplayPowerByName"
        )

        if not power:
            raise RuntimeError(
                "Edmund Mahon non trovato "
                "nella query conflicts"
            )

        connection = power.get(
            "powerplayConflictsByPowerId"
        )

        if connection is None:
            raise RuntimeError(
                "Campo powerplayConflictsByPowerId assente"
            )

        if total_count is None:
            total_count = int(
                connection.get("totalCount")
                or 0
            )

            print(
                "[Vault Conflict] "
                f"Conflitti Mahon: {total_count}"
            )

        nodes = connection.get(
            "nodes"
        ) or []

        if not nodes:
            if offset < total_count:
                raise RuntimeError(
                    "Pagina Conflict vuota prima della fine: "
                    f"{offset}/{total_count}"
                )

            break

        for conflict in nodes:
            if not conflict:
                continue

            system = conflict.get(
                "system"
            )

            if not system:
                continue

            name = clean_text(
                system.get("name")
            )

            if not name:
                continue

            if (
                system.get("powerplayState")
                != "Unoccupied"
            ):
                continue

            all_conflicts = (
                system.get("powerplayConflicts")
                or {}
            )

            conflict_count = int(
                all_conflicts.get(
                    "totalCount"
                )
                or 0
            )

            if conflict_count <= 0:
                continue

            if conflict_count == 1:
                sheet_state = "Expansion"

            else:
                sheet_state = "Contested"

            row = [
                name,
                sheet_state,
                "",
                "",
                format_progress(
                    conflict.get(
                        "conflictProgress"
                    )
                ),
                format_relative_time(
                    conflict.get(
                        "updatedAt"
                    )
                ),
            ]

            rows[name.lower()] = row

        offset += len(nodes)

        print(
            "[Vault Conflict] "
            f"{min(offset, total_count)}/{total_count}"
            f" | Expansion/Contested: {len(rows)}"
            f" | Batch: {batch_size}"
        )

        if offset >= total_count:
            break

        time.sleep(
            VAULT_REQUEST_DELAY
        )

    return rows


# ============================================================
# VAULT - MAHON COMPLETO
# ============================================================

def fetch_powerplay_from_vault():
    occupied = (
        fetch_mahon_occupied_from_vault()
    )

    conflicts = (
        fetch_mahon_conflicts_from_vault()
    )

    combined = dict(
        conflicts
    )

    # Gli stati occupied hanno priorità
    # su eventuali conflict obsoleti.
    combined.update(
        occupied
    )

    rows = list(
        combined.values()
    )

    rows.sort(
        key=lambda row:
        row[0].lower()
    )

    if (
        len(rows)
        < MIN_VAULT_MAHON_SYSTEMS
    ):
        raise RuntimeError(
            "Dataset Mahon Vault sospetto: "
            f"solo {len(rows)} sistemi"
        )

    state_counts = {}

    for row in rows:
        state = row[1]

        state_counts[state] = (
            state_counts.get(
                state,
                0,
            )
            + 1
        )

    print()
    print("-" * 70)
    print(
        "[Vault Mahon] RISULTATO"
    )
    print("-" * 70)

    for state in [
        "Stronghold",
        "Fortified",
        "Exploited",
        "Expansion",
        "Contested",
    ]:
        print(
            f"{state:<12}: "
            f"{state_counts.get(state, 0)}"
        )

    print(
        f"{'TOTAL':<12}: "
        f"{len(rows)}"
    )

    print("-" * 70)

    return rows


# ============================================================
# VAULT - EXCP
# ============================================================

def fetch_excp_from_vault():
    print()
    print("=" * 70)
    print(
        "[VAULT] EXCP CONTROLLED SYSTEMS"
    )
    print("=" * 70)

    offset = 0
    total_count = None
    batch_size = VAULT_EXCP_BATCH_SIZE

    systems = set()

    while True:
        try:
            data = vault_post(
                VAULT_EXCP_QUERY,
                {
                    "first": batch_size,
                    "offset": offset,
                    "factionId": EXCP_FACTION_ID,
                },
            )

        except RuntimeError as exc:
            if is_query_cost_error(exc):
                new_batch = reduce_batch_size(
                    batch_size
                )

                if new_batch is None:
                    raise

                print(
                    "[Vault EXCP] "
                    "Query troppo costosa. "
                    f"Batch {batch_size} -> {new_batch}"
                )

                batch_size = new_batch
                continue

            raise

        connection = data.get(
            "systems"
        )

        if connection is None:
            raise RuntimeError(
                "Campo systems assente "
                "nella query EXCP"
            )

        if total_count is None:
            total_count = int(
                connection.get("totalCount")
                or 0
            )

            if total_count <= 0:
                raise RuntimeError(
                    "EXCP totalCount = 0"
                )

            print(
                "[Vault EXCP] "
                f"Controlled systems: {total_count}"
            )

        nodes = connection.get(
            "nodes"
        ) or []

        if not nodes:
            if offset < total_count:
                raise RuntimeError(
                    "Pagina EXCP vuota prima della fine: "
                    f"{offset}/{total_count}"
                )

            break

        for node in nodes:
            name = clean_text(
                (node or {}).get("name")
            )

            if name:
                systems.add(
                    name
                )

        offset += len(nodes)

        print(
            "[Vault EXCP] "
            f"{min(offset, total_count)}/{total_count}"
            f" | Batch: {batch_size}"
        )

        if offset >= total_count:
            break

        time.sleep(
            VAULT_REQUEST_DELAY
        )

    systems = sorted(
        systems,
        key=str.lower,
    )

    if (
        len(systems)
        < MIN_VAULT_EXCP_SYSTEMS
    ):
        raise RuntimeError(
            "Dataset EXCP Vault sospetto: "
            f"solo {len(systems)} sistemi"
        )

    print(
        "[Vault EXCP] OK: "
        f"{len(systems)} sistemi"
    )

    return systems


# ============================================================
# INARA REQUEST
# ============================================================

def safe_request(
    url,
    attempts=5,
):
    scraper = (
        cloudscraper
        .create_scraper()
    )

    last_error = None

    for attempt in range(
        1,
        attempts + 1,
    ):
        try:
            wait = random.uniform(
                3,
                8,
            )

            print(
                "[Inara] "
                f"Attesa {wait:.1f}s..."
            )

            time.sleep(wait)

            response = scraper.get(
                url,
                headers=HEADERS,
                timeout=45,
            )

            response.raise_for_status()

            html_lower = (
                response.text.lower()
            )

            blocked_markers = [
                "something happened",
                "access denied",
                "captcha",
            ]

            if any(
                marker in html_lower
                for marker
                in blocked_markers
            ):
                raise RuntimeError(
                    "Pagina di blocco/"
                    "errore Inara rilevata"
                )

            return response.text

        except Exception as exc:
            last_error = exc

            print(
                "[Inara] Tentativo "
                f"{attempt}/{attempts} "
                f"fallito: {exc}"
            )

            if attempt < attempts:
                wait = (
                    15 * attempt
                )

                print(
                    "[Inara] Retry fra "
                    f"{wait}s..."
                )

                time.sleep(wait)

    raise RuntimeError(
        "Impossibile scaricare "
        f"{url}"
    ) from last_error


# ============================================================
# INARA - FIND POWERPLAY TABLE
# ============================================================

def find_powerplay_table(
    soup,
):
    for table in soup.find_all(
        "table"
    ):
        headers = [
            clean_text(
                th.get_text(
                    " ",
                    strip=True,
                )
            )
            for th
            in table.find_all(
                "th"
            )
        ]

        if any(
            "star system"
            in header.lower()
            for header
            in headers
        ):
            return table

    return None


# ============================================================
# INARA - PARSE POWERPLAY
# ============================================================

def parse_inara_powerplay_page(
    html,
):
    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    table = find_powerplay_table(
        soup
    )

    if table is None:
        raise RuntimeError(
            "Tabella Powerplay "
            "Inara non trovata"
        )

    headers = [
        clean_text(
            th.get_text(
                " ",
                strip=True,
            )
        )
        for th
        in table.find_all(
            "th"
        )
    ]

    def find_column(*names):
        for name in names:
            wanted = (
                name.lower()
            )

            for index, header in enumerate(
                headers
            ):
                if (
                    header.lower()
                    == wanted
                ):
                    return index

            for index, header in enumerate(
                headers
            ):
                if (
                    wanted
                    in header.lower()
                ):
                    return index

        return None

    indexes = {
        "Star system":
            find_column(
                "star system"
            ),

        "State":
            find_column(
                "state"
            ),

        "Under":
            find_column(
                "under"
            ),

        "Reinf":
            find_column(
                "reinf"
            ),

        "Progress":
            find_column(
                "progress"
            ),

        "Updated":
            find_column(
                "updated"
            ),
    }

    missing = [
        key
        for key, value
        in indexes.items()
        if value is None
    ]

    if missing:
        raise RuntimeError(
            "Colonne Inara mancanti: "
            f"{missing} "
            f"| Header={headers}"
        )

    rows = []

    for tr in table.find_all(
        "tr"
    ):
        cells = tr.find_all(
            "td"
        )

        if not cells:
            continue

        values = [
            clean_text(
                td.get_text(
                    " ",
                    strip=True,
                )
            )
            for td
            in cells
        ]

        try:
            star_system = values[
                indexes[
                    "Star system"
                ]
            ]

            if not star_system:
                continue

            row = [
                star_system,

                values[
                    indexes[
                        "State"
                    ]
                ],

                values[
                    indexes[
                        "Under"
                    ]
                ],

                values[
                    indexes[
                        "Reinf"
                    ]
                ],

                values[
                    indexes[
                        "Progress"
                    ]
                ],

                values[
                    indexes[
                        "Updated"
                    ]
                ],
            ]

        except IndexError:
            continue

        rows.append(
            row
        )

    return rows


# ============================================================
# INARA FALLBACK - MAHON
# ============================================================

def fetch_powerplay_from_inara():
    print()
    print("=" * 70)
    print(
        "[INARA FALLBACK] MAHON"
    )
    print("=" * 70)

    all_rows = []

    for url in (
        INARA_POWERPLAY_URLS
    ):
        print(
            "[Inara Mahon] "
            f"{url}"
        )

        html = safe_request(
            url
        )

        rows = (
            parse_inara_powerplay_page(
                html
            )
        )

        print(
            "[Inara Mahon] "
            f"{len(rows)} righe"
        )

        all_rows.extend(
            rows
        )

    deduplicated = {}

    for row in all_rows:
        name = (
            row[0]
            .lower()
            .strip()
        )

        if name:
            deduplicated[
                name
            ] = row

    rows = list(
        deduplicated.values()
    )

    rows.sort(
        key=lambda row:
            row[0].lower()
    )

    if not rows:
        raise RuntimeError(
            "Inara Mahon: "
            "nessun sistema trovato"
        )

    print(
        "[Inara Mahon] Totale: "
        f"{len(rows)}"
    )

    return rows


# ============================================================
# INARA FALLBACK - EXCP
# ============================================================

def fetch_excp_from_inara():
    print()
    print("=" * 70)
    print(
        "[INARA FALLBACK] EXCP"
    )
    print("=" * 70)

    html = safe_request(
        INARA_EXCP_URL
    )

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    target_table = None
    star_system_index = None

    for table in soup.find_all(
        "table"
    ):
        headers = [
            clean_text(
                th.get_text(
                    " ",
                    strip=True,
                )
            )
            for th
            in table.find_all(
                "th"
            )
        ]

        for index, header in enumerate(
            headers
        ):
            if (
                header.lower()
                == "star system"
            ):
                target_table = table
                star_system_index = index
                break

        if (
            target_table
            is not None
        ):
            break

    if (
        target_table is None
        or
        star_system_index is None
    ):
        raise RuntimeError(
            "Tabella EXCP Inara "
            "non trovata"
        )

    systems = []

    for tr in target_table.find_all(
        "tr"
    ):
        tags = tr.get(
            "data-tags",
            "",
        )

        if isinstance(
            tags,
            list,
        ):
            tags = " ".join(
                tags
            )

        tags = str(
            tags
        ).lower()

        if (
            "ctrl" not in tags
            or
            "noctrl" in tags
        ):
            continue

        cells = tr.find_all(
            "td"
        )

        if (
            len(cells)
            <= star_system_index
        ):
            continue

        name = clean_text(
            cells[
                star_system_index
            ].get_text(
                " ",
                strip=True,
            )
        )

        if name:
            systems.append(
                name
            )

    systems = sorted(
        set(systems),
        key=str.lower,
    )

    if not systems:
        raise RuntimeError(
            "EXCP Inara: "
            "nessun sistema trovato"
        )

    print(
        "[Inara EXCP] "
        f"{len(systems)} sistemi"
    )

    return systems


# ============================================================
# WRITE - MAHON
# ============================================================

def write_mahon(
    rows,
    source,
):
    now_rome = datetime.now(
        ZoneInfo(
            "Europe/Rome"
        )
    ).strftime(
        "%d/%m/%Y %H:%M"
    )

    values = [
        [
            "Star system",
            "State",
            "Under",
            "Reinf",
            "Progress",
            "Updated",
            " ",
            "Systems",
            "Last Update",
        ]
    ]

    for index, row in enumerate(
        rows
    ):
        output_row = list(
            row
        )

        output_row.append(
            ""
        )

        if index == 0:
            output_row.append(
                len(rows)
            )

            output_row.append(
                now_rome
            )

        else:
            output_row.append(
                ""
            )

            output_row.append(
                ""
            )

        values.append(
            output_row
        )

    post_apps_script(
        {
            "action":
                "write",

            "sheet":
                "Mahon",

            "values":
                values,
        }
    )

    print(
        "[Mahon] Scritti "
        f"{len(rows)} sistemi "
        f"| SOURCE={source}"
    )


# ============================================================
# WRITE - EXCP
# ============================================================

def write_excp(
    systems,
    source,
):
    now_rome = datetime.now(
        ZoneInfo("Europe/Rome")
    ).strftime(
        "%d/%m/%Y %H:%M"
    )

    values = [
        [
            "Star system",
            "",
            "Controlled Systems",
            "Last Update",
        ]
    ]

    for index, system in enumerate(
        systems
    ):
        values.append(
            [
                system,
                "",
                (
                    len(systems)
                    if index == 0
                    else ""
                ),
                (
                    now_rome
                    if index == 0
                    else ""
                ),
            ]
        )

    post_apps_script(
        {
            "action": "write",
            "sheet": "EXCP",
            "values": values,
        }
    )

    print(
        "[EXCP] Scritti "
        f"{len(systems)} sistemi "
        f"| SOURCE={source} "
        f"| Last Update={now_rome}"
    )


# ============================================================
# RUN - MAHON
# ============================================================

def run_powerplay():
    try:
        rows = (
            fetch_powerplay_from_vault()
        )

        source = (
            "EliteHub Vault"
        )

    except Exception as vault_error:
        print()
        print("!" * 70)

        print(
            "[MAHON] "
            "VAULT NON UTILIZZABILE"
        )

        print(
            "[MAHON] Motivo: "
            f"{vault_error}"
        )

        print(
            "[MAHON] Passaggio "
            "automatico a Inara"
        )

        print("!" * 70)
        print()

        rows = (
            fetch_powerplay_from_inara()
        )

        source = (
            "Inara fallback"
        )

    write_mahon(
        rows,
        source,
    )

    return {
        "source":
            source,

        "systems":
            len(rows),
    }


# ============================================================
# RUN - EXCP
# ============================================================

def run_excp():
    try:
        systems = (
            fetch_excp_from_vault()
        )

        source = (
            "EliteHub Vault"
        )

    except Exception as vault_error:
        print()
        print("!" * 70)

        print(
            "[EXCP] "
            "VAULT NON UTILIZZABILE"
        )

        print(
            "[EXCP] Motivo: "
            f"{vault_error}"
        )

        print(
            "[EXCP] Passaggio "
            "automatico a Inara"
        )

        print("!" * 70)
        print()

        systems = (
            fetch_excp_from_inara()
        )

        source = (
            "Inara fallback"
        )

    write_excp(
        systems,
        source,
    )

    return {
        "source":
            source,

        "systems":
            len(systems),
    }


# ============================================================
# MATCH - EXCP_Mahon
# ============================================================

def run_match():
    print()
    print("=" * 70)

    print(
        "[MATCH] EXCP ∩ MAHON"
    )

    print("=" * 70)

    result = (
        post_apps_script(
            {
                "action":
                    "match",
            }
        )
    )

    matches = result.get(
        "matches",
        0,
    )

    print(
        "[MATCH] "
        f"{matches} sistemi"
    )

    return matches


# ============================================================
# MAIN
# ============================================================

def main():
    print()
    print("=" * 70)
    print(
        "ELITE DANGEROUS DATA UPDATE"
    )
    print("=" * 70)
    print()

    start = time.time()

    # 1. MAHON
    powerplay_result = (
        run_powerplay()
    )

    time.sleep(
        random.uniform(
            2,
            4,
        )
    )

    # 2. EXCP
    excp_result = (
        run_excp()
    )

    time.sleep(
        random.uniform(
            2,
            4,
        )
    )

    # 3. MATCH
    matches = (
        run_match()
    )

    elapsed = (
        time.time()
        - start
    )

    print()
    print("=" * 70)
    print(
        "AGGIORNAMENTO COMPLETATO"
    )
    print("=" * 70)

    print(
        "Mahon source     : "
        f"{powerplay_result['source']}"
    )

    print(
        "Mahon systems    : "
        f"{powerplay_result['systems']}"
    )

    print(
        "EXCP source      : "
        f"{excp_result['source']}"
    )

    print(
        "EXCP systems     : "
        f"{excp_result['systems']}"
    )

    print(
        "EXCP_Mahon       : "
        f"{matches}"
    )

    print(
        "Durata           : "
        f"{elapsed:.1f}s"
    )

    print("=" * 70)


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
