import pytest
from fastapi.testclient import TestClient

from beam_lab.api import app


def test_plan_and_api_validation():
    client = TestClient(app)
    config = client.get("/example").json()
    response = client.post("/plan", json=config)
    assert response.status_code == 200
    assert "span_length_mm" in response.text
    assert "Mu3" not in response.text
    config["etabs_executable"] = r"C:\other\ETABS.exe"
    assert client.post("/plan", json=config).status_code == 422


def test_unknown_download_and_cross_origin_submission():
    client = TestClient(app)
    assert client.get("/jobs/unknown/files/beams.csv").status_code == 404
    assert client.post("/jobs", json={}, headers={"origin": "https://unrelated.example"}).status_code == 403
