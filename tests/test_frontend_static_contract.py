"""Regression contracts for the canonical 3D frontend and its static assets."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[1]
FRONTEND = ROOT / "frontend"
HOME_HTML = FRONTEND / "home_assistant.html"
MAIN_SOURCE = ROOT / "src" / "main.py"

EXPECTED_FILES = {
    "HA.glb",
    "api.js",
    "home_assistant.html",
    "index.html",
    "login.html",
    "realestate_3d.html",
    "icon/ac.png",
    "icon/air_purrifier.png",
    "icon/camera.png",
    "icon/curtain.png",
    "icon/dish_washer.png",
    "icon/heater.png",
    "icon/light.png",
    "icon/lock.png",
    "icon/roomba.png",
    "icon/speaker.png",
    "icon/tophat.png",
    "icon/tv.png",
    "icon/window.png",
    "js/addons/controls/OrbitControls.js",
    "js/addons/loaders/DRACOLoader.js",
    "js/addons/loaders/GLTFLoader.js",
    "js/addons/utils/BufferGeometryUtils.js",
    "js/addons/utils/SkeletonUtils.js",
    "js/draco_decoder.js",
    "js/three-bundle.js",
    "js/three.core.js",
    "js/three.module.js",
}

LEGACY_FILES = {
    "app.js",
    "demo.html",
    "history.html",
    "home-standalone.html",
    "members.html",
    "profile.html",
    "prototype.html",
    "rooms.html",
    "styles.css",
}


def test_complete_3d_frontend_is_in_canonical_directory():
    actual_files = {
        path.relative_to(FRONTEND).as_posix()
        for path in FRONTEND.rglob("*")
        if path.is_file() and path.name != ".DS_Store"
    }

    missing_files = EXPECTED_FILES - actual_files
    assert not missing_files, f"Missing canonical frontend files: {sorted(missing_files)}"


def test_legacy_frontend_files_are_removed():
    assert not any((FRONTEND / relative_path).exists() for relative_path in LEGACY_FILES)
    assert not (ROOT / "mock").exists()


def test_application_serves_only_canonical_frontend():
    source = MAIN_SOURCE.read_text(encoding="utf-8")

    assert 'app.mount("/", NoCacheStaticFiles(directory="frontend", html=True), name="frontend")' in source
    assert 'app.mount("/mock"' not in source
    assert 'app.mount("/legacy"' not in source
    assert 'directory="mock"' not in source


def test_canonical_frontend_is_reachable_from_root():
    from fastapi.testclient import TestClient

    from src.main import app

    client = TestClient(app)

    assert client.get("/").status_code == 200
    assert client.get("/home_assistant.html").status_code == 200
    assert client.get("/js/three.module.js").status_code == 200
    assert client.get("/HA.glb").status_code == 200
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/mock/home_assistant.html").status_code == 404
    assert client.get("/legacy/index.html").status_code == 404


def test_3d_assets_resolve_from_frontend_root():
    source = HOME_HTML.read_text(encoding="utf-8")

    assert '"three": "/js/three.module.js"' in source
    assert '"three/addons/": "/js/addons/"' in source
    assert "dracoLoader.setDecoderPath('/js/');" in source
    assert "'/HA.glb'" in source
    assert "/mock/" not in source
