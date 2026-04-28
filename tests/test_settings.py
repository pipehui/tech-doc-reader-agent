from tech_doc_agent.app.core.settings import Settings


def test_settings_parses_typed_values():
    settings = Settings(
        TAVILY_DAILY_LIMIT="7",
        ALLOWED_ORIGINS="http://127.0.0.1:5173,http://localhost:5173",
    )

    assert settings.TAVILY_DAILY_LIMIT == 7
    assert settings.ALLOWED_ORIGINS == [
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ]


def test_settings_uses_project_data_path_by_default():
    settings = Settings()

    assert settings.DATA_PATH == "./tech_doc_agent/data"
