import hashlib
import io
import json

import pytest

from wormscore import realdemo


def test_real_demo_project_from_cache(tmp_path):
    """No network: a fake fetch stands in for Zenodo. Checks the files, credit and config."""
    fetched = []

    def fake_fetch(name, md5, size, cache, log):
        cache.mkdir(parents=True, exist_ok=True)
        (cache / name).write_bytes(b"x")
        fetched.append(name)
        return cache / name

    project = realdemo.make_real_demo(tmp_path / "demo", log=lambda *a: None, fetch=fake_fetch,
                                      cache=tmp_path / "cache")
    assert sorted(fetched) == sorted(realdemo.FILES)
    assert all((project / "data" / n).exists() for n in realdemo.FILES)
    assert "CC BY 4.0" in (project / "data" / "ATTRIBUTION.txt").read_text(encoding="utf-8")
    cfg = json.loads((project / "config.json").read_text(encoding="utf-8"))
    rules = cfg["sources"][0]["genotypes"]
    assert all(any(n.startswith(r["match"].rstrip("*")) for r in rules) for n in realdemo.FILES)


def test_download_checks_md5_and_reuses_the_cache(tmp_path, monkeypatch):
    body = b"pretend image"
    good = hashlib.md5(body).hexdigest()
    calls = []

    def fake_urlopen(url, timeout):
        calls.append(url)
        return io.BytesIO(body)

    monkeypatch.setattr(realdemo.urllib.request, "urlopen", fake_urlopen)
    log = lambda *a: None
    with pytest.raises(RuntimeError, match="MD5"):
        realdemo.download("a b.czi", "0" * 32, len(body), tmp_path, log)
    assert not list(tmp_path.iterdir())                       # nothing half-written is kept
    path = realdemo.download("a b.czi", good, len(body), tmp_path, log)
    assert path.read_bytes() == body and "a%20b.czi" in calls[-1]
    realdemo.download("a b.czi", good, len(body), tmp_path, log)
    assert len(calls) == 2                                    # second call used the cache
