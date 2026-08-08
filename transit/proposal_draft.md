---
title: "Final Project Proposal"
---

**Project title:** Muni Vehicle Activity Stream  
**Student names:** Chantelle Willette and Patrick Crouch  
**Project format:** Two-person team  
**Contribution plan:** Chantelle will lead producer, event-contract, and producer-test work; Patrick will lead consumer, output, replay, and documentation work. Both will jointly design the architecture, integrate and validate the end-to-end path, review the proposal/report, and rehearse the complete system. Work will remain approximately 50-50, and both partners will be able to explain every component.

# 1. Problem Summary

Transit analysts and application developers need a current, machine-readable view of Muni service activity. This project will stream Muni vehicle observations and produce one-minute active-vehicle counts by route, plus stale and invalid-record counts. The course-sized scope is one agency, one realtime feed, one primary Kafka topic, and one inspectable JSONL output; BART, dashboards, station proximity, and BigQuery are stretch goals.

# 2. Planned Data Source and Classification

**Data source and official URL:** 511 SF Bay GTFS-Realtime Vehicle Positions, https://api.511.org/transit/vehiclepositions  
**Data owner:** Metropolitan Transportation Commission (MTC)  
**Classification:** Realtime with deterministic replay fallback.  
**Why:** The API supplies changing vehicle-position snapshots; cached protobuf snapshots can be replayed record by record.  
**Access and limitations:** A 511 token is required; the default limit is 60 requests/hour/token. Records may be missing identifiers, routes, coordinates, or current timestamps, and the API may be unavailable. No credentials will be submitted.  
**Review path:** A non-cloud local demo will use bundled, public GTFS-RT protobuf fixtures, pinned dependencies, Docker Compose, and one run command. Live polling and GCP sinks are optional.

# 3. Architecture Sketch

```text
OTHER / BATCH                         REALTIME STREAMING LAYER
cached protobuf fixtures ----\
                                > poller/replay producer -> Pydantic validation
511 Muni VehiclePositions -----/              |
                                               v
                                muni.vehicle_positions.v1
                                  key = vehicle_id; schema v1
                                               |
                                               v
                                  one-minute Python consumer
                                               |
                                               v
                         route_vehicle_counts.jsonl + run_summary.json
```

Each valid Kafka message represents one vehicle observation. The producer owns decoding and contract compliance; the consumer owns deduplication, one-minute aggregation, and output. Invalid events are recorded separately for evaluation.

# 4. Planned Tools and Packages

Python 3.11: application code; Apache Kafka via Docker Compose: broker; `confluent-kafka`: producer/consumer client; `gtfs-realtime-bindings`: protobuf decoding; Pydantic: validation and JSON Schema; `requests`: optional live poller; `pytest`: repeatable tests; JSONL: inspectable output.

# 5. Feasibility Risks and Plan

**Minimum result:** One local command replays cached snapshots through Kafka and produces route counts, rejection evidence, and a run summary.  
**Risks:** API limits/outages, missing fields, duplicates, Kafka setup, and time.  
**Fallback:** Use fixtures only, retain one topic and consumer, and omit cloud sinks, dashboards, Trip Updates, BART, and station joins.  
**Milestones:** Extract parser and define contract; publish fixtures; build consumer/output; add deduplication and tests; validate replay; document and present.

# 6. AI Element and Disclosure

**Planned AI element:** Disclosed, verified AI-assisted development. Codex will draft candidate event-contract rules and edge-case tests from selected GTFS-RT fields. Students will retain representative prompts, candidate outputs, and an accepted/rejected decision log. Suggestions will be verified against the GTFS-RT specification, cached fixtures, manual field checks, and passing tests. If AI is unavailable or a suggestion is rejected, students will author the contract/tests directly; runtime ingestion is unaffected.

**Proposal disclosure:** Codex assisted with repository review, feasibility analysis, architecture scoping, and initial proposal drafting. The students will verify the text against the assignment, source code, and official 511 documentation and revise it before submission.
