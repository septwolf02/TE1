from PIL import Image

from te.studio.branding import icon_resource


def test_packaged_icon_has_all_windows_sizes_and_opens_without_checkout_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sizes = {(n, n) for n in (16, 32, 48, 64, 128, 256)}
    with icon_resource().open("rb") as stream, Image.open(stream) as icon:
        assert icon.format == "ICO"
        assert icon.ico.sizes() == sizes
        for size in sizes:
            frame = icon.ico.getimage(size).convert("RGBA")
            assert frame.size == size
            assert frame.getchannel("A").getextrema() == (0, 255)
