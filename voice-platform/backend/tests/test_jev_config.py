import os
from app.config import get_config, reload_config

def test_jev_config_loading(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test_ts_key_999")
    monkeypatch.setenv("JEV_MODEL", "jev-1.13.0")
    
    cfg = reload_config()
    assert hasattr(cfg, "jev")
    assert cfg.jev.typesafe_api_key == "test_ts_key_999"
    assert cfg.jev.jev_model == "jev-1.13.0"
    assert cfg.jev.enabled is True
