"""
eventstudy.estimator
--------------------
Difference-in-differences estimators for daily maritime traffic panels.

DESIGN NOTES (read before changing anything)
--------------------------------------------
1. Outcome is log1p(transits). Effects are therefore proportional, which is what
   we want: a 10-transit drop means something different at Bab el-Mandeb (~70/day)
   than at Magellan Strait (~4/day).

2. Daily maritime traffic is heavily autocorrelated. Naive OLS standard errors on
   a daily panel are badly understated. Two defences are implemented:
     - collapse to weekly means (reduces, does not eliminate, serial correlation)
     - randomization inference, which does not rely on the error structure at all

3. With ~15-25 chokepoints there are too few clusters for cluster-robust standard
   errors to be trustworthy. Randomization inference is the primary inference
   method here and cluster-robust SEs are reported only as a secondary reference.

4. Parallel trends is checked, reported, and never assumed. If the pre-period
   trend differential is large, the unit-specific-trend specification should be
   preferred and that choice must be stated in FINDINGS.md.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

RNG_SEED = 20260830


# --------------------------------------------------------------------------- #
# Panel construction
# --------------------------------------------------------------------------- #

def build_panel(
    df: pd.DataFrame,
    treated: list[str],
    controls: list[str],
    event_date: str,
    pre_days: int = 365,
    post_days: int = 365,
    unit_col: str = "portname",
    date_col: str = "date",
    value_col: str = "n_total",
    freq: str = "W",
    group_col: str | None = None,
) -> pd.DataFrame:
    """
    Build a balanced two-group panel around an event date.

    freq="W" collapses to weekly means (recommended). freq="D" keeps daily rows.

    group_col names a column holding a correlation cluster for each unit (country
    for ports, corridor for chokepoints). If supplied, randomization inference
    draws placebo treated sets from WITHIN a single cluster, which preserves the
    spatial correlation of a real treated group. Without it the placebo null is
    too narrow and p-values are anti-conservative.

    Returns columns: unit, group, rel, y, treated, post, did.
    """
    event = pd.Timestamp(event_date)
    lo, hi = event - pd.Timedelta(days=pre_days), event + pd.Timedelta(days=post_days)

    units = list(treated) + list(controls)
    overlap = set(treated) & set(controls)
    if overlap:
        raise ValueError(f"unit appears in both treated and control: {sorted(overlap)}")

    panel = df[df[unit_col].isin(units) & df[date_col].between(lo, hi)].copy()
    missing = set(units) - set(panel[unit_col].unique())
    if missing:
        raise ValueError(f"units absent from data: {sorted(missing)}")

    group_map = None
    if group_col:
        if group_col not in panel.columns:
            raise ValueError(f"group_col '{group_col}' not in data")
        group_map = panel.groupby(unit_col)[group_col].first().to_dict()

    panel["y_raw"] = panel[value_col].astype(float)
    panel["rel_days"] = (panel[date_col] - event).dt.days

    if freq == "W":
        # Week index relative to the event, so week 0 starts on the event date.
        panel["rel"] = np.floor(panel["rel_days"] / 7).astype(int)
        grouped = (
            panel.groupby([unit_col, "rel"], as_index=False)
            .agg(y_raw=("y_raw", "mean"), n_obs=("y_raw", "size"))
        )
        # Drop partial weeks at the panel edges to keep the panel balanced.
        grouped = grouped[grouped["n_obs"] == 7]
    else:
        grouped = panel.rename(columns={"rel_days": "rel"})[[unit_col, "rel", "y_raw"]].copy()

    grouped = grouped.rename(columns={unit_col: "unit"})
    grouped["y"] = np.log1p(grouped["y_raw"])
    grouped["treated"] = grouped["unit"].isin(treated).astype(int)
    grouped["post"] = (grouped["rel"] >= 0).astype(int)
    grouped["did"] = grouped["treated"] * grouped["post"]
    grouped["group"] = (
        grouped["unit"].map(group_map) if group_map else "_ungrouped"
    )

    # Enforce a balanced panel: keep only periods observed for every unit.
    counts = grouped.groupby("rel")["unit"].nunique()
    full = counts[counts == len(units)].index
    grouped = grouped[grouped["rel"].isin(full)].sort_values(["unit", "rel"])

    return grouped.reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Diagnostics
# --------------------------------------------------------------------------- #

@dataclass
class ParallelTrends:
    treated_slope: float
    control_slope: float
    differential: float
    p_value: float
    n_pre_periods: int

    def verdict(self, tol: float = 0.05) -> str:
        if self.p_value > 0.10 and abs(self.differential) < tol:
            return "PASS - pre-trends statistically and substantively similar"
        if self.p_value > 0.10:
            return "WEAK - not statistically distinguishable, but differential is large"
        return "FAIL - pre-trends differ; prefer the unit-trend specification"


def check_parallel_trends(panel: pd.DataFrame) -> ParallelTrends:
    """Regress y on time, treated, and their interaction, using pre-period only."""
    pre = panel[panel["post"] == 0].copy()
    if pre["rel"].nunique() < 4:
        raise ValueError("need at least 4 pre-periods to assess parallel trends")

    model = smf.ols("y ~ rel * treated", data=pre).fit()
    slope_c = model.params["rel"]
    diff = model.params["rel:treated"]

    scale = 52.0 if pre["rel"].max() - pre["rel"].min() < 200 else 365.0
    return ParallelTrends(
        treated_slope=(slope_c + diff) * scale,
        control_slope=slope_c * scale,
        differential=diff * scale,
        p_value=float(model.pvalues["rel:treated"]),
        n_pre_periods=int(pre["rel"].nunique()),
    )


# --------------------------------------------------------------------------- #
# Estimation
# --------------------------------------------------------------------------- #

@dataclass
class DiDResult:
    beta: float
    pct_effect: float
    se_cluster: float
    ci_low: float
    ci_high: float
    p_ri: float | None
    n_obs: int
    n_treated: int
    n_control: int
    spec: str
    placebo_betas: np.ndarray = field(default_factory=lambda: np.array([]), repr=False)

    def summary(self) -> str:
        pv = "n/a" if self.p_ri is None else f"{self.p_ri:.4f}"
        return (
            f"  spec              : {self.spec}\n"
            f"  beta (log points) : {self.beta:+.4f}\n"
            f"  implied effect    : {self.pct_effect:+.1f}%\n"
            f"  95% CI (bootstrap): [{self.ci_low:+.1f}%, {self.ci_high:+.1f}%]\n"
            f"  cluster SE        : {self.se_cluster:.4f}  (few clusters - reference only)\n"
            f"  randomization p   : {pv}\n"
            f"  panel             : {self.n_obs} obs, {self.n_treated} treated / "
            f"{self.n_control} control units"
        )


def _design(panel: pd.DataFrame, unit_trends: bool) -> tuple[pd.DataFrame, str]:
    """
    Build the estimation frame and formula.

    Unit-specific linear trends are added as explicit numeric columns with one
    unit held out as the reference. Writing them as `C(unit):rel` instead makes
    the design rank-deficient, because the average linear trend is already
    spanned by the period fixed effects. Holding one unit out normalises that.
    """
    formula = "y ~ did + C(unit) + C(rel)"
    if not unit_trends:
        return panel, formula

    d = panel.copy()
    units = sorted(d["unit"].unique())
    for i, u in enumerate(units[1:]):  # unit[0] is the held-out reference
        d[f"utrend_{i}"] = (d["unit"] == u).astype(float) * d["rel"]
    formula += " + " + " + ".join(f"utrend_{i}" for i in range(len(units) - 1))
    return d, formula


def _fit(panel: pd.DataFrame, unit_trends: bool):
    d, formula = _design(panel, unit_trends)
    return smf.ols(formula, data=d).fit(
        cov_type="cluster", cov_kwds={"groups": d["unit"]}
    )


def _fit_beta(panel: pd.DataFrame, unit_trends: bool) -> float | None:
    """Point estimate only, used inside resampling loops."""
    try:
        d, formula = _design(panel, unit_trends)
        return float(smf.ols(formula, data=d).fit().params["did"])
    except Exception:  # noqa: BLE001
        return None


def estimate_did(
    panel: pd.DataFrame,
    unit_trends: bool = False,
    n_permutations: int = 2000,
    n_bootstrap: int = 2000,
    seed: int = RNG_SEED,
) -> DiDResult:
    """
    Two-way fixed effects DiD with randomization inference and a block bootstrap CI.

    unit_trends=True adds unit-specific linear time trends, which is the correct
    fallback when the parallel-trends check fails.
    """
    model = _fit(panel, unit_trends)
    beta = float(model.params["did"])
    se = float(model.bse["did"])

    treated_units = sorted(panel.loc[panel["treated"] == 1, "unit"].unique())
    control_units = sorted(panel.loc[panel["treated"] == 0, "unit"].unique())
    k = len(treated_units)

    # --- Randomization inference -------------------------------------------- #
    # Reassign the treated label to every feasible combination of control units
    # and refit. The true beta's position in that distribution is an exact p-value
    # under the sharp null of no effect for any unit.
    rng = np.random.default_rng(seed)
    placebo: list[float] = []

    # Draw placebo treated sets from within one correlation cluster where the
    # panel supplies one. Drawing k unrelated units instead understates the
    # variance of a genuinely clustered treated group, which inflates the
    # rejection rate - measured at 22% against a nominal 5% on placebo dates.
    unit_group = panel.groupby("unit")["group"].first().to_dict() if "group" in panel else {}
    clusters: dict = {}
    for u in control_units:
        clusters.setdefault(unit_group.get(u, "_ungrouped"), []).append(u)
    eligible = [g for g, us in clusters.items() if len(us) >= k]
    grouped_ri = len(eligible) >= 2 and "_ungrouped" not in clusters

    if not grouped_ri and k > 1:
        warnings.warn(
            "randomization inference is drawing placebo units at random because no "
            "usable grouping was supplied. If the treated units are correlated with "
            "each other, the resulting p-value is anti-conservative. Pass group_col "
            "to build_panel.",
            stacklevel=2,
        )

    if len(control_units) > k:
        for _ in range(n_permutations):
            if grouped_ri:
                pool = clusters[eligible[rng.integers(len(eligible))]]
                fake = list(rng.choice(pool, size=k, replace=False))
            else:
                fake = list(rng.choice(control_units, size=k, replace=False))
            p2 = panel[panel["unit"].isin(control_units)].copy()
            p2["treated"] = p2["unit"].isin(fake).astype(int)
            p2["did"] = p2["treated"] * p2["post"]
            b = _fit_beta(p2, unit_trends)
            if b is not None:
                placebo.append(b)

    placebo_arr = np.asarray(placebo)
    p_ri = (
        float((np.abs(placebo_arr) >= abs(beta)).mean()) if placebo_arr.size >= 100 else None
    )

    # --- Block bootstrap over units ----------------------------------------- #
    # With very few treated units the resampling space is tiny (k=2 gives only
    # 4 distinct treated draws), so the resulting interval is coarse. Randomization
    # inference is the primary inference method in that case, not this CI.
    if k < 3:
        warnings.warn(
            f"only {k} treated unit(s): block bootstrap CI is coarse and should be "
            "reported alongside the randomization p-value, not instead of it",
            stacklevel=2,
        )
    boot: list[float] = []
    for _ in range(n_bootstrap):
        draw_t = rng.choice(treated_units, size=k, replace=True)
        draw_c = rng.choice(control_units, size=len(control_units), replace=True)
        frames = []
        for i, u in enumerate([*draw_t, *draw_c]):
            block = panel[panel["unit"] == u].copy()
            block["unit"] = f"{u}__{i}"  # keep resampled units distinct
            frames.append(block)
        bs = pd.concat(frames, ignore_index=True)
        b = _fit_beta(bs, unit_trends)   # must match the reported specification
        if b is not None:
            boot.append(b)

    if len(boot) >= 100:
        lo, hi = np.percentile(boot, [2.5, 97.5])
    else:
        lo, hi = beta - 1.96 * se, beta + 1.96 * se

    to_pct = lambda b: (np.exp(b) - 1) * 100  # noqa: E731

    return DiDResult(
        beta=beta,
        pct_effect=to_pct(beta),
        se_cluster=se,
        ci_low=to_pct(lo),
        ci_high=to_pct(hi),
        p_ri=p_ri,
        n_obs=len(panel),
        n_treated=k,
        n_control=len(control_units),
        spec=("TWFE + unit trends" if unit_trends else "TWFE")
             + (" | clustered RI" if grouped_ri else " | unclustered RI"),
        placebo_betas=placebo_arr,
    )


def event_study_path(panel: pd.DataFrame, ref_period: int = -1) -> pd.DataFrame:
    """
    Period-by-period treatment effects (leads and lags) relative to ref_period.
    Non-zero leads are evidence against parallel trends.
    """
    d = panel.copy()
    d["rel_f"] = d["rel"].astype(int)
    keep = sorted(d["rel_f"].unique())
    if ref_period not in keep:
        raise ValueError(f"ref_period {ref_period} not in panel")

    # patsy rejects '-' inside term names, so negative offsets are encoded as 'm'.
    # TWO reference periods must be omitted, not one. With unit fixed effects,
    # period fixed effects, and a full set of event-time indicators for the
    # treated group, those indicators sum to the treated dummy, which the unit
    # fixed effects already absorb. Dropping one period leaves the collinearity
    # in place: statsmodels falls back to a pseudo-inverse and the standard
    # errors come back NaN ("invalid value encountered in sqrt"). Convention is
    # to normalise on the period before the event plus the earliest lead.
    omit = {ref_period, min(keep)}
    if len(omit) < 2:
        omit = {ref_period, sorted(keep)[1]}

    tag = lambda r: f"lead_{'m' if r < 0 else 'p'}{abs(r)}"  # noqa: E731
    term_map = {tag(r): r for r in keep if r not in omit}

    # Build all dummies at once; inserting them one at a time fragments the frame.
    dummies = pd.DataFrame(
        {
            name: ((d["rel_f"] == r) & (d["treated"] == 1)).astype(int)
            for name, r in term_map.items()
        },
        index=d.index,
    )
    d = pd.concat([d, dummies], axis=1)

    terms = list(term_map)
    model = smf.ols(
        "y ~ " + " + ".join(terms) + " + C(unit) + C(rel_f)", data=d
    ).fit(cov_type="cluster", cov_kwds={"groups": d["unit"]})

    rows = []
    for t, r in term_map.items():
        b, se = model.params[t], model.bse[t]
        rows.append({
            "rel": r,
            "coef": b,
            "pct": (np.exp(b) - 1) * 100,
            "ci_low_pct": (np.exp(b - 1.96 * se) - 1) * 100,
            "ci_high_pct": (np.exp(b + 1.96 * se) - 1) * 100,
            "is_reference": False,
        })
    for r in omit:  # normalised to zero by construction
        rows.append({"rel": r, "coef": 0.0, "pct": 0.0,
                     "ci_low_pct": 0.0, "ci_high_pct": 0.0, "is_reference": True})
    return pd.DataFrame(rows).sort_values("rel").reset_index(drop=True)
