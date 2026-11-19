"""Build blind set saves an image's planes several at a time (pipeline.encode_images). Every file it writes
must stay byte for byte what one file at a time wrote: the blind set's files are checksummed in
second-scorer packages, and an existing project must rebuild to the same bytes."""
import errno
import hashlib
import json
from pathlib import Path

import PIL
import pytest
from PIL import Image, features

from wormscore import pipeline
from wormscore.demo import make_demo
from wormscore.pipeline import inventory, prepare

quiet = lambda *a: None
STORED = json.loads((Path(__file__).parent / "blind_sha256_1.26.17.json").read_text())


def one_at_a_time(images, put):
    """What 1.26.17 did: each file made and saved in turn, on this thread."""
    return [put(name, Image.fromarray(make())) for name, make in images]


def build(tmp_path, name, identity, monkeypatch):
    p = tmp_path / name
    make_demo(p, n_per_group=2, identity=identity, log=quiet)
    inventory(p, log=quiet)
    monkeypatch.setattr(pipeline.secrets, "randbits", lambda n: 20261118)    # the codes the stored values used
    prepare(p, log=quiet)
    return {f.relative_to(p / "blind").as_posix(): f.read_bytes()
            for f in sorted((p / "blind").rglob("*")) if f.is_file()}


@pytest.mark.parametrize("kind", ("plain", "identity"))
def test_saving_together_writes_the_same_bytes_as_one_at_a_time(tmp_path, monkeypatch, kind):
    together = build(tmp_path, "together", kind == "identity", monkeypatch)
    monkeypatch.setattr(pipeline, "encode_images", one_at_a_time)
    alone = build(tmp_path, "alone", kind == "identity", monkeypatch)
    assert list(together) == list(alone)
    assert [n for n in together if together[n] != alone[n]] == []
    assert any("/identity/" in n for n in together) == (kind == "identity")


@pytest.mark.skipif((PIL.__version__, features.version("zlib")) != (STORED["pillow"], STORED["zlib"]),
                    reason="the stored values were taken with another Pillow or zlib, which may compress differently")
@pytest.mark.parametrize("kind", ("plain", "identity"))
def test_the_blind_set_is_byte_for_byte_what_1_26_17_wrote(tmp_path, monkeypatch, kind):
    got = build(tmp_path, kind, kind == "identity", monkeypatch)
    assert {n: hashlib.sha256(b).hexdigest() for n, b in got.items()} == STORED[kind]


def test_a_full_disk_while_saving_together_says_what_it_said_before(tmp_path, monkeypatch):
    save = Image.Image.save

    def full(im, fp, *a, **kw):
        if "z03" in str(fp):
            raise OSError(errno.ENOSPC, "No space left on device", str(fp))
        return save(im, fp, *a, **kw)
    said = []
    for how in ("together", "alone"):
        p = make_demo(tmp_path / how, n_per_group=1, log=quiet)
        inventory(p, log=quiet)
        with monkeypatch.context() as m:
            m.setattr(Image.Image, "save", full)
            if how == "alone":
                m.setattr(pipeline, "encode_images", one_at_a_time)
            with pytest.raises(SystemExit) as e:
                prepare(p, log=quiet)
        said.append(str(e.value).replace(str(p), "<project>"))
        assert not (p / "blind").exists() and not (p / pipeline.BUILDING).exists()
    assert said[0] == said[1] and "could not be written" in said[0]
