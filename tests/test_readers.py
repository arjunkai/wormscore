import importlib.metadata as md
import struct
import sys

import numpy as np

from wormscore import config as C
from wormscore import readers


def _utf16(text):
    return text.encode("utf-16-le")


def _series_xml(name, block, shape, pixel_um, z_um):
    """XML for one 2-channel series: a GFP channel (488 nm, HyD) and a transmitted-light channel."""
    nz, ny, nx = shape
    plane = 2 * nx * ny
    return f"""
<Element Name="{name}"><Data><Image>
 <ImageDescription>
  <Channels>
   <ChannelDescription DataType="0" ChannelTag="0" Resolution="12" NameOfMeasuredQuantity="" Min="0" Max="4095" Unit="" IsLUTInverted="0" BitInc="0" LUTName="Green" BytesInc="0"/>
   <ChannelDescription DataType="0" ChannelTag="0" Resolution="12" NameOfMeasuredQuantity="" Min="0" Max="4095" Unit="" IsLUTInverted="0" BitInc="0" LUTName="Gray" BytesInc="{plane}"/>
  </Channels>
  <Dimensions>
   <DimensionDescription DimID="1" Unit="m" BitInc="0" Origin="0" NumberOfElements="{nx}" Length="{pixel_um * (nx - 1) * 1e-6}" BytesInc="2"/>
   <DimensionDescription DimID="2" Unit="m" BitInc="0" Origin="0" NumberOfElements="{ny}" Length="{pixel_um * (ny - 1) * 1e-6}" BytesInc="{2 * nx}"/>
   <DimensionDescription DimID="3" Unit="m" BitInc="0" Origin="0" NumberOfElements="{nz}" Length="{z_um * (nz - 1) * 1e-6}" BytesInc="{2 * plane}"/>
  </Dimensions>
 </ImageDescription>
 <Attachment Name="HardwareSetting" SystemTypeName="TCS TEST">
  <ATLConfocalSettingDefinition ObjectiveName="HC PL APO 63x/1.40 OIL" Zoom="2" StagePosX="0.001" StagePosY="0.002" LineAverage="3">
   <Spectro><MultiBand Channel="1" LeftWorld="500" RightWorld="550"/></Spectro>
   <AotfList><Aotf><LaserLineSetting LaserLine="488" IntensityDev="20"/><LaserLineSetting LaserLine="561" IntensityDev="0"/></Aotf></AotfList>
   <DetectorList>
    <Detector Name="HyD 1" Channel="1" Gain="100" IsActive="1"/>
    <Detector Name="Trans PMT" Channel="2" Gain="200" IsActive="1"/>
    <Detector Name="HyD 3" Channel="3" Gain="0" IsActive="0"/>
   </DetectorList>
  </ATLConfocalSettingDefinition>
 </Attachment>
</Image></Data><Memory Size="{shape[0] * 2 * plane}" MemoryBlockID="{block}"/><Children/></Element>"""


def write_lif(path, stacks):
    """A minimal Leica .lif with one series per (name, (gfp, bf)) pair, both uint16 (z, y, x).
    A name with a slash is put inside a folder of that name."""
    series, blocks, folders = [], [], {}
    for k, (name, (gfp, bf)) in enumerate(stacks):
        block = f"MemBlock_{k + 1}"
        data = np.stack([gfp, bf], axis=1).astype("<u2")      # z, c, y, x
        blocks.append((block, data.tobytes()))
        folder, _, leaf = name.rpartition("/")
        xml = _series_xml(leaf, block, gfp.shape, 0.25, 0.8)
        (folders.setdefault(folder, []) if folder else series).append(xml)
    for folder, children in folders.items():
        series.append(f'<Element Name="{folder}"><Data/><Memory Size="0" MemoryBlockID="MemBlock_0"/>'
                      f'<Children>{"".join(children)}</Children></Element>')
    xml = (f'<LMSDataContainerHeader Version="2"><Element Name="{path.stem}"><Data/>'
           f'<Memory Size="0" MemoryBlockID="MemBlock_0"/><Children>{"".join(series)}</Children>'
           f"</Element></LMSDataContainerHeader>")
    with open(path, "wb") as fh:
        fh.write(struct.pack("<IIBI", 0x70, 2 * len(xml) + 5, 0x2A, len(xml)) + _utf16(xml))
        for block, data in [("MemBlock_0", b"")] + blocks:
            fh.write(struct.pack("<IIBQBI", 0x70, 0, 0x2A, len(data), 0x2A, len(block)) + _utf16(block) + data)


def test_leica_lif_series_roles_settings_and_pixels(tmp_path):
    rng = np.random.default_rng(0)
    stacks = [(name, (rng.integers(0, 4096, (4, 6, 8)), rng.integers(0, 4096, (4, 6, 8))))
              for name in ("Series001", "Series002", "Position 1/Series003")]
    path = tmp_path / "test.lif"
    write_lif(path, stacks)
    images = readers.leica_images(path, C.DEFAULTS)

    # Series numbers and names are part of each image's identity in the key; they must stay stable.
    assert [im["location_in_file"] for im in images] == [
        "series 0 (Series001)", "series 1 (Series002)", "series 2 (Position 1/Series003)"]
    im = images[0]
    assert im["channel_roles"] == ["MARKER", "BF"] and im["channel_names"] == ["Green", "Gray"]
    assert (im["n_z"], im["height"], im["width"], im["bits"]) == (4, 6, 8, 12)
    assert im["pixel_um"] == 0.25 and im["z_step_um"] == 0.8
    assert im["settings"] == "HyD gain 100.0; 488nm@20.0%; line avg 3"
    assert im["system"] == "Leica TCS TEST" and im["objective"] == "HC PL APO 63x/1.40 OIL"
    assert round(im["stage_x"]) == 1000 and round(im["stage_y"]) == 2000
    assert images[2]["channel_roles"] == ["MARKER", "BF"]       # inside a folder: still resolved
    for im, (_, (gfp, bf)) in zip(images, stacks):
        assert np.array_equal(readers.read(im, 0), gfp)
        assert np.array_equal(readers.read(im, 1), bf)


def test_gpl_leica_reader_is_not_used():
    """readlif is GPL-3; the MIT-licensed Windows app bundles every dependency, so it must not be one."""
    import wormscore.pipeline  # noqa: F401
    assert "readlif" not in sys.modules
    assert not any(r.lower().startswith("readlif") for r in md.requires("wormscore"))
