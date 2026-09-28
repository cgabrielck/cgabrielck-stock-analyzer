import pytest
from agents.fundamental_analyzer import calculate_growth_score, calculate_all_scores


def test_score_all_metrics_perfect() -> None:
    data = {
        "ticker": "TEST",
        "revenue_growth": 20.0,
        "eps_growth": 20.0,
        "profit_margin": 20.0,
        "peg": 0.5,
        "roe": 30.0,
        "debt_equity": 0.3,
        "sector": "Technology",
    }
    score, details, _ = calculate_growth_score(data)
    assert score == 100.0
    assert len(details) == 6


def test_score_missing_metrics() -> None:
    data = {
        "ticker": "TEST",
        "revenue_growth": None,
        "eps_growth": None,
        "profit_margin": None,
        "peg": None,
        "roe": None,
        "debt_equity": None,
        "sector": "Technology",
    }
    score, details, _ = calculate_growth_score(data)
    assert score == 0
    assert len(details) == 0


def test_score_partial_metrics() -> None:
    data = {
        "ticker": "TEST",
        "revenue_growth": 10.0,
        "eps_growth": None,
        "profit_margin": None,
        "peg": None,
        "roe": None,
        "debt_equity": None,
        "sector": "Technology",
    }
    score, details, _ = calculate_growth_score(data)
    assert 0 < score < 100
    assert "营收增长 (YoY)" in details


def test_zero_growth_is_valid_and_counts_against_score() -> None:
    data = {
        "ticker": "TEST", "revenue_growth": 0.0, "eps_growth": 0.0,
        "profit_margin": None, "peg": None, "roe": None, "debt_equity": None,
    }

    score, details, metrics_used = calculate_growth_score(data)

    assert score == 0
    assert metrics_used == 2
    assert details["营收增长 (YoY)"]["score"] == "0/100"
    assert details["EPS增长 (YoY)"]["score"] == "0/100"


def test_peg_scoring() -> None:
    data = {
        "ticker": "TEST",
        "revenue_growth": None,
        "eps_growth": None,
        "profit_margin": None,
        "peg": 0.3,
        "roe": None,
        "debt_equity": None,
        "sector": "Technology",
    }
    score, details, _ = calculate_growth_score(data)
    assert "PEG比率" in details
    assert details["PEG比率"]["score"] == "100/100"


def test_debt_equity_scoring() -> None:
    data = {
        "ticker": "TEST",
        "revenue_growth": None,
        "eps_growth": None,
        "profit_margin": None,
        "peg": None,
        "roe": None,
        "debt_equity": 0.1,
        "sector": "Technology",
    }
    score, details, _ = calculate_growth_score(data)
    assert "负债/权益比" in details
    assert details["负债/权益比"]["score"] == "100/100"


def test_debt_equity_high() -> None:
    data = {
        "ticker": "TEST",
        "revenue_growth": None,
        "eps_growth": None,
        "profit_margin": None,
        "peg": None,
        "roe": None,
        "debt_equity": 5.0,
        "sector": "Technology",
    }
    score, details, _ = calculate_growth_score(data)
    assert "负债/权益比" in details
    assert details["负债/权益比"]["score"] == "10/100"


def test_calculate_all_scores_sorts_correctly() -> None:
    all_data = {
        "A": {"ticker": "A", "revenue_growth": 10.0, "sector": "Tech"},
        "B": {"ticker": "B", "revenue_growth": 40.0, "sector": "Tech"},
        "C": {"ticker": "C", "revenue_growth": 5.0, "sector": "Tech"},
    }
    scored = calculate_all_scores(all_data)
    assert len(scored) == 3
    assert scored[0]["ticker"] == "B"
    assert scored[1]["ticker"] == "A"
    assert scored[2]["ticker"] == "C"


def test_calculate_all_scores_skips_errors() -> None:
    all_data = {
        "A": {"ticker": "A", "revenue_growth": 10.0, "sector": "Tech"},
        "B": {"ticker": "B", "error": "Something broke"},
    }
    scored = calculate_all_scores(all_data)
    assert len(scored) == 1


def test_calculate_all_scores_assigns_other_to_missing_sector() -> None:
    all_data = {
        "A": {"ticker": "A", "revenue_growth": 10.0, "sector": None},
        "B": {"ticker": "B", "revenue_growth": 10.0, "sector": "Tech"},
    }
    scored = calculate_all_scores(all_data)
    assert len(scored) == 2
    assert next(stock for stock in scored if stock["ticker"] == "A")["sector"] == "Other"


def test_quality_split_columns_on_full_metrics() -> None:
    data = {
        "ticker": "TEST",
        "revenue_growth": 20.0,
        "eps_growth": 20.0,
        "profit_margin": 20.0,
        "peg": 0.5,
        "roe": 30.0,
        "debt_equity": 0.3,
        "sector": "Technology",
    }
    from agents.fundamental_analyzer import quality_attribution, score_fundamentals

    result = score_fundamentals(data)
    assert result["growth_score"] == 100.0
    assert result["quality_score"] == 100.0
    assert result["quality_roe_score"] == 100.0
    assert result["quality_margin_score"] == 100.0
    assert result["quality_leverage_score"] == 100.0
    assert result["growth_component_score"] == 100.0
    assert result["value_component_score"] == 100.0
    shares = result["score_attribution"]
    assert abs(
        (shares["composite_share_quality"] or 0)
        + (shares["composite_share_growth"] or 0)
        + (shares["composite_share_value"] or 0)
        - 1.0
    ) < 1e-6
    attr = quality_attribution(data)
    assert attr["quality_roe_score"] == 100.0


def test_quality_only_metrics_match_composite() -> None:
    data = {
        "ticker": "TEST",
        "revenue_growth": None,
        "eps_growth": None,
        "profit_margin": 20.0,
        "peg": None,
        "roe": 30.0,
        "debt_equity": 0.3,
    }
    from agents.fundamental_analyzer import score_fundamentals

    result = score_fundamentals(data)
    assert result["growth_score"] == 100.0
    assert result["quality_score"] == 100.0
    assert result["growth_component_score"] is None
    assert result["value_component_score"] is None


def test_calculate_all_scores_attaches_quality_columns() -> None:
    all_data = {
        "A": {"ticker": "A", "roe": 30.0, "profit_margin": 10.0, "debt_equity": 0.3, "sector": "Tech"},
    }
    scored = calculate_all_scores(all_data)
    row = scored[0]
    assert row["quality_roe_score"] == 100.0
    assert row["quality_margin_score"] == 50.0
    assert row["quality_leverage_score"] == 100.0
    assert row["quality_score"] is not None
    assert "score_attribution" in row
