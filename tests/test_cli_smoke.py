from typer.testing import CliRunner

from kenya_data_engine.cli.app import app


def test_version_flag():
    r = CliRunner().invoke(app, ["--version"])
    assert r.exit_code == 0
    assert "0.1.0" in r.stdout
