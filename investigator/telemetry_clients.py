import requests


class TelemetryClient:
    def __init__(self, prometheus_url: str, loki_url: str, tempo_url: str):
        self.prometheus_url = prometheus_url.rstrip('/')
        self.loki_url = loki_url.rstrip('/')
        self.tempo_url = tempo_url.rstrip('/')

    def query_prometheus(self, query: str, time: str = None):
        params = {"query": query}
        if time:
            params["time"] = time
        resp = requests.get(
            f"{self.prometheus_url}/api/v1/query",
            params=params,
            timeout=(2, 10),
        )
        resp.raise_for_status()
        return resp.json()

    def query_loki(self, query: str, start: str, end: str):
        params = {"query": query, "start": start, "end": end}
        resp = requests.get(
            f"{self.loki_url}/loki/api/v1/query_range",
            params=params,
            timeout=(2, 10),
        )
        resp.raise_for_status()
        return resp.json()

    def search_tempo(self, service: str, start: int, end: int):
        params = {
            "serviceName": service,
            "start": str(start),
            "end": str(end),
            "limit": "100",
        }
        resp = requests.get(
            f"{self.tempo_url}/api/search",
            params=params,
            timeout=(2, 10),
        )
        resp.raise_for_status()
        return resp.json()
