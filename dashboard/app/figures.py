from __future__ import annotations

from typing import Any

import plotly.graph_objects as go

from .boundaries import BoundaryLayer
from .queries import BoundaryMode

MAP_CENTER = {"lat": 37.7749, "lon": -122.4194}
MAP_ZOOM = 10.65


def build_boundary_map(
    mode: BoundaryMode,
    layer: BoundaryLayer,
    selected_boundary_id: str | None,
) -> go.Figure:
    title = "Neighborhoods" if mode == "neighborhoods" else "Police Districts"
    if not layer.ids:
        fig = go.Figure()
        fig.update_layout(title=f"{title} map (no geometry loaded)")
        return fig

    z_values = [1 if boundary_id == selected_boundary_id else 0 for boundary_id in layer.ids]
    names = [layer.id_to_name.get(boundary_id, boundary_id) for boundary_id in layer.ids]

    fig = go.Figure(
        go.Choroplethmapbox(
            geojson=layer.geojson,
            locations=layer.ids,
            z=z_values,
            featureidkey="properties.id",
            customdata=names,
            colorscale=[
                [0.0, "#A5B4FC"],
                [0.5, "#A5B4FC"],
                [0.5, "#1D4ED8"],
                [1.0, "#1D4ED8"],
            ],
            showscale=False,
            marker_opacity=0.65,
            marker_line_width=1.2,
            marker_line_color="#111827",
            hovertemplate="<b>%{customdata}</b><extra></extra>",
        )
    )
    fig.update_layout(
        mapbox={"style": "carto-positron", "center": MAP_CENTER, "zoom": MAP_ZOOM},
        margin={"l": 0, "r": 0, "t": 40, "b": 0},
        title=f"San Francisco {title}",
        clickmode="event+select",
    )
    return fig


def empty_histogram(title: str, message: str) -> go.Figure:
    fig = go.Figure()
    title_text = _with_top10_subtitle(title)
    fig.update_layout(
        title=title_text,
        xaxis={"visible": False},
        yaxis={"visible": False},
        annotations=[
            {
                "text": message,
                "xref": "paper",
                "yref": "paper",
                "x": 0.5,
                "y": 0.5,
                "showarrow": False,
                "font": {"size": 14},
            }
        ],
    )
    return fig


def build_histogram(title: str, rows: list[dict[str, Any]]) -> go.Figure:
    top_rows = rows[:10]
    x_values = [str(row["category"]) for row in top_rows]
    y_values = [int(row["category_count"]) for row in top_rows]

    fig = go.Figure(go.Bar(x=x_values, y=y_values, marker_color="#2563EB"))
    fig.update_layout(
        title=_with_top10_subtitle(title),
        xaxis_title="Category",
        yaxis_title="Count",
        margin={"l": 40, "r": 20, "t": 45, "b": 80},
    )
    fig.update_xaxes(categoryorder="total descending", tickangle=-35)
    return fig


def _with_top10_subtitle(title: str) -> str:
    if "<sup>" in title:
        return title
    return f"{title}<br><sup>Top 10 categories by count</sup>"
