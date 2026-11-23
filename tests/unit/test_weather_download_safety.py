"""Safety helpers of `scripts/weather_download.py`: names, file filters, root check, redirects.

The script is imported by path (it is not a package). No network: redirects run through
`httpx.MockTransport`.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.unit

BS = chr(92)

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "weather_download.py"


@pytest.fixture(scope="module")
def wd():
    spec = importlib.util.spec_from_file_location("weather_download_under_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


@pytest.mark.parametrize(
    "name",
    [
        "collection_/../../app/main.py",
        "..",
        ".",
        "...",
        "a..b.csv",
        "x%2F",
        "a" + BS + "b.csv",
        "dir" + BS + ".." + BS + "x",
        "sub/file.csv",
        "",
        "name with space.csv",
    ],
    ids=[
        "traversal",
        "dotdot",
        "dot",
        "dots",
        "embedded-dotdot",
        "percent-encoded-slash",
        "backslash",
        "backslash-traversal",
        "slash",
        "empty",
        "space",
    ],
)
def test_safe_name_rejects_dangerous_names(wd, name: str) -> None:
    assert wd.safe_name(name) is False


@pytest.mark.parametrize(
    "name",
    [
        "RME_398245439RMD00090000000_______P1.TAB",
        "WE__0100___________CAL_ATS_________________P02.CSV",
        "WE__0100___________CAL_ATS_________________P02.xml",
        "collection_data_derived_env.xml",
        "collection_data_derived_env_inventory.csv",
        "sol_0100",
        "SOL00009",
        "SOL_00001_00089",
        "sol_0090_0179",
    ],
)
def test_safe_name_accepts_normal_names(wd, name: str) -> None:
    assert wd.safe_name(name) is True


def _source(wd, mission: str, subdir: str):
    return next(s for s in wd.SOURCES if s.mission == mission and s.subdir == subdir)


@pytest.mark.parametrize(
    ("mission", "subdir", "name", "kept"),
    [
        ("meda", "data_derived_env/", "WE__0100___DER_PS__P01.CSV", True),
        ("meda", "data_derived_env/", "WE__0100___DER_PS__P01.xml", True),
        ("meda", "data_derived_env/", "WE__0100___DER_PS__P01.CSV.part", False),
        ("meda", "data_derived_env/", "../x.csv.bak", False),
        ("meda", "data_calibrated_env/", "WE__0100___CAL_ATS__P02.CSV", True),
        ("meda", "data_calibrated_env/", "WE__0100___CAL_WS__P02.xml", True),
        ("meda", "data_calibrated_env/", "WE__0100___CAL_OTHER__P02.CSV", False),
        ("meda", "data_calibrated_env/", "collection_x.xml", True),
        ("meda", "data_calibrated_env/", "xcollection_x.xml", False),
        ("rems", "DATA/", "RME_398245439RMD00090000000_______P1.TAB", True),
        ("rems", "DATA/", "RME_398245439RMD00090000000_______P1.LBL", True),
        ("rems", "DATA/", "RME_398245439ADR00090000000_______P1.TAB", False),
        ("rems", "DATA/", "RME_398245439RMD00090000000_______P1.TAB.tmp", False),
        ("rems", "DATA/", "collection_rems.xml", True),
    ],
)
def test_file_filters_match_the_whole_name(
    wd, mission: str, subdir: str, name: str, kept: bool
) -> None:
    assert bool(_source(wd, mission, subdir).file_re.fullmatch(name)) is kept


def test_inside_root_accepts_paths_below_the_root(wd, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(wd, "OUT_ROOT", tmp_path / "out")
    (tmp_path / "out").mkdir()

    assert wd.inside_root(tmp_path / "out" / "meda" / "sol_0100" / "f.csv") is True


@pytest.mark.parametrize(
    "relative",
    ["../escape.csv", "meda/../../escape.csv", "../out-sibling/f.csv"],
    ids=["parent", "nested-parent", "sibling"],
)
def test_inside_root_rejects_paths_escaping_the_root(
    wd, monkeypatch, tmp_path: Path, relative: str
) -> None:
    monkeypatch.setattr(wd, "OUT_ROOT", tmp_path / "out")
    (tmp_path / "out").mkdir()

    assert wd.inside_root(tmp_path / "out" / relative) is False


def _client(wd, handler) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
        event_hooks={"response": [wd._check_host]},
    )


def test_redirect_to_another_host_is_refused(wd) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == wd.ALLOWED_HOST:
            return httpx.Response(302, headers={"Location": "https://evil.example.test/x.csv"})
        raise AssertionError("the redirect must not be followed")

    with pytest.raises(wd.RedirectRefused), _client(wd, handler) as client:
        client.get(f"https://{wd.ALLOWED_HOST}/PDS/a.csv")


def test_response_from_another_host_is_refused(wd) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="x")

    with pytest.raises(wd.RedirectRefused), _client(wd, handler) as client:
        client.get("https://evil.example.test/a.csv")


@pytest.mark.parametrize("location", ["/PDS/b.csv", "b.csv", "https://atmos.nmsu.edu/PDS/b.csv"])
def test_relative_and_same_host_redirects_are_followed(wd, location: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/PDS/a.csv":
            return httpx.Response(302, headers={"Location": location})
        return httpx.Response(200, text="ok")

    with _client(wd, handler) as client:
        response = client.get(f"https://{wd.ALLOWED_HOST}/PDS/a.csv")

    assert response.status_code == 200 and response.text == "ok"
    assert response.url.host == wd.ALLOWED_HOST
