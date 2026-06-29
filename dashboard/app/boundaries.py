from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .queries import BoundaryMode, DashboardQueries


@dataclass
class BoundaryLayer:
    mode: BoundaryMode
    geojson: dict[str, Any]
    ids: list[str]
    id_to_name: dict[str, str]


class BoundaryService:
    def __init__(self, queries: DashboardQueries) -> None:
        self.queries = queries
        self._cache: dict[BoundaryMode, BoundaryLayer] = {}

    def load(self, mode: BoundaryMode) -> BoundaryLayer:
        cached = self._cache.get(mode)
        if cached is not None:
            return cached

        rows = self.queries.fetch_boundary_features(mode)
        features: list[dict[str, Any]] = []
        ids: list[str] = []
        id_to_name: dict[str, str] = {}

        for row in rows:
            boundary_id = str(row["id"])
            name = row.get("name") or boundary_id
            geojson_text = row.get("geojson")
            if not geojson_text:
                continue
            try:
                geometry = json.loads(geojson_text)
            except json.JSONDecodeError:
                continue
            features.append(
                {
                    "type": "Feature",
                    "id": boundary_id,
                    "properties": {"id": boundary_id, "name": name},
                    "geometry": geometry,
                }
            )
            ids.append(boundary_id)
            id_to_name[boundary_id] = str(name)

        layer = BoundaryLayer(
            mode=mode,
            geojson={"type": "FeatureCollection", "features": features},
            ids=ids,
            id_to_name=id_to_name,
        )
        self._cache[mode] = layer
        return layer
