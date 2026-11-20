"""The model of a Leica TCS SP5 (1.26.21). LAS AF writes no SystemTypeName, so the microscope was "Leica".
The file names its model as the scanner's SystemType (ScannerSettingRecord Identifier="SystemType"
Variant="TCS SP5": S-BIAD1480, and Zenodo 13752242's Fig6c, written by LAS AF with the SP5's argon, 561 and
633 nm lines; Bio-Formats: Microscope Model="TCS SP5"). It is shown in Check the reading, Browse and the
Methods draft. The microscope's name as the project keys it (flat-field sessions, settings groups, results
tables) stays "Leica", so no session or group moves; under readers.as_before() it is not read at all."""
import numpy as np

from test_readers import HARDWARE, write_lif
from wormscore import checkreading as K
from wormscore import methods, readers

SP5 = HARDWARE.replace(' SystemTypeName="TCS TEST"', "").replace(
    "<ATLConfocalSettingDefinition", '<ScannerSetting><ScannerSettingRecord Identifier="SystemType" Unit="" '
    'Description="System Type" Data="0" Variant="TCS SP5" VariantType="8"/></ScannerSetting><ATLConfocalSettingDefinition', 1)


def lif(path, hardware):
    rng = np.random.default_rng(0)
    write_lif(path, [("Series001", (rng.integers(0, 4096, (4, 6, 8)), rng.integers(0, 4096, (4, 6, 8))))], hardware=hardware)
    return path


def test_an_sp5_file_names_its_model_and_keeps_its_microscope_name(tmp_path):
    cfg = K.default_settings()
    img = readers.leica_images(lif(tmp_path / "sp5.lif", SP5), cfg)[0]
    assert (img["system"], img["model"]) == ("Leica", "Leica TCS SP5")
    with readers.as_before():
        old = readers.leica_images(lif(tmp_path / "sp5b.lif", SP5), cfg)[0]
    assert old["system"] == "Leica" and "model" not in old
    named = readers.leica_images(lif(tmp_path / "sp8.lif", HARDWARE), cfg)[0]     # SystemTypeName: as before
    assert named["system"] == "Leica TCS TEST" and "model" not in named
    text = "\n".join(K.describe(img, cfg, tmp_path / "sp5.lif"))
    assert "Microscope        Leica TCS SP5" in text


def test_the_methods_table_names_the_model():
    key = [{"genotype": "wt", "system": "Leica", "objective": "63x", "source": "s", "local_path": "a", "location_in_file": "1"},
           {"genotype": "wt", "system": "Zeiss LSM 880", "objective": "63x", "source": "s", "local_path": "b", "location_in_file": "1"}]
    rows = methods.images_table(key, [], {}, {("a", "1"): "Leica TCS SP5"})
    assert "| wt | Leica TCS SP5 | 63x | s | 1 | 0 |" in rows and "| wt | Zeiss LSM 880 | 63x | s | 1 | 0 |" in rows
    assert methods.images_table(key, [], {}) == methods.images_table(key, [], {}, {})       # an inventory from before
    assert "| wt | Leica | 63x | s | 1 | 0 |" in methods.images_table(key, [], {})


def test_the_methods_table_names_the_included_images_models(tmp_path):
    """Found by review: the models of excluded images were named in the row too."""
    row = lambda path: {"genotype": "wt", "system": "Leica", "objective": "63x", "source": "s", "local_path": path,
                        "location_in_file": "1"}
    models = {("a", "1"): "Leica TCS SP5", ("b", "1"): "Leica TCS SP2"}
    assert "| wt | Leica TCS SP5 | 63x | s | 1 | 1 |" in methods.images_table([row("a")], [row("b")], {}, models)
    assert "| wt | Leica TCS SP2 | [see excluded.csv] | [source] | 0 | 1 |" in methods.images_table([], [row("b")], {}, models)


def test_the_models_come_from_inventory_csv(tmp_path):
    out = tmp_path / "outputs"
    out.mkdir()
    (out / "inventory.csv").write_text("local_path,location_in_file,model\na,1,Leica TCS SP5\nb,1,\n", encoding="utf-8")
    assert methods.microscope_models(tmp_path) == {("a", "1"): "Leica TCS SP5"}
    (out / "inventory.csv").write_text("local_path,location_in_file\na,1\n", encoding="utf-8")     # before 1.26.21
    assert methods.microscope_models(tmp_path) == {}
    assert methods.microscope_models(tmp_path / "none") == {}
