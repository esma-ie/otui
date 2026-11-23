"""Saving uploaded model files and finding the run file."""

import io
import zipfile

import pytest

from otui.uploads import UploadError, find_run_files, safe_name, save_uploads

RUN = b"$BATINCLUDE base.dd\n$ BATINCLUDE maindrv.mod mod\n"


def _zip(members: dict[str, bytes]) -> io.BytesIO:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    buf.seek(0)
    return buf


@pytest.mark.parametrize(
    "name, expected",
    [
        ("base.dd", "base.dd"),
        ("model/base.dd", "base.dd"),
        ("C:\\models\\demo\\demo12.run", "demo12.run"),
        ("../../etc/passwd", "passwd"),
    ],
)
def test_safe_name(name, expected):
    assert safe_name(name) == expected


@pytest.mark.parametrize("name", ["", "..", ".hidden"])
def test_safe_name_rejects(name):
    with pytest.raises(UploadError):
        safe_name(name)


def test_files_and_run_detection(tmp_path):
    paths = save_uploads(
        [("demo.run", io.BytesIO(RUN)), ("base.dd", io.BytesIO(b"SET X / a /;\n"))], tmp_path
    )
    assert [p.name for p in paths] == ["base.dd", "demo.run"]
    assert [p.name for p in find_run_files(tmp_path)] == ["demo.run"]


def test_zip_is_flattened_and_cannot_escape(tmp_path):
    z = _zip(
        {
            "model/demo.run": RUN,
            "model/dd/base.dd": b"x",
            "../../escape.dd": b"y",
            "__MACOSX/model/._base.dd": b"junk",
            "model/.DS_Store": b"junk",
        }
    )
    paths = save_uploads([("model.zip", z)], tmp_path / "files")
    # members under "..", hidden files and macOS metadata are skipped
    assert sorted(p.name for p in paths) == ["base.dd", "demo.run"]
    assert all(p.parent == tmp_path / "files" for p in paths)
    assert not (tmp_path / "escape.dd").exists()


def test_name_clash_is_an_error(tmp_path):
    z = _zip({"a/base.dd": b"1", "b/BASE.dd": b"2"})
    with pytest.raises(UploadError, match="two files are named"):
        save_uploads([("m.zip", z)], tmp_path)


def test_size_limit(tmp_path):
    from otui.uploads import Folder

    f = Folder(tmp_path, max_bytes=10)
    with pytest.raises(UploadError, match="larger than"):
        f.add("big.dd", io.BytesIO(b"x" * 11))


def test_bad_zip(tmp_path):
    with pytest.raises(UploadError, match="zip"):
        save_uploads([("m.zip", io.BytesIO(b"not a zip"))], tmp_path)
