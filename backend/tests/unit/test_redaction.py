from shared_kernel.logging.redaction import redact


def test_redacts_authorization_key() -> None:
    out = redact({"Authorization": "Bearer abc.def.ghi"})
    assert out["Authorization"] == "***"


def test_redacts_nested_password() -> None:
    out = redact({"user": {"password": "hunter2", "name": "a"}})
    assert out["user"]["password"] == "***"
    assert out["user"]["name"] == "a"


def test_redacts_openai_like_secret_in_string() -> None:
    s = "config=sk-" + "a" * 45 + " end"
    out = redact({"blob": s})
    assert "sk-" + "a" * 45 not in out["blob"]
    assert "***" in out["blob"]


def test_redacts_anthropic_secret() -> None:
    out = redact({"blob": "key=sk-ant-" + "x" * 30})
    assert "sk-ant-" not in out["blob"]


def test_redacts_pem_block() -> None:
    pem = "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----"
    out = redact({"pem": pem})
    assert out["pem"] == "***"


def test_case_insensitive_keys() -> None:
    out = redact({"API_KEY": "x", "api-key": "y", "Api_Key": "z"})
    assert all(v == "***" for v in out.values())


def test_redacts_compound_key_names() -> None:
    out = redact(
        {
            "access_token": "a",
            "refresh_token": "b",
            "client_secret": "c",
            "x-api-key": "d",
            "proxy-authorization": "e",
        }
    )
    assert all(v == "***" for v in out.values())


def test_redacts_gemini_and_voyage_and_vault_values() -> None:
    out = redact(
        {
            "blob": "g=AIza" + "a" * 35 + " v=pa-" + "b" * 25 + " t=hvs." + "c" * 30,
        }
    )
    assert "AIza" not in out["blob"]
    assert "pa-" + "b" * 25 not in out["blob"]
    assert "hvs." not in out["blob"]


def test_depth_cap() -> None:
    nested: dict = {"a": {}}
    cur = nested["a"]
    for _ in range(20):
        cur["a"] = {}
        cur = cur["a"]
    out = redact(nested)
    assert out  # does not blow up
