# 🛡️ Faultweaver

### Predictive SRE Incident Investigator

**Trace the cause. Weave the recovery.**

**Evidence-driven incident detection, distributed observability, and policy-gated Kubernetes remediation.**

<p align="center">
  <b>Monitor. Detect. Investigate. Validate. Recover.</b>
</p>

<p align="center">
  <a href="#-overview">Overview</a> ·
  <a href="#-features">Features</a> ·
  <a href="#-architecture">Architecture</a> ·
  <a href="#-dashboard-screenshots">Screenshots</a> ·
  <a href="#-installation">Setup</a> ·
  <a href="#-usage">Usage</a> ·
  <a href="#-incident-investigation">Investigation</a> ·
  <a href="#-technology-stack">Tech Stack</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-Backend-009688?logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white" alt="Docker">
  <img src="https://img.shields.io/badge/PostgreSQL-Database-4169E1?logo=postgresql&logoColor=white" alt="PostgreSQL">
  <img src="https://img.shields.io/badge/Keycloak-Authentication-4D4D4D?logo=keycloak&logoColor=white" alt="Keycloak">
  <img src="https://img.shields.io/badge/Prometheus-Monitoring-E6522C?logo=prometheus&logoColor=white" alt="Prometheus">
  <img src="https://img.shields.io/badge/OpenTelemetry-Observability-000000?logo=opentelemetry&logoColor=white" alt="OpenTelemetry">
  <img src="https://img.shields.io/badge/Grafana-Dashboards-F46800?logo=grafana&logoColor=white" alt="Grafana">
  <img src="https://img.shields.io/badge/Kubernetes-Remediation-326CE5?logo=kubernetes&logoColor=white" alt="Kubernetes">
  <img src="https://img.shields.io/badge/Pytest-Testing-0A9EDC?logo=pytest&logoColor=white" alt="Pytest">
</p>

---

## 📌 Overview

**Faultweaver** is a local-first Site Reliability Engineering (SRE) platform designed to demonstrate how distributed applications can detect abnormal behavior, investigate incidents using correlated telemetry, and validate controlled infrastructure recovery workflows.

Modern distributed systems rarely fail in isolation. A slow database, an unhealthy dependency, or a failing microservice can trigger a chain of errors across an entire application.

Faultweaver addresses this challenge by bringing together **real-time observability, statistical anomaly detection, controlled fault injection, evidence-based incident investigation, and ORION — a safety-first remediation control plane.**

Rather than simply reporting that a service is unhealthy, Faultweaver investigates the available evidence, identifies potentially affected services, ranks diagnostic leads, and evaluates whether a proposed recovery action is safe.

The platform runs locally using Docker Compose and generates its own application traffic and telemetry. It does not depend on pre-recorded datasets.

### 🎯 Core Philosophy

<p align="center">
  <h3>Trace the cause. Weave the recovery.</h3>
  <i>Turn distributed failures into explainable incidents and controlled recovery workflows.</i>
</p>

---

## ✨ Features

### 🔍 1. Distributed Microservices Simulation

Simulates an e-commerce application through three independently running Python FastAPI microservices:

- **Order Service:** Handles order-related application workflows.
- **Inventory Service:** Manages inventory operations and supports controlled fault injection.
- **Payment Service:** Represents payment-processing operations.

PostgreSQL stores application and inventory data, while Keycloak provides local OpenID Connect authentication.

### 📊 2. Unified Observability

Collects and visualizes multiple telemetry signals to provide visibility into distributed application behavior.

- **Prometheus:** Request rates, latency, service health, and HTTP error metrics.
- **OpenTelemetry:** Application instrumentation and telemetry collection.
- **Grafana Loki:** Centralized application log storage.
- **Grafana Tempo:** Distributed trace storage.
- **Grafana:** Dashboards for inspecting application health and performance.

### 🧠 3. Statistical Anomaly Detection

Establishes a baseline using recent service measurements and detects deviations from expected behavior.

- Observes normal application behavior.
- Builds statistical latency baselines.
- Detects abnormal service measurements.
- Identifies unusual latency and error behavior.
- Uses available telemetry to support investigation.
- Reports insufficient evidence rather than presenting uncertain findings as confirmed conclusions.

**Note:** The current detection engine uses statistical baseline analysis, not a trained machine-learning model.

### 🚨 4. Controlled Fault Injection

Creates reproducible service failures without requiring an external dataset.

Supported failure scenarios include:

- Artificial request latency.
- Injected HTTP error responses.
- Repeated inventory failures.
- Combined failure scenarios.

Faults can be introduced while application traffic continues, allowing the observability stack to capture their impact.

### 🔬 5. Evidence-Based Incident Investigation

The Investigator API analyzes service behavior and returns structured incident reports containing:

- Detection status.
- Relevant telemetry evidence.
- Ranked service leads.
- Supporting observations.
- Uncertainties and evidence limitations.
- Recommended diagnostic checks.

This helps distinguish observed symptoms from potential causes.

### 🛡️ 6. ORION — Safety-First Remediation Control Plane

ORION extends the investigation workflow into controlled infrastructure recovery.

Its capabilities include:

- Authenticated incident ingestion.
- Persistent incident storage in PostgreSQL.
- Duplicate-alert detection and deduplication.
- Incident lifecycle management.
- Auditable decision and event history.
- Deterministic remediation planning.
- Fail-closed policy enforcement.
- Kubernetes staging validation.
- Approval-controlled canary execution.
- Health monitoring and rollback handling.

ORION separates a proposed repair from a validated, approved, and successfully completed action.

**A detected incident never automatically grants unrestricted permission to modify infrastructure.**

---

## 🏗️ Architecture

Faultweaver consists of three interconnected engineering layers.

### Application Layer

The simulated e-commerce application contains:

| Service | Responsibility |
|---|---|
| Order Service | Order processing |
| Inventory Service | Inventory management and fault injection |
| Payment Service | Payment-processing simulation |
| PostgreSQL | Persistent application and incident data |
| Keycloak | Authentication and OpenID Connect |

### Observability & Investigation Layer

| Component | Responsibility |
|---|---|
| Prometheus | Metrics collection and querying |
| OpenTelemetry | Application instrumentation |
| Grafana Loki | Centralized logging |
| Grafana Tempo | Distributed tracing |
| Grafana | Telemetry visualization |
| Investigator API | Anomaly detection and incident investigation |

### ORION Control Layer

| Component | Responsibility |
|---|---|
| Incident Manager | Incident ingestion and lifecycle |
| Deduplication Engine | Prevents repeated incident processing |
| Deterministic Planner | Generates bounded repair proposals |
| Policy Gate | Enforces safety and authorization rules |
| Kubernetes Staging | Validates proposed infrastructure changes |
| Approval Gate | Requires explicit authorization |
| Canary Controller | Monitors controlled deployment |
| Rollback Workflow | Handles unsuccessful health checks |
| Audit Timeline | Records incident and remediation events |

### System Architecture Diagram

```text
                 E-COMMERCE APPLICATION
              ┌─────────┬───────────┬─────────┐
              │  Order  │ Inventory │ Payment │
              └────┬────┴─────┬─────┴────┬────┘
                   │          │          │
                   └──────────┼──────────┘
                              │
                     APPLICATION TRAFFIC
                              │
                              ▼
                    OBSERVABILITY STACK
             ┌────────────┬──────────────┐
             │ Prometheus │ OpenTelemetry│
             ├────────────┼──────────────┤
             │    Loki    │    Tempo     │
             └────────────┴──────────────┘
                              │
                              ▼
                      GRAFANA DASHBOARDS
                              │
                              ▼
                    ANOMALY DETECTION
                  Statistical Baselines
                              │
                              ▼
                   INCIDENT INVESTIGATOR
                Evidence Collection & Ranking
                              │
                              ▼
                            ORION
                  Incident Lifecycle Manager
                              │
                              ▼
                    DETERMINISTIC PLANNER
                     Guarded Repair Plan
                              │
                              ▼
                     POLICY & VALIDATION
                              │
                              ▼
                     KUBERNETES STAGING
                              │
                              ▼
                      HUMAN APPROVAL
                              │
                              ▼
                       CANARY EXECUTION
                         /         \
                        /           \
                   HEALTHY        UNHEALTHY
                      │               │
                      ▼               ▼
                   CONTINUE        ROLLBACK
                      │               │
                      └───────┬───────┘
                              ▼
                       AUDIT TIMELINE
```

---

## 📸 Dashboard Screenshots

### 01. Service Overview Dashboard

Grafana shows service health, request rates, HTTP 5xx responses, and average request latency across the distributed application.

![SentinelSRE Service Overview Dashboard](Screenshots/Screenshot%202026-11-21%20125934.png)

### 02. HTTP 5xx Responses and Average Latency

The dashboard highlights service errors and latency changes while fault activity is visible.

![SentinelSRE HTTP Errors and Latency](Screenshots/Screenshot%202026-11-21%20125950.png)

### 03. Inventory Service Injected Error Logs

This view shows injected inventory errors captured by Grafana from Loki.

![SentinelSRE Inventory Error Logs](Screenshots/Screenshot%202026-11-21%20124343.png)

### 04. Distributed Trace and Error Count

The Grafana panel shows an order-service trace and the number of service error-log entries in the selected time range.

![SentinelSRE Trace and Error Count](Screenshots/Screenshot%202026-11-21%20143613.png)

---

## 🔬 Incident Investigation

One of Faultweaver's central capabilities is its ability to reproduce and investigate failures using live application telemetry.

### Example: Inventory Service Failure

A typical investigation follows this sequence:

**Step 1 — Generate normal application traffic**

Orders are submitted through the Order API to establish normal service behavior.

**Step 2 — Establish a statistical baseline**

Recent service measurements are collected and used to establish expected latency behavior.

**Step 3 — Inject a controlled fault**

The fault-injection mechanism introduces artificial latency or error responses in the Inventory Service.

**Step 4 — Observe the impact**

Prometheus captures changes in metrics, Loki collects service logs, and Tempo stores distributed traces.

**Step 5 — Investigate the incident**

The Investigator API analyzes the available evidence and produces a structured report with ranked service leads and uncertainties.

**Step 6 — Evaluate recovery**

ORION receives the incident, records its lifecycle, generates a deterministic repair proposal, and evaluates it through the applicable policy and validation controls.

**Step 7 — Validate and execute safely**

Eligible actions undergo Kubernetes staging validation and explicit approval before canary execution. Health monitoring determines whether to continue or roll back.

### Example Error Log

```text
Service: inventory-service
Level: ERROR
Function: raise_injected_error

Injected inventory error for item_id=itemB
Injected inventory error for item_id=itemA
Injected inventory error for item_id=itemB
```

These entries illustrate how controlled failures generate observable evidence that can be correlated with application metrics and traces.

---

## 🛠️ Technology Stack

| Category | Technologies |
|---|---|
| Language | Python |
| Backend Framework | FastAPI |
| Microservices | Order, Inventory, Payment |
| Database | PostgreSQL |
| Authentication | Keycloak, OpenID Connect |
| Containerization | Docker, Docker Compose |
| Metrics Monitoring | Prometheus |
| Instrumentation | OpenTelemetry |
| Centralized Logging | Grafana Loki |
| Distributed Tracing | Grafana Tempo |
| Visualization | Grafana |
| Orchestration | Kubernetes |
| Automated Testing | Pytest |

---

## 🚀 Installation

### Prerequisites

Make sure the following tools are installed:

- Docker
- Docker Compose
- Git
- Python 3.11 or later

### 1. Clone the Repository

```bash
git clone <YOUR_REPOSITORY_URL>
cd Faultweaver
```

### 2. Start the Platform

```bash
docker compose up --build -d
```

### 3. Verify Running Containers

```bash
docker compose ps
```

### 4. Inspect Application Logs

```bash
docker compose logs -f
```

### 5. Run Tests

Execute the project's configured test command in the appropriate Python environment:

```bash
pytest
```

**Configuration:** Set the required environment variables and service-specific configuration according to the repository's setup instructions.

---

## ⚙️ Usage

The typical demonstration workflow is:

1. Start the Docker Compose environment.
2. Verify that all application and observability services are running.
3. Generate application traffic through the Order Service.
4. Allow normal measurements to establish a baseline.
5. Introduce a controlled Inventory Service failure.
6. Inspect metrics, logs, and distributed traces in Grafana.
7. Submit an incident investigation request.
8. Review the structured investigation report.
9. Inspect ORION's incident lifecycle and proposed recovery plan.
10. Validate eligible remediation actions in Kubernetes staging.
11. Complete the required approval before canary execution.
12. Observe health checks and rollback behavior.

---

## 🧪 Testing & Evaluation

Faultweaver includes automated tests and controlled evaluation scenarios for its investigation and remediation-control workflows.

**Recorded verification: 35 passing tests.**

Testing and evaluation cover:

- Statistical anomaly detection.
- Incident investigation behavior.
- Evidence and uncertainty handling.
- Incident deduplication.
- Policy enforcement.
- Remediation workflow decisions.
- Safety checks and failure handling.

The project emphasizes repeatable fault scenarios and inspectable investigation outcomes.

---

## 🔐 Safety & Reliability

Faultweaver follows a safety-first approach to infrastructure changes.

- **Fail-closed policies:** Unvalidated actions are blocked.
- **Explicit approval:** Sensitive remediation actions require authorization.
- **Evidence-driven decisions:** Investigation reports expose supporting evidence and uncertainty.
- **Incident deduplication:** Repeated alerts do not needlessly create duplicate incident workflows.
- **Audit history:** Important incident and control-plane events are recorded.
- **Canary monitoring:** Controlled execution is evaluated against defined health conditions.
- **Rollback:** Unsuccessful canary outcomes trigger the configured rollback workflow.

---

## ⚠️ Project Scope & Limitations

Faultweaver is a local-first experimental and demonstration platform, not a production deployment connected to real customer infrastructure.

Its anomaly detection is statistical and baseline-driven, not based on a trained machine-learning model. Investigation reports provide evidence-backed diagnostic leads, not guaranteed root-cause conclusions.

Kubernetes remediation is governed by staging validation, policy, and approval controls. The system should not be interpreted as an unrestricted autonomous production-healing platform.

The project demonstrates an integrated SRE workflow in a controlled environment, with a focus on observability, explainability, reproducibility, and safe infrastructure automation.

---

## 🔮 Future Enhancements

- Advanced causal dependency analysis.
- More complex multi-service and compound-fault benchmarks.
- Adaptive statistical baselines.
- Expanded Kubernetes recovery scenarios.
- Deeper security and adversarial testing.
- Longer-duration reliability and performance evaluation.
- Improved recovery strategy selection.

---

## 👩‍💻 Author

**Kruthi G. R.**

---

<p align="center">
  <h3>🛡️ Faultweaver</h3>
  <b>Trace the cause. Weave the recovery.</b>
  <br>
  <i>From distributed failures to evidence-driven, controlled recovery.</i>
</p>

---

<p align="center">
  ⭐ If you find this project interesting, consider starring the repository!
</p>