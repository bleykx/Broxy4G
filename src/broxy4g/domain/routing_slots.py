"""Allocation des tables de routage dédiées aux uplinks gérés.

Chaque uplink (dongle mobile ou interface hôte portant un proxy local) reçoit
une table de routage et une marque firewall uniques, prises dans une base
commune afin d'éviter toute collision entre sources.
"""

from collections.abc import Iterable

# Table de routage dédiée : 10001, 10002, ...
BASE_ROUTE_TABLE_ID = 10000


def allocate_routing_slot(used_table_ids: Iterable[int | None]) -> tuple[int, int]:
    """Retourne (route_table_id, fwmark) libre, la marque valant table - base."""
    taken = {t for t in used_table_ids if t}
    table_id = BASE_ROUTE_TABLE_ID + 1
    while table_id in taken:
        table_id += 1
    return table_id, table_id - BASE_ROUTE_TABLE_ID
