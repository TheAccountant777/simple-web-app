import httpx
import pytest
from typer.testing import CliRunner

from kenya_data_engine.cli import init as init_mod
from kenya_data_engine.cli.app import app
from kenya_data_engine.cli.init import write_env

MODELS = "https://api.deepseek.com/models"


@pytest.fixture
def cli(tmp_home, monkeypatch):
    monkeypatch.setenv("ENGINE_HOME", str(tmp_home.root))
    monkeypatch.setattr(init_mod, "run_doctor", lambda state, **kw: 0)  # no network
    return CliRunner()


def init(cli, *args):
    return cli.invoke(app, ["init", *args])


def test_init_non_interactive_writes_env_0600(cli, monkeypatch, tmp_home):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deep")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-x")
    r = init(cli, "--non-interactive", "--no-verify")
    assert r.exit_code == 0, r.output
    text = tmp_home.env_path.read_text()
    assert "DEEPSEEK_API_KEY=sk-deep" in text and "TAVILY_API_KEY=tvly-x" in text
    assert "SERPER" not in text
    assert oct(tmp_home.env_path.stat().st_mode & 0o777) == "0o600"
    assert tmp_home.config_path.exists() and tmp_home.calendar_path.exists()
    assert "sk-deep" not in r.output


def test_init_does_not_overwrite_config_without_force(cli, monkeypatch, tmp_home):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deep")
    tmp_home.config_path.write_text("top_n: 9\n")
    r = init(cli, "--non-interactive", "--no-verify")
    assert tmp_home.config_path.read_text() == "top_n: 9\n"
    assert "kept existing" in r.stdout
    r = init(cli, "--non-interactive", "--no-verify", "--force")
    assert "top_n: 5" in tmp_home.config_path.read_text() and "overwritten" in r.stdout


def test_init_preserves_unrelated_env_lines(cli, monkeypatch, tmp_home):
    tmp_home.env_path.write_text("# mine\nFOO=bar\nDEEPSEEK_API_KEY=old\n")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-new")
    r = init(cli, "--non-interactive", "--no-verify")
    assert r.exit_code == 0, r.output
    lines = tmp_home.env_path.read_text().splitlines()
    assert lines[:2] == ["# mine", "FOO=bar"] and "DEEPSEEK_API_KEY=sk-new" in lines
    assert "old" not in tmp_home.env_path.read_text()


def test_init_non_interactive_without_key_is_friendly(cli):
    r = init(cli, "--non-interactive", "--no-verify")
    assert r.exit_code == 2 and "DEEPSEEK_API_KEY is required" in r.stdout
    assert "Traceback" not in r.stdout


def test_init_non_interactive_keeps_saved_key(cli, tmp_home):
    tmp_home.env_path.write_text("DEEPSEEK_API_KEY=saved\n")
    assert init(cli, "--non-interactive", "--no-verify").exit_code == 0
    assert "DEEPSEEK_API_KEY=saved" in tmp_home.env_path.read_text()


def test_init_verifies_key_and_runs_doctor(cli, monkeypatch, respx_mock):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deep")
    respx_mock.get(MODELS).respond(200, json={"data": []})
    r = init(cli, "--non-interactive")
    assert r.exit_code == 0, r.output
    assert "Next: engine run" in r.stdout


def test_init_non_interactive_rejected_key_fails(cli, monkeypatch, respx_mock):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "bad")
    respx_mock.get(MODELS).respond(401)
    r = init(cli, "--non-interactive")
    assert r.exit_code == 2 and "rejected" in r.stdout and "--no-verify" in r.stdout


def test_init_exits_1_when_doctor_fails(cli, monkeypatch, respx_mock):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk")
    monkeypatch.setattr(init_mod, "run_doctor", lambda state, **kw: 1)
    respx_mock.get(MODELS).respond(200, json={})
    r = init(cli, "--non-interactive")
    assert r.exit_code == 1 and "Next: engine run" not in r.stdout


def answers(monkeypatch, *values):
    it = iter(values)
    monkeypatch.setattr(init_mod, "_ask", lambda label, req, has: next(it))


def test_init_interactive_retries_bad_key(cli, monkeypatch, respx_mock, tmp_home):
    answers(monkeypatch, "bad", "good", "tvly", "", "")
    respx_mock.get(MODELS).mock(
        side_effect=lambda req: httpx.Response(
            200 if "good" in req.headers["authorization"] else 401
        )
    )
    r = init(cli)
    assert r.exit_code == 0, r.output
    text = tmp_home.env_path.read_text()
    assert "DEEPSEEK_API_KEY=good" in text and "TAVILY_API_KEY=tvly" in text
    assert "rejected" in r.stdout and "try again (1/3)" in r.stdout


def test_init_interactive_gives_up_after_three(cli, monkeypatch, respx_mock):
    answers(monkeypatch, "a", "b", "c")
    respx_mock.get(MODELS).respond(401)
    r = init(cli)
    assert r.exit_code == 2 and "3 attempts" in r.stdout


def test_init_interactive_required_key_reprompts_when_empty(cli, monkeypatch, tmp_home):
    answers(monkeypatch, "", "sk-ok", "", "", "")
    r = init(cli, "--no-verify")
    assert r.exit_code == 0, r.output
    assert "is required" in r.stdout
    assert "DEEPSEEK_API_KEY=sk-ok" in tmp_home.env_path.read_text()


def test_init_interactive_enter_keeps_existing(cli, monkeypatch, tmp_home):
    tmp_home.env_path.write_text("DEEPSEEK_API_KEY=keep\nTAVILY_API_KEY=t1\n")
    answers(monkeypatch, "", "t2", "", "")
    assert init(cli, "--no-verify").exit_code == 0
    text = tmp_home.env_path.read_text()
    assert "DEEPSEEK_API_KEY=keep" in text and "TAVILY_API_KEY=t2" in text


def test_verify_deepseek_outcomes(respx_mock):
    respx_mock.get(MODELS).respond(200)
    assert init_mod.verify_deepseek("https://api.deepseek.com/", "k")[0]
    respx_mock.get(MODELS).respond(500)
    assert "HTTP 500" in init_mod.verify_deepseek("https://api.deepseek.com", "k")[1]
    respx_mock.get(MODELS).mock(side_effect=httpx.ConnectError("x"))
    assert "could not reach" in init_mod.verify_deepseek("https://api.deepseek.com", "k")[1]


def test_write_env_quotes_special_values_and_fixes_perms(tmp_path):
    p = tmp_path / ".env"
    p.write_text("A=1\n")
    p.chmod(0o644)
    write_env(p, {"B": 'has space"q', "C": "plain-1"})
    assert p.read_text().splitlines() == ["A=1", 'B="has space\\"q"', "C=plain-1"]
    assert oct(p.stat().st_mode & 0o777) == "0o600"
