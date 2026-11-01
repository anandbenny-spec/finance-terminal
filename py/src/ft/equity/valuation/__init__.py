"""The valuation engine (Spec 02A §5.7, §5.9): market-agnostic. Inputs and results in `model`, the cost of capital in
`capital`, the FCFF model, its default run and the scenario grid in `dcf`, the reverse DCF in `reverse` and a lender's
implied return on equity in `implied_roe`. Nothing here names a market, a currency or a source: the caller builds the
inputs (`ft/equity/assemble.py` for the listed companies)."""
from ft.equity.valuation.capital import Capital, NotApplicable, cost_of_capital
from ft.equity.valuation.dcf import Drivers, default_run, per_share, prepare, project, scenario_grid
from ft.equity.valuation.implied_roe import implied_roe
from ft.equity.valuation.model import (
    IMPLIED_ROE_VERSION,
    MODEL_VERSION,
    Cell,
    Company,
    Grid,
    Input,
    Lender,
    Market,
    Result,
    Run,
    Settings,
    Year,
)
from ft.equity.valuation.reverse import solve as reverse_dcf

__all__ = ["IMPLIED_ROE_VERSION", "MODEL_VERSION", "Capital", "Cell", "Company", "Drivers", "Grid", "Input", "Lender",
           "Market", "NotApplicable", "Result", "Run", "Settings", "Year", "cost_of_capital", "default_run",
           "implied_roe", "per_share", "prepare", "project", "reverse_dcf", "scenario_grid"]
