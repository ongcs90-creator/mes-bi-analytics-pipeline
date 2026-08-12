import os
import pandas as pd
import numpy as np
from datetime import datetime, date

# Cache dictionary to hold the loaded DataFrames in memory
_CACHE = {}
_CACHE_MTIMES = {}

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(BASE_DIR, "data")

# Machine definitions for target machine classification
_TARGET_MACHINES = {
    ("名張",  "3A"), ("名張",  "3B"), ("名張",  "8A"), ("名張",  "8B"),
    ("柏原",  "5A"), ("柏原",  "5B"),
    ("埼玉",  "1A"), ("埼玉",  "1B"), ("埼玉",  "3A"),
    ("竜ケ崎", "4B"),
    ("筑波",  "L01"),
}

def _is_target_machine(factory: str, machine: str) -> str:
    machine = str(machine)
    for fac, suffix in _TARGET_MACHINES:
        if fac == factory and suffix in machine:
            return "Y"
    return "N"

def _is_kakouki(machine: str) -> str:
    m = str(machine)
    if any(ch in m for ch in ("S", "K", "M")):
        return "N"
    return "Y"

def _bin_maki(val: float) -> int:
    if val <= 1000.0:
        return 1
    elif val <= 2000.0:
        return 2
    elif val <= 3000.0:
        return 3
    elif val <= 4000.0:
        return 4
    elif val <= 5000.0:
        return 5
    elif val <= 6000.0:
        return 6
    elif val <= 7000.0:
        return 7
    elif val <= 8000.0:
        return 8
    elif val <= 9000.0:
        return 9
    elif val <= 10000.0:
        return 10
    else:
        return 11

_BIN_LABELS = {
    1:  "0-1000",
    2:  "1001-2000",
    3:  "2001-3000",
    4:  "3001-4000",
    5:  "4001-5000",
    6:  "5001-6000",
    7:  "6001-7000",
    8:  "7001-8000",
    9:  "8001-9000",
    10: "9001-10000",
    11: ">10000",
}

def enrich_jisseki(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "対象号機" not in df.columns:
        df["対象号機"] = df.apply(lambda r: _is_target_machine(str(r["工場"]), str(r["機台"])), axis=1)
    if "加工機" not in df.columns:
        df["加工機"] = df["機台"].apply(_is_kakouki)
    if "巻m" not in df.columns:
        投入本数 = pd.to_numeric(df["投入本数"], errors="coerce").replace(0, np.nan)
        df["巻m"] = pd.to_numeric(df["投入m"], errors="coerce") / 投入本数

    if "巻m(bin)" not in df.columns:
        df["巻m(bin)"] = df["巻m"].apply(lambda v: _bin_maki(v) if pd.notna(v) and v > 0 else np.nan)

    if "巻m(bin)_label" not in df.columns:
        def label(v):
            if pd.isna(v):
                return None
            return _BIN_LABELS.get(int(v), ">10000")
        df["巻m(bin)_label"] = df["巻m(bin)"].apply(label)

    df["所要時間[h]"] = pd.to_numeric(df["所要時間[h]"], errors="coerce").fillna(0)
    
    if "加工終了日時" in df.columns:
        df["加工終了日時"] = pd.to_datetime(df["加工終了日時"], errors="coerce")
        df["month_key"] = df["加工終了日時"].dt.to_period("M").astype(str).str.replace("-", "/")
    elif "加工終了月" in df.columns:
        df["month_key"] = df["加工終了月"].astype(str).str.strip()

    return df

def build_customer_machine_perf(df: pd.DataFrame) -> pd.DataFrame:
    grp_cols = ["month_key", "得意先", "工場", "加工機", "対象号機", "機台"]
    agg = (
        df.groupby(grp_cols, dropna=False)
        .agg(
            sum_仕上m=("仕上m", "sum"),
            sum_要検品m=("要検品m", "sum"),
            sum_所要時間h_L=("所要時間[h]", "sum"),
        )
        .reset_index()
    )
    agg.rename(columns={"sum_所要時間h_L": "sum_所要時間[h]_L"}, inplace=True)

    # Compute group-level productivity (ignoring 機台) to group by logical averages (like 加工機 and 対象号機)
    group_keys_no_mach = ["工場", "加工機", "対象号機", "得意先"]
    
    # Calculate group-level monthly productivity for each customer
    grp_monthly = agg.groupby(["month_key"] + group_keys_no_mach, dropna=False).agg(
        grp_m=("sum_仕上m", "sum"),
        grp_y=("sum_要検品m", "sum"),
        grp_h=("sum_所要時間[h]_L", "sum")
    ).reset_index()
    
    grp_monthly["group_productivity"] = np.where(
        grp_monthly["grp_h"] > 0,
        (grp_monthly["grp_m"] + grp_monthly["grp_y"]) / grp_monthly["grp_h"],
        0.0
    )
    
    # Shift to calculate previous month group-level productivity
    grp_monthly = grp_monthly.sort_values(group_keys_no_mach + ["month_key"]).reset_index(drop=True)
    grp_monthly["prev_group_productivity"] = grp_monthly.groupby(group_keys_no_mach)["group_productivity"].shift(1).fillna(0)
    
    # Merge group levels back into the machine-level aggregated dataframe
    agg = agg.merge(
        grp_monthly[["month_key"] + group_keys_no_mach + ["group_productivity", "prev_group_productivity"]],
        on=["month_key"] + group_keys_no_mach,
        how="left"
    )
    
    # Override productivity values with group-level averages to align comparisons
    agg["今月の生産性"] = agg["group_productivity"]
    agg["前月の生産性"] = agg["prev_group_productivity"]

    total_m = agg["sum_仕上m"] + agg["sum_要検品m"]
    agg["期待工数"] = np.where(agg["前月の生産性"] > 0, total_m / agg["前月の生産性"], np.nan)
    agg["ギャップ"] = agg["期待工数"] - agg["sum_所要時間[h]_L"]
    return agg

def build_factory_target_perf(df: pd.DataFrame) -> pd.DataFrame:
    grp_cols = ["month_key", "工場", "対象号機"]
    agg = (
        df.groupby(grp_cols, dropna=False)
        .agg(
            sum_仕上m=("仕上m", "sum"),
            sum_要検品m=("要検品m", "sum"),
            sum_所要時間h_R=("所要時間[h]", "sum"),
        )
        .reset_index()
    )
    agg.rename(columns={"sum_所要時間h_R": "sum_所要時間[h]_R"}, inplace=True)
    agg["工場生産性"] = np.where(agg["sum_所要時間[h]_R"] > 0, (agg["sum_仕上m"] + agg["sum_要検品m"]) / agg["sum_所要時間[h]_R"], 0.0)
    agg["工場_対象号機"] = agg["工場"] + agg["対象号機"]

    sort_key = ["工場_対象号機", "month_key"]
    agg = agg.sort_values(sort_key).reset_index(drop=True)
    agg["工場生産性_先月"] = agg.groupby("工場_対象号機")["工場生産性"].shift(1).fillna(0)
    return agg

def build_target_variance_analysis(customer_machine_perf: pd.DataFrame, factory_target_perf: pd.DataFrame) -> pd.DataFrame:
    join_keys = ["工場", "month_key", "対象号機"]
    merged = customer_machine_perf.merge(
        factory_target_perf[join_keys + ["sum_所要時間[h]_R", "工場生産性", "工場_対象号機", "工場生産性_先月"]],
        on=join_keys,
        how="left",
    )
    ratio = merged["sum_所要時間[h]_L"] / merged["sum_所要時間[h]_R"].replace(0, np.nan)
    merged["能率差異"] = ratio * (merged["今月の生産性"] - merged["前月の生産性"])
    merged["構成差異"] = ratio * (merged["前月の生産性"] - merged["工場生産性_先月"])
    total_m = merged["sum_仕上m"] + merged["sum_要検品m"]
    merged["生産ギャップ"] = (total_m - merged["工場生産性_先月"] * merged["sum_所要時間[h]_L"]) / merged["sum_所要時間[h]_R"].replace(0, np.nan)
    return merged

def build_factory_category_perf(df: pd.DataFrame) -> pd.DataFrame:
    grp_cols = ["month_key", "工場", "加工機"]
    agg = (
        df.groupby(grp_cols, dropna=False)
        .agg(
            sum_仕上m=("仕上m", "sum"),
            sum_要検品m=("要検品m", "sum"),
            sum_所要時間h_R=("所要時間[h]", "sum"),
        )
        .reset_index()
    )
    agg.rename(columns={"sum_所要時間h_R": "sum_所要時間[h]_R"}, inplace=True)
    agg["工場生産性2"] = np.where(agg["sum_所要時間[h]_R"] > 0, (agg["sum_仕上m"] + agg["sum_要検品m"]) / agg["sum_所要時間[h]_R"], 0.0)
    agg["工場_加工機"] = agg["工場"] + agg["加工機"]

    sort_key = ["工場_加工機", "month_key"]
    agg = agg.sort_values(sort_key).reset_index(drop=True)
    agg["工場生産性_先月2"] = agg.groupby("工場_加工機")["工場生産性2"].shift(1).fillna(0)
    return agg

def build_category_variance_analysis(customer_machine_perf: pd.DataFrame, factory_category_perf: pd.DataFrame) -> pd.DataFrame:
    join_keys = ["工場", "month_key", "加工機"]
    merged = customer_machine_perf.merge(
        factory_category_perf[join_keys + ["sum_所要時間[h]_R", "工場生産性2", "工場_加工機", "工場生産性_先月2"]],
        on=join_keys,
        how="left",
    )
    ratio = merged["sum_所要時間[h]_L"] / merged["sum_所要時間[h]_R"].replace(0, np.nan)
    merged["能率差異"] = ratio * (merged["今月の生産性"] - merged["前月の生産性"])
    merged["構成差異"] = ratio * (merged["前月の生産性"] - merged["工場生産性_先月2"])
    total_m = merged["sum_仕上m"] + merged["sum_要検品m"]
    merged["生産ギャップ"] = (total_m - merged["工場生産性_先月2"] * merged["sum_所要時間[h]_L"]) / merged["sum_所要時間[h]_R"].replace(0, np.nan)
    return merged

def run_pipeline(raw_jisseki: pd.DataFrame) -> dict:
    enriched = enrich_jisseki(raw_jisseki)
    customer_machine_perf = build_customer_machine_perf(enriched)
    factory_target_perf = build_factory_target_perf(enriched)
    target_variance_analysis = build_target_variance_analysis(customer_machine_perf, factory_target_perf)
    factory_category_perf = build_factory_category_perf(enriched)
    category_variance_analysis = build_category_variance_analysis(customer_machine_perf, factory_category_perf)
    return {
        "enriched": enriched,
        "customer_machine_perf": customer_machine_perf,
        "factory_target_perf": factory_target_perf,
        "target_variance_analysis": target_variance_analysis,
        "factory_category_perf": factory_category_perf,
        "category_variance_analysis": category_variance_analysis
    }

def get_last_modified_time() -> str:
    path = os.path.join(DATA_DIR, "生産実績_全社.xlsx")
    try:
        mt = os.path.getmtime(path)
        return datetime.fromtimestamp(mt).strftime("%Y/%m/%d %H:%M")
    except Exception:
        return datetime.now().strftime("%Y/%m/%d %H:%M")

def load_excel_cached(file_path: str, sheet_name=0) -> pd.DataFrame:
    base, _ = os.path.splitext(file_path)
    sheet_suffix = f"_{sheet_name}" if sheet_name != 0 else ""
    cache_path = f"{base}{sheet_suffix}.pkl"
    
    if os.path.exists(cache_path) and os.path.exists(file_path):
        cache_mtime = os.path.getmtime(cache_path)
        src_mtime = os.path.getmtime(file_path)
        if cache_mtime > src_mtime:
            try:
                return pd.read_pickle(cache_path)
            except Exception as e:
                print(f"Failed to load cached pickle {cache_path}: {e}")
                
    df = pd.read_excel(file_path, sheet_name=sheet_name)
    try:
        df.to_pickle(cache_path)
    except Exception as e:
        print(f"Failed to save pickle cache to {cache_path}: {e}")
    return df

def load_data_and_run_pipeline(force_reload=False):
    global _CACHE, _CACHE_MTIMES
    
    file_names = [
        "生産実績_全社.xlsx",
        "生産実績_目標1.xlsx",
        "生産実績_2026出勤日.xlsx",
        "M_カレンダーマスタ_汎用版.csv",
    ]
    
    current_mtimes = {}
    reload_needed = force_reload
    
    for fn in file_names:
        p = os.path.join(DATA_DIR, fn)
        try:
            current_mtimes[fn] = os.path.getmtime(p)
        except Exception:
            current_mtimes[fn] = 0
            
    if not _CACHE:
        reload_needed = True
    else:
        for fn in file_names:
            if _CACHE_MTIMES.get(fn) != current_mtimes.get(fn):
                reload_needed = True
                break
                
    if not reload_needed:
        return _CACHE

    # Load production records (cached)
    path_jisseki = os.path.join(DATA_DIR, "生産実績_全社.xlsx")
    print(f"Loading production records from {path_jisseki}...")
    raw_jisseki = load_excel_cached(path_jisseki, sheet_name="生産実績")
    
    # Load targets and pivot (cached)
    path_target = os.path.join(DATA_DIR, "生産実績_目標1.xlsx")
    print(f"Loading targets from {path_target}...")
    raw_target = load_excel_cached(path_target)
    raw_target["日付"] = pd.to_datetime(raw_target["日付"], errors="coerce")
    raw_target = raw_target[raw_target["工場"] != "サンタック"].copy()
    
    # Pivot targets so col_map {"稼働率": "稼働率", "LSP": "LS/分", "段取時間件": "版替時間/件"} matches
    pivot_tgt = raw_target.pivot_table(
        index=["工場", "日付", "加工機", "対象号機"],
        columns="指標",
        values="目標値",
        aggfunc="mean"
    ).reset_index()
    
    # Rename columns to match what 04_管理_旧.py expects
    pivot_tgt = pivot_tgt.rename(columns={
        "版替時間/件": "版替時間/件",
        "稼働率": "稼働率",
        "LS/分": "LS/分"
    })

    # Load work days master (cached)
    path_workdays = os.path.join(DATA_DIR, "生産実績_2026出勤日.xlsx")
    print(f"Loading workdays from {path_workdays}...")
    raw_workdays = load_excel_cached(path_workdays)
    raw_workdays["日付"] = pd.to_datetime(raw_workdays["日付"], errors="coerce")

    # Load calendar master
    path_calendar = os.path.join(DATA_DIR, "M_カレンダーマスタ_汎用版.csv")
    print(f"Loading calendar from {path_calendar}...")
    raw_calendar = pd.read_csv(path_calendar)
    raw_calendar["日付"] = pd.to_datetime(raw_calendar["日付"], errors="coerce")

    # Load work time details from second sheet of production excel file (cached)
    print("Loading work time details from sheet '作業時間詳細'...")
    raw_work = load_excel_cached(path_jisseki, sheet_name="作業時間詳細")
    for col in ["開始時間", "終了日時", "終了日"]:
        if col in raw_work.columns:
            raw_work[col] = pd.to_datetime(raw_work[col], errors="coerce")

    pipeline = run_pipeline(raw_jisseki)

    _CACHE = {
        "raw_jisseki": raw_jisseki,
        "enriched": pipeline["enriched"],
        "customer_machine_perf": pipeline["customer_machine_perf"],
        "factory_target_perf": pipeline["factory_target_perf"],
        "target_variance_analysis": pipeline["target_variance_analysis"],
        "factory_category_perf": pipeline["factory_category_perf"],
        "category_variance_analysis": pipeline["category_variance_analysis"],
        "target_df": pivot_tgt,
        "workdays_df": raw_workdays,
        "calendar_df": raw_calendar,
        "work_df": raw_work,
        "last_updated": get_last_modified_time()
    }
    _CACHE_MTIMES = current_mtimes
    return _CACHE
