from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any

from dash import Dash, Input, Output, State, dash_table, dcc, html, no_update

from .bigquery_client import create_client
from .boundaries import BoundaryLayer, BoundaryService
from .cache import TTLCache
from .config import AppConfig
from .figures import build_boundary_map, build_histogram, empty_histogram
from .queries import BoundaryMode, DashboardQueries
from .text_to_sql import (
    AgentError,
    TextToSqlAgent,
    format_bytes,
)
from .time_utils import PACIFIC_TZ, pacific_date_range_to_utc, utc_to_pacific_date

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
LOGGER = logging.getLogger(__name__)


def _default_date_range(queries: DashboardQueries) -> tuple[str, str, str, str]:
    bounds = queries.fetch_time_bounds_utc()
    if bounds is None:
        end_local = dt.datetime.now(tz=PACIFIC_TZ).date()
        start_local = end_local - dt.timedelta(days=7)
        start_text = start_local.isoformat()
        end_text = end_local.isoformat()
        return start_text, end_text, start_text, end_text
    min_utc, max_utc = bounds
    min_date = utc_to_pacific_date(min_utc)
    max_date = utc_to_pacific_date(max_utc)
    return min_date, max_date, min_date, max_date


def _format_int(value: int | None) -> str:
    if value is None:
        return "N/A"
    return f"{int(value):,}"


def _format_delay_minutes_with_direction(value_sec: float | None) -> str:
    if value_sec is None:
        return "N/A"
    value_sec = float(value_sec)
    if abs(value_sec) < 0.5:
        return "0.0 min on time"
    direction = "late" if value_sec > 0 else "early"
    minutes = abs(value_sec) / 60.0
    return f"{minutes:.1f} min {direction}"


def _format_percent(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{float(value):.1f}%"


BEDROOM_BUCKETS: tuple[tuple[str, str], ...] = (
    ("studio", "Studio"),
    ("1bd", "1 BD"),
    ("2bd", "2 BD"),
    ("3bd", "3 BD"),
    ("4bd+", "4 BD+"),
)


def _format_currency(value: float | int | None) -> str:
    if value is None:
        return "N/A"
    return f"${float(value):,.0f}"


def _format_date_label(value: object) -> str:
    if value is None:
        return "today"
    if isinstance(value, dt.datetime):
        value = value.date()
    if isinstance(value, dt.date):
        return value.strftime("%b %-d, %Y")
    try:
        return dt.date.fromisoformat(str(value)).strftime("%b %-d, %Y")
    except ValueError:
        return str(value)


def _rental_value_cell(value: float | int | None, count: int | None) -> html.Div:
    count_value = int(count or 0)
    listing_word = "listing" if count_value == 1 else "listings"
    return html.Div(
        [
            html.Div(_format_currency(value), style={"fontSize": "22px", "fontWeight": "700"}),
            html.Div(
                f"{count_value:,} {listing_word}",
                style={"fontSize": "12px", "color": "#6B7280", "marginTop": "2px"},
            ),
        ],
        style={"textAlign": "center", "padding": "8px 6px"},
    )


def _build_rental_comparison(
    rows: list[dict[str, Any]],
    start_date: str | None,
    end_date: str | None,
) -> html.Div:
    by_bucket = {str(row.get("bedroom_bucket")): row for row in rows}
    current_as_of = next(
        (row.get("current_as_of") for row in rows if row.get("current_as_of") is not None),
        dt.datetime.now(tz=PACIFIC_TZ).date(),
    )

    selected_label = "Selected timeframe"
    if start_date and end_date:
        selected_label = f"{_format_date_label(start_date)} – {_format_date_label(end_date)}"

    grid_children: list[Any] = [
        html.Div("", style={"padding": "6px"}),
        *[
            html.Div(label, style={"fontWeight": "700", "textAlign": "center", "padding": "6px"})
            for _, label in BEDROOM_BUCKETS
        ],
        html.Div(
            [
                html.Div("Average listing prices over selected timeframe", style={"fontWeight": "600"}),
                html.Div(selected_label, style={"fontSize": "12px", "color": "#6B7280", "marginTop": "2px"}),
            ],
            style={"padding": "8px 10px"},
        ),
    ]

    for bucket, _ in BEDROOM_BUCKETS:
        row = by_bucket.get(bucket, {})
        grid_children.append(
            _rental_value_cell(
                row.get("selected_average_price"),
                row.get("selected_listing_count"),
            )
        )

    grid_children.append(
        html.Div(
            [
                html.Div("vs current average listing prices", style={"fontWeight": "600"}),
                html.Div(
                    f"as of {_format_date_label(current_as_of)}",
                    style={"fontSize": "12px", "color": "#6B7280", "marginTop": "2px"},
                ),
            ],
            style={"padding": "8px 10px"},
        )
    )

    for bucket, _ in BEDROOM_BUCKETS:
        row = by_bucket.get(bucket, {})
        grid_children.append(
            _rental_value_cell(
                row.get("current_average_price"),
                row.get("current_listing_count"),
            )
        )

    return html.Div(
        [
            html.Div("Rental Market", style={"fontSize": "18px", "fontWeight": "700", "marginBottom": "8px"}),
            html.Div(
                grid_children,
                style={
                    "display": "grid",
                    "gridTemplateColumns": "minmax(230px, 1.45fr) repeat(5, minmax(105px, 1fr))",
                    "alignItems": "stretch",
                    "columnGap": "4px",
                    "rowGap": "2px",
                },
            ),
        ],
        style={
            "border": "1px solid #E5E7EB",
            "borderRadius": "6px",
            "padding": "12px",
            "backgroundColor": "#FFFFFF",
            "overflowX": "auto",
        },
    )


def _parse_local_time(value: str | None, fallback: dt.time) -> dt.time:
    if not value:
        return fallback
    parsed = dt.datetime.strptime(value, "%H:%M").time()
    return parsed.replace(second=0, microsecond=0)


def _selected_heading(mode: BoundaryMode) -> str:
    return "Selected Neighborhood:" if mode == "neighborhoods" else "Selected Police District:"


def _selected_label_children(mode: BoundaryMode, name: str) -> list[html.Div]:
    return [
        html.Div(_selected_heading(mode), style={"fontSize": "13px", "color": "#4B5563"}),
        html.Div(name, style={"fontSize": "24px", "fontWeight": "700", "lineHeight": "1.2"}),
    ]


def build_dashboard_app() -> Dash:
    config = AppConfig.from_env()
    bq_client = create_client(config)
    queries = DashboardQueries(client=bq_client, config=config)
    boundaries = BoundaryService(queries=queries)
    metrics_cache = TTLCache(
        ttl_seconds=config.cache_ttl_seconds,
        max_entries=config.cache_max_entries,
    )

    agent: TextToSqlAgent | None = None
    agent_disabled_reason: str | None = None
    if config.agent_enabled:
        try:
            agent = TextToSqlAgent.create(config=config, bq_client=bq_client)
        except Exception as exc:  # noqa: BLE001
            agent_disabled_reason = str(exc)
            LOGGER.warning(
                "Text-to-SQL agent unavailable, tab will show a friendly error: %s",
                exc,
            )
    else:
        agent_disabled_reason = (
            "The Ask-a-question agent is disabled (DASH_AGENT_ENABLED=false)."
        )

    try:
        default_start_date, default_end_date, min_allowed_date, max_allowed_date = _default_date_range(
            queries=queries
        )
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Could not fetch default date bounds: %s", exc)
        end_local = dt.datetime.now(tz=PACIFIC_TZ).date()
        start_local = end_local - dt.timedelta(days=7)
        default_start_date = start_local.isoformat()
        default_end_date = end_local.isoformat()
        min_allowed_date = default_start_date
        max_allowed_date = default_end_date

    app = Dash(__name__)
    app.title = "SF Interactive Dashboard"

    dashboard_tab_layout = html.Div(
        [
            html.H1("San Francisco Livability Dashboard"),
            html.P(
                "UTC filtering with Pacific UI controls. Click one boundary to load metrics.",
                style={"color": "#4B5563"},
            ),
            html.Div(
                [
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Label("Boundary mode", style={"fontWeight": "600"}),
                                    dcc.RadioItems(
                                        id="mode-toggle",
                                        options=[
                                            {"label": "Neighborhoods", "value": "neighborhoods"},
                                            {"label": "Police Districts", "value": "police_districts"},
                                        ],
                                        value="neighborhoods",
                                    ),
                                ],
                                style={"display": "flex", "flexDirection": "column", "gap": "8px"},
                            ),
                            html.Div(
                                [
                                    html.Label("Date range (Pacific Time)", style={"fontWeight": "600"}),
                                    dcc.DatePickerRange(
                                        id="date-range",
                                        start_date=default_start_date,
                                        end_date=default_end_date,
                                        min_date_allowed=min_allowed_date,
                                        max_date_allowed=max_allowed_date,
                                        display_format="YYYY-MM-DD",
                                        updatemode="bothdates",
                                    ),
                                ],
                                style={"display": "flex", "flexDirection": "column", "gap": "8px"},
                            ),
                            html.Div(
                                [
                                    html.Label("Time window (Pacific Time)", style={"fontWeight": "600"}),
                                    html.Div(
                                        [
                                            dcc.Input(
                                                id="time-start",
                                                type="text",
                                                value="00:00",
                                                debounce=True,
                                                placeholder="HH:MM",
                                                style={"width": "110px"},
                                            ),
                                            html.Span("to", style={"color": "#4B5563"}),
                                            dcc.Input(
                                                id="time-end",
                                                type="text",
                                                value="23:59",
                                                debounce=True,
                                                placeholder="HH:MM",
                                                style={"width": "110px"},
                                            ),
                                        ],
                                        style={"display": "flex", "alignItems": "center", "gap": "8px"},
                                    ),
                                ],
                                style={"display": "flex", "flexDirection": "column", "gap": "8px"},
                            ),
                            html.Div(
                                id="selection-label",
                                style={
                                    "marginTop": "4px",
                                    "color": "#1F2937",
                                    "display": "flex",
                                    "flexDirection": "column",
                                    "gap": "2px",
                                },
                            ),
                        ],
                        style={
                            "display": "flex",
                            "flexDirection": "column",
                            "gap": "16px",
                            "flex": "0 0 250px",
                            "padding": "10px",
                            "border": "1px solid #E5E7EB",
                            "borderRadius": "6px",
                            "backgroundColor": "#FFFFFF",
                            "height": "500px",
                        },
                    ),
                    html.Div(
                        [
                            dcc.Loading(
                                dcc.Graph(id="boundary-map", style={"height": "500px"}),
                                type="default",
                            )
                        ],
                        style={"flex": "1 1 50%", "minWidth": "460px"},
                    ),
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Div("311 Incidents", style={"color": "#4B5563"}),
                                    html.Div(id="kpi-311-total", style={"fontSize": "24px"}),
                                ],
                                style=_kpi_card_style(),
                            ),
                            html.Div(
                                [
                                    html.Div("Police Incidents", style={"color": "#4B5563"}),
                                    html.Div(id="kpi-police-total", style={"fontSize": "24px"}),
                                ],
                                style=_kpi_card_style(),
                            ),
                            html.Div(
                                [
                                    html.Div("Transit Stop Arrivals", style={"color": "#4B5563"}),
                                    html.Div(id="kpi-transit-total", style={"fontSize": "24px"}),
                                ],
                                style=_kpi_card_style(),
                            ),
                            html.Div(
                                [
                                    html.Div("Median Arrival Delay", style={"color": "#4B5563"}),
                                    html.Div(id="kpi-transit-delay-median", style={"fontSize": "24px"}),
                                ],
                                style=_kpi_card_style(),
                            ),
                            html.Div(
                                [
                                    html.Div("% Delays > 5 min", style={"color": "#4B5563"}),
                                    html.Div(id="kpi-transit-delay-over5", style={"fontSize": "24px"}),
                                ],
                                style=_kpi_card_style(),
                            ),
                        ],
                        style={
                            "display": "flex",
                            "flexDirection": "column",
                            "gap": "8px",
                            "flex": "0 0 300px",
                            "height": "500px",
                        },
                    ),
                ],
                style={"display": "flex", "gap": "12px", "alignItems": "stretch"},
            ),
            html.Div(id="rental-market", style={"marginTop": "12px"}),
            html.Div(
                [
                    dcc.Loading(dcc.Graph(id="hist-311"), type="default"),
                    dcc.Loading(dcc.Graph(id="hist-police"), type="default"),
                ],
                style={"display": "grid", "gridTemplateColumns": "1fr 1fr", "gap": "12px", "marginTop": "12px"},
            ),
            html.Div(id="error-message", style={"marginTop": "10px", "color": "#B91C1C"}),
            dcc.Store(id="selected-boundary"),
        ],
        style={"padding": "16px 20px", "fontFamily": "Arial, sans-serif"},
    )

    agent_tab_layout = _build_agent_tab_layout(
        config=config,
        agent_available=agent is not None,
        disabled_reason=agent_disabled_reason,
    )

    app.layout = html.Div(
        [
            dcc.Tabs(
                id="top-tabs",
                value="dashboard",
                children=[
                    dcc.Tab(label="Dashboard", value="dashboard", children=[dashboard_tab_layout]),
                    dcc.Tab(label="Ask a question", value="agent", children=[agent_tab_layout]),
                ],
            ),
        ],
        style={"fontFamily": "Arial, sans-serif"},
    )

    @app.callback(
        Output("selected-boundary", "data"),
        Input("boundary-map", "clickData"),
        State("mode-toggle", "value"),
        State("selected-boundary", "data"),
        prevent_initial_call=True,
    )
    def update_selected_boundary(
        click_data: dict | None,
        mode_value: BoundaryMode,
        current_selection: dict | None,
    ) -> dict | None:
        if not click_data:
            return current_selection

        points = click_data.get("points", [])
        if not points:
            return current_selection
        selected_id = _normalize_boundary_id(points[0].get("location"))
        selected_name = points[0].get("customdata") or selected_id
        if selected_id is None:
            return current_selection
        return {"id": selected_id, "name": str(selected_name), "mode": mode_value}

    @app.callback(
        Output("boundary-map", "figure"),
        Output("selection-label", "children"),
        Output("kpi-311-total", "children"),
        Output("kpi-police-total", "children"),
        Output("kpi-transit-total", "children"),
        Output("kpi-transit-delay-median", "children"),
        Output("kpi-transit-delay-over5", "children"),
        Output("rental-market", "children"),
        Output("hist-311", "figure"),
        Output("hist-police", "figure"),
        Output("error-message", "children"),
        Input("mode-toggle", "value"),
        Input("selected-boundary", "data"),
        Input("date-range", "start_date"),
        Input("date-range", "end_date"),
        Input("time-start", "value"),
        Input("time-end", "value"),
    )
    def refresh_dashboard(
        mode_value: BoundaryMode,
        selected_boundary: dict | None,
        start_date: str | None,
        end_date: str | None,
        start_time: str | None,
        end_time: str | None,
    ):
        try:
            layer = boundaries.load(mode_value)
            selected_id, selected_name = _selection_for_mode(
                layer=layer,
                selected_boundary=selected_boundary,
                mode_value=mode_value,
            )

            map_figure = build_boundary_map(
                mode=mode_value,
                layer=layer,
                selected_boundary_id=selected_id,
            )

            if selected_id is None:
                mode_label = "neighborhood" if mode_value == "neighborhoods" else "police district"
                return (
                    map_figure,
                    _selected_label_children(mode_value, f"(Select a {mode_label})"),
                    "N/A",
                    "N/A",
                    "N/A",
                    "N/A",
                    "N/A",
                    _build_rental_comparison([], start_date, end_date),
                    empty_histogram("311 Incidents by Service Name", "No boundary selected."),
                    empty_histogram("Police Incidents by Category", "No boundary selected."),
                    "",
                )

            start_utc, end_utc = pacific_date_range_to_utc(start_date=start_date, end_date=end_date)
            if start_utc is None or end_utc is None:
                return (
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    "Date range is invalid.",
                )
            if start_utc >= end_utc:
                return (
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    "Date range must have start before end.",
                )
            try:
                local_start_time = _parse_local_time(start_time, dt.time(hour=0, minute=0))
                local_end_time = _parse_local_time(end_time, dt.time(hour=23, minute=59))
            except ValueError:
                return (
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    "Time window is invalid. Use HH:MM format.",
                )
            if local_start_time > local_end_time:
                return (
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    "Time window must have start before end.",
                )

            cache_key = json.dumps(
                {
                    "mode": mode_value,
                    "boundary_id": selected_id,
                    "start_utc": start_utc.isoformat(),
                    "end_utc": end_utc.isoformat(),
                    "local_start_time": local_start_time.isoformat(),
                    "local_end_time": local_end_time.isoformat(),
                },
                sort_keys=True,
            )
            cached = metrics_cache.get(cache_key)
            if cached is None:
                cached = queries.fetch_boundary_metrics(
                    mode=mode_value,
                    boundary_id=selected_id,
                    start_utc=start_utc,
                    end_utc=end_utc,
                    local_start_time=local_start_time,
                    local_end_time=local_end_time,
                )
                metrics_cache.set(cache_key, cached)

            totals = cached["totals"]
            hist_311_rows = cached["hist_311"]
            hist_police_rows = cached["hist_police"]
            rental_rows = cached["rentals"]
            rental_comparison = _build_rental_comparison(
                rental_rows,
                start_date=start_date,
                end_date=end_date,
            )

            hist_311_figure = (
                build_histogram("311 Incidents by Service Name", hist_311_rows)
                if hist_311_rows
                else empty_histogram("311 Incidents by Service Name", "No records for selected range.")
            )
            hist_police_figure = (
                build_histogram("Police Incidents by Category", hist_police_rows)
                if hist_police_rows
                else empty_histogram("Police Incidents by Category", "No records for selected range.")
            )

            return (
                map_figure,
                _selected_label_children(mode_value, selected_name),
                _format_int(totals.get("incidents_311_total")),
                _format_int(totals.get("police_total")),
                _format_int(totals.get("transit_arrivals_total")),
                _format_delay_minutes_with_direction(totals.get("transit_median_delay_sec")),
                _format_percent(totals.get("transit_pct_delay_over_300_sec")),
                rental_comparison,
                hist_311_figure,
                hist_police_figure,
                "",
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("Dashboard refresh failed")
            safe_layer = _safe_layer(boundaries=boundaries, mode_value=mode_value)
            map_figure = build_boundary_map(mode_value, safe_layer, None)
            return (
                map_figure,
                _selected_label_children(mode_value, "(unavailable)"),
                "N/A",
                "N/A",
                "N/A",
                "N/A",
                "N/A",
                _build_rental_comparison([], start_date, end_date),
                empty_histogram("311 Incidents by Service Name", "Data unavailable."),
                empty_histogram("Police Incidents by Category", "Data unavailable."),
                f"Error: {exc}",
            )

    if agent is not None:
        _register_agent_callbacks(app=app, agent=agent, config=config)

    return app


def _register_agent_callbacks(app: Dash, agent: TextToSqlAgent, config: AppConfig) -> None:
    @app.callback(
        Output("agent-sql", "value"),
        Output("agent-explanation", "children"),
        Output("agent-estimate", "children"),
        Output("agent-generated-sql-store", "data"),
        Output("agent-generate-error", "children"),
        Output("agent-results-table", "columns"),
        Output("agent-results-table", "data"),
        Output("agent-run-status", "children"),
        Input("agent-generate-btn", "n_clicks"),
        State("agent-question", "value"),
        prevent_initial_call=True,
    )
    def generate_sql(_n_clicks: int, question: str | None):
        try:
            generated = agent.generate(question=question or "")
        except AgentError as exc:
            return "", "", "", None, str(exc), [], [], ""
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("Agent generate failed")
            return "", "", "", None, f"Unexpected agent error: {exc}", [], [], ""

        explanation_children = html.Div(
            [
                html.Div("Model explanation", style={"fontWeight": "600"}),
                html.Div(generated.explanation or "(no explanation returned)"),
                html.Div(
                    f"Model: {generated.model}",
                    style={"color": "#6B7280", "fontSize": "12px", "marginTop": "4px"},
                ),
            ]
        )
        estimate_children = html.Div(
            [
                html.Span("Dry-run estimate: ", style={"color": "#4B5563"}),
                html.Span(
                    f"~{format_bytes(generated.estimated_bytes_processed)} scanned",
                    style={"fontWeight": "600"},
                ),
                html.Span(
                    f" (cap {format_bytes(config.llm_max_bytes_billed)})",
                    style={"color": "#6B7280", "marginLeft": "6px"},
                ),
            ]
        )
        return (
            generated.executable_sql,
            explanation_children,
            estimate_children,
            {
                "sql": generated.executable_sql,
                "question": generated.question,
            },
            "",
            [],
            [],
            "",
        )

    @app.callback(
        Output("agent-results-table", "columns", allow_duplicate=True),
        Output("agent-results-table", "data", allow_duplicate=True),
        Output("agent-run-status", "children", allow_duplicate=True),
        Output("agent-run-error", "children"),
        Input("agent-run-btn", "n_clicks"),
        State("agent-generated-sql-store", "data"),
        prevent_initial_call=True,
    )
    def run_generated_sql(_n_clicks: int, pending: dict | None):
        if not pending or not pending.get("sql"):
            return [], [], "", "Generate SQL first, then click Run."
        try:
            result = agent.execute(sql=pending["sql"])
        except AgentError as exc:
            return [], [], "", str(exc)
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("Agent execute failed")
            return [], [], "", f"Query failed: {exc}"

        columns = [{"name": name, "id": name} for name in result.columns]
        data = _stringify_rows_for_table(result.rows)
        status = (
            f"Returned {len(result.rows):,} row(s) in {result.duration_ms} ms, "
            f"billed {format_bytes(result.total_bytes_billed)}."
        )
        return columns, data, status, ""


def _safe_layer(boundaries: BoundaryService, mode_value: BoundaryMode) -> BoundaryLayer:
    try:
        return boundaries.load(mode_value)
    except Exception:  # noqa: BLE001
        return BoundaryLayer(mode=mode_value, geojson={"type": "FeatureCollection", "features": []}, ids=[], id_to_name={})


def _selection_for_mode(
    layer: BoundaryLayer,
    selected_boundary: dict | None,
    mode_value: BoundaryMode,
) -> tuple[str | None, str | None]:
    if not selected_boundary:
        return None, None
    if selected_boundary.get("mode") != mode_value:
        return None, None
    selected_id = _normalize_boundary_id(selected_boundary.get("id"))
    if selected_id is None:
        return None, None
    if selected_id not in layer.id_to_name:
        return None, None
    return selected_id, layer.id_to_name[selected_id]

def _normalize_boundary_id(raw_value: object) -> str | None:
    if raw_value is None:
        return None
    text = str(raw_value).strip()
    if not text or text.lower() == "none":
        return None
    # Plotly sometimes emits numeric IDs as floats (e.g., "53.0").
    # Normalize integer-like values to stable string IDs used in GeoJSON.
    try:
        as_float = float(text)
    except (TypeError, ValueError):
        return text
    if as_float.is_integer():
        return str(int(as_float))
    return text


def _kpi_card_style() -> dict[str, str]:
    return {
        "border": "1px solid #E5E7EB",
        "borderRadius": "6px",
        "padding": "8px 10px",
        "backgroundColor": "#F9FAFB",
        "flex": "1 1 0",
        "display": "flex",
        "flexDirection": "column",
        "justifyContent": "center",
    }


def _build_agent_tab_layout(
    config: AppConfig,
    agent_available: bool,
    disabled_reason: str | None = None,
) -> html.Div:
    header = html.Div(
        [
            html.H1("Ask a question"),
            html.P(
                (
                    f"Powered by {config.llm_model} against "
                    f"`{config.bq_project}.{config.agent_dataset}`. "
                    "Type a plain-English question; we'll show the SQL Gemini writes "
                    "before running it."
                ),
                style={"color": "#4B5563"},
            ),
        ]
    )

    if not agent_available:
        return html.Div(
            [
                header,
                html.Div(
                    _agent_disabled_message(config, reason=disabled_reason),
                    style={
                        "padding": "12px",
                        "border": "1px solid #FCA5A5",
                        "backgroundColor": "#FEF2F2",
                        "color": "#7F1D1D",
                        "borderRadius": "6px",
                    },
                ),
            ],
            style={"padding": "16px 20px"},
        )

    return html.Div(
        [
            header,
            html.Div(
                [
                    html.Label("Your question", style={"fontWeight": "600"}),
                    dcc.Textarea(
                        id="agent-question",
                        placeholder=(
                            "e.g. Which neighborhood had the most 311 requests "
                            "in the last 7 days?"
                        ),
                        style={
                            "width": "100%",
                            "height": "90px",
                            "padding": "8px",
                            "fontFamily": "inherit",
                        },
                    ),
                    html.Div(
                        [
                            html.Button(
                                "Generate SQL",
                                id="agent-generate-btn",
                                n_clicks=0,
                                style=_agent_button_style(primary=True),
                            ),
                            html.Div(
                                id="agent-generate-error",
                                style={"color": "#B91C1C", "marginLeft": "12px"},
                            ),
                        ],
                        style={
                            "display": "flex",
                            "alignItems": "center",
                            "gap": "8px",
                            "marginTop": "8px",
                        },
                    ),
                ],
                style={"marginBottom": "20px"},
            ),
            html.Div(
                [
                    html.Div("Generated SQL", style={"fontWeight": "600"}),
                    dcc.Loading(
                        dcc.Textarea(
                            id="agent-sql",
                            value="",
                            readOnly=True,
                            style={
                                "width": "100%",
                                "height": "220px",
                                "padding": "8px",
                                "fontFamily": "'SFMono-Regular', Consolas, monospace",
                                "fontSize": "13px",
                                "backgroundColor": "#F9FAFB",
                            },
                        ),
                        type="default",
                    ),
                    html.Div(id="agent-explanation", style={"marginTop": "8px"}),
                    html.Div(id="agent-estimate", style={"marginTop": "4px"}),
                    html.Div(
                        [
                            html.Button(
                                "Run query",
                                id="agent-run-btn",
                                n_clicks=0,
                                style=_agent_button_style(primary=False),
                            ),
                            html.Div(
                                id="agent-run-status",
                                style={"color": "#4B5563", "marginLeft": "12px"},
                            ),
                        ],
                        style={
                            "display": "flex",
                            "alignItems": "center",
                            "gap": "8px",
                            "marginTop": "12px",
                        },
                    ),
                    html.Div(
                        id="agent-run-error",
                        style={"color": "#B91C1C", "marginTop": "8px"},
                    ),
                ],
                style={"marginBottom": "20px"},
            ),
            html.Div(
                [
                    html.Div("Results", style={"fontWeight": "600", "marginBottom": "6px"}),
                    dcc.Loading(
                        dash_table.DataTable(
                            id="agent-results-table",
                            columns=[],
                            data=[],
                            page_size=25,
                            fixed_rows={"headers": True},
                            style_table={
                                "overflowX": "auto",
                                "maxHeight": "500px",
                            },
                            style_cell={
                                "textAlign": "left",
                                "padding": "6px 8px",
                                "fontFamily": "Arial, sans-serif",
                                "fontSize": "13px",
                                "minWidth": "80px",
                                "maxWidth": "400px",
                                "whiteSpace": "normal",
                            },
                            style_header={
                                "backgroundColor": "#F3F4F6",
                                "fontWeight": "600",
                            },
                        ),
                        type="default",
                    ),
                ]
            ),
            dcc.Store(id="agent-generated-sql-store"),
        ],
        style={"padding": "16px 20px"},
    )


def _agent_button_style(primary: bool) -> dict[str, str]:
    base = {
        "padding": "8px 14px",
        "border": "1px solid #D1D5DB",
        "borderRadius": "6px",
        "cursor": "pointer",
        "fontWeight": "600",
    }
    if primary:
        base.update({"backgroundColor": "#2563EB", "color": "#FFFFFF", "border": "1px solid #2563EB"})
    else:
        base.update({"backgroundColor": "#FFFFFF", "color": "#111827"})
    return base


def _agent_disabled_message(config: AppConfig, reason: str | None = None) -> str:
    reason_prefix = f"{reason} " if reason else "The Ask-a-question agent could not start. "
    return (
        f"{reason_prefix}"
        "Confirm the runtime service account has roles/aiplatform.user in "
        f"project '{config.llm_project}', that the '{config.agent_dataset}' "
        f"dataset exists in '{config.bq_project}' and contains queryable tables, "
        "and that DASH_AGENT_ENABLED is not set to false."
    )


def _stringify_rows_for_table(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        display_row: dict[str, Any] = {}
        for key, value in row.items():
            if value is None:
                display_row[key] = ""
            elif isinstance(value, (dict, list)):
                display_row[key] = json.dumps(value, default=str)
            else:
                display_row[key] = value
        out.append(display_row)
    return out


app = build_dashboard_app()
server = app.server


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8050)
